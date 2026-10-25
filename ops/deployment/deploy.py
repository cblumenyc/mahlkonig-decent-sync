#!/usr/bin/python3 -I
"""Fixed Mahlkönig - Decent sync deployment authority. Install root-owned; never import a release.

No caller-selected paths, executables, environment or service names are accepted.
Application code is executed only by the restricted service or isolated checker.
"""
from contextlib import contextmanager, closing
import fcntl
import hashlib
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request

STATE = Path('/var/lib/coffee-sync-deploy')
RELEASES = Path('/opt/coffee-bridge/releases')
APP = Path('/opt/coffee-bridge/app')
CONF = Path('/etc/coffee-bridge/config.json')
INBOX = None  # Loaded from the root-owned deployment policy by the CLI.
ACTOR = 'operator'
POLICY = Path('/etc/coffee-bridge/deployment.json')
PYTHON = '/opt/coffee-bridge/venv/bin/python'
SERVICE = 'coffee-bridge.service'
WORKER = 'coffee-sync-deploy-worker.service'
SYSTEMCTL = '/usr/bin/systemctl'
MAX_ARCHIVE = 16*1024*1024
MAX_EXPANDED = 32*1024*1024
TERMINAL = {'complete','rolled_back','failed','deferred'}
ENV = {'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','HOME':'/root'}


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value):
        raise ValueError('Expected a SHA-256 release/operation identifier')
    return value


def run(*args, timeout=60, check=True):
    return subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,env=ENV,cwd='/',timeout=timeout,check=check)


def save(path,data):
    tmp=path.with_suffix('.tmp')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(data,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(d)
    finally:os.close(d)


def load(path,default=None):
    return json.loads(path.read_text()) if path.exists() else default


@contextmanager
def lock(name):
    with open(STATE/name,'a') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        yield


def seal(digest):
    identifier(digest)
    target=STATE/(digest+'.tar.gz')
    # Open inbox itself without following a user-replaced symlink, then use its FD.
    d=os.open(INBOX,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:fd=os.open(digest+'.tar.gz',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=d)
    finally:os.close(d)
    temporary=target.with_suffix('.copying')
    h=hashlib.sha256();size=0
    with os.fdopen(fd,'rb') as src:
        info=os.fstat(src.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_ARCHIVE:
            raise ValueError('Archive must be a bounded regular file')
        with open(temporary,'wb') as dst:
            os.chmod(temporary,0o600)
            while True:
                chunk=src.read(65536)
                if not chunk:break
                size+=len(chunk)
                if size>MAX_ARCHIVE:raise ValueError('Archive too large')
                h.update(chunk);dst.write(chunk)
            dst.flush();os.fsync(dst.fileno())
    if h.hexdigest()!=digest:
        temporary.unlink();raise ValueError('Uploaded archive digest differs')
    temporary.replace(target)
    return target


def unpack(archive,digest):
    destination=RELEASES/digest
    if destination.exists():
        # Only sealed and fully verified directories can be reused.
        meta=load(STATE/('release-'+digest+'.json'))
        if not meta:raise ValueError('Unregistered release directory')
        return meta
    staging=RELEASES/(digest+'.staging')
    if staging.exists():shutil.rmtree(staging)
    staging.mkdir(mode=0o755)
    try:
        with gzip.open(archive,'rb') as compressed:
            expanded=compressed.read(MAX_EXPANDED+1024*1024+1)
            if len(expanded)>MAX_EXPANDED+1024*1024:
                raise ValueError('Expanded archive too large')
        with tarfile.open(fileobj=io.BytesIO(expanded),mode='r:') as tar:
            members=tar.getmembers()
            names=[m.name for m in members]
            if len(members)>512 or len(set(names))!=len(names) or sum(m.size for m in members)>MAX_EXPANDED:
                raise ValueError('Archive member limits exceeded')
            for m in members:
                p=PurePosixPath(m.name)
                if (not m.isfile() or p.is_absolute() or str(p)!=m.name
                        or any(part in ('.','..','__pycache__','.DS_Store') or part.startswith('.') for part in p.parts)
                        or (m.name!='manifest.json' and (len(p.parts)<2 or p.parts[0] not in ('app','tests')))
                        or (p.name != 'LICENSE' and p.suffix not in ('.json','.py','.html','.css','.js','.lock','.txt','.tcl','.md','.svg','.png'))):
                    raise ValueError('Forbidden archive member')
            if 'manifest.json' not in names:raise ValueError('Missing release manifest')
            manifest=json.load(tar.extractfile('manifest.json'))
            if (manifest.get('schema')!=1 or manifest.get('journal_schema')!=2
                    or manifest.get('controller_contracts')!=[1,2]
                    or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',manifest.get('version',''))
                    or set(manifest.get('files',{}))!=set(names)-{'manifest.json'}):
                raise ValueError('Unsupported or incomplete manifest')
            for m in members:
                data=tar.extractfile(m).read()
                if m.name!='manifest.json' and hashlib.sha256(data).hexdigest()!=manifest['files'][m.name]:
                    raise ValueError('Release file digest differs')
                output=staging/m.name;output.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
                output.write_bytes(data);output.chmod(0o644)
        for name in ('app/app.py','app/control.py','app/engine.py','app/controllers.py',
                     'app/cycles.py','app/release.json','app/requirements.lock','tests/deploy_smoke.py'):
            if not (staging/name).is_file():raise ValueError('Missing required runtime or checker')
        if (staging/'app/requirements.lock').read_bytes()!=(APP/'requirements.lock').read_bytes():
            raise ValueError('Dependency changes require a separate reviewed environment update')
        release=json.loads((staging/'app/release.json').read_text())
        expected_release={'version':manifest['version'],'journal_schema':2,'controller_contracts':[1,2]}
        if 'receipt_expiry_api' in manifest:expected_release['receipt_expiry_api']=1
        if release!=expected_release:
            raise ValueError('Release metadata differs')
        staging.chmod(0o755)
        for p in staging.rglob('*'):
            if p.is_dir():p.chmod(0o755)
        staging.rename(destination)
        meta={'digest':digest,'version':manifest['version'],'path':str(destination/'app'),
              'verified_files':manifest['files'],'created_at':time.time()}
        save(STATE/('release-'+digest+'.json'),meta)
        return meta
    except BaseException:
        if staging.exists():shutil.rmtree(staging)
        raise


def control(payload):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(4);s.connect('/var/lib/coffee-bridge/deployment.sock')
        s.sendall(json.dumps(payload).encode()+b'\n')
        data=b''
        while not data.endswith(b'\n'):
            chunk=s.recv(4096)
            if not chunk or len(data)>65536:raise OSError('Incomplete local control response')
            data+=chunk
    reply=json.loads(data)
    if 'error' in reply:raise RuntimeError(reply['error'])
    return reply


def api(path):
    c=load(CONF)
    # Configuration is fixed, root-owned installation input, not caller input.
    req=urllib.request.Request(f"http://{c['listen_host']}:{c.get('port',80)}{path}")
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req,timeout=5) as r:return json.load(r)


def journal_idle():
    config=load(CONF);path=Path(config['database'])
    if path!=Path('/var/lib/coffee-bridge/bridge.sqlite3'):
        raise ValueError('Unexpected production journal path')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        row=db.execute("SELECT value FROM kv WHERE key='active'").fetchone()
        if row and json.loads(row[0]):raise RuntimeError('A cycle started during maintenance')
        row=db.execute("SELECT value FROM kv WHERE key='native'").fetchone()
        if row and json.loads(row[0]).get('pending_brew'):
            raise RuntimeError('The grinder still has a pending grind')
        if db.execute("SELECT 1 FROM shots WHERE state IN ('validating','delivering','uncertain') LIMIT 1").fetchone():
            raise RuntimeError('Unresolved delivery in journal')
    return path


def compatible(meta):
    if meta.get('legacy'):
        raise RuntimeError('Pre-contract-2 releases are not supported rollback targets')
    config = load(CONF, {})
    path = config.get('database')
    if path and Path(path).exists():
        with closing(sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True)) as db:
            row = db.execute("SELECT value FROM kv WHERE key='receipt_expiry_api'").fetchone()
        if row and json.loads(row[0]) == 1:
            release = load(Path(meta['path'])/'release.json', {})
            if release.get('receipt_expiry_api') != 1:
                raise RuntimeError('Rollback target cannot enforce expired telemetry receipts')


def configure():
    global INBOX, ACTOR
    info = POLICY.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Deployment policy must be a root-owned regular file')
    policy = load(POLICY)
    ACTOR = policy['operator']
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', ACTOR):
        raise ValueError('Invalid deployment operator')
    INBOX = Path(policy['inbox'])
    if not INBOX.is_absolute() or INBOX.is_symlink():
        raise ValueError('Invalid deployment inbox')


def backup(operation):
    directory=STATE/'backups'/operation
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Never overwrite a pre-activation backup after an interrupted activation.
    if not (directory/'journal.sqlite3').exists():
        source=journal_idle()
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(directory/'journal.sqlite3')) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Backup integrity failure')
        shutil.copyfile(CONF,directory/'config.json')
        for f in directory.iterdir():f.chmod(0o600)
    return str(directory)


def select(path):
    path=Path(path)
    if path.parent.parent!=RELEASES or path.name!='app' or not path.is_dir():
        raise ValueError('Unrecognized installed release path')
    link=APP.with_name('app.next')
    if link.is_symlink():link.unlink()
    os.symlink(str(path),link);os.replace(link,APP)
    directory=os.open(APP.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)


def health(meta):
    compatible(meta)
    for attempt in range(30):
        try:
            reply=control({'command':'status'})
            c=load(CONF).get('automation',{})
            healthy=(reply.get('healthy') and reply.get('version')==meta['version']
                and reply.get('journal_schema')==2 and reply.get('enabled')==c.get('enabled',False)
                and reply.get('feedback_mode')==c.get('feedback_mode','observe'))
            if healthy and run(SYSTEMCTL,'is-active','--quiet',SERVICE,check=False).returncode==0:
                return reply
        except (OSError,ValueError,RuntimeError):pass
        time.sleep(1)
    raise RuntimeError('Application health verification failed')


def update_job(job,phase,**values):
    job.update(phase=phase,updated_at=time.time(),**values)
    save(STATE/('operation-'+job['id']+'.json'),job)


def prune(current,previous,operation=None):
    """Bound code/backup retention without touching the live journal or audit records."""
    keep={current['digest'],previous['digest']}
    metas=sorted(STATE.glob('release-*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    keep.update(load(p)['digest'] for p in metas[:5])
    for p in metas:
        meta=load(p);digest=identifier(meta['digest'])
        if digest not in keep:
            release=RELEASES/digest
            if release.is_dir() and not release.is_symlink():shutil.rmtree(release)
            p.unlink()
    backups=sorted((p for p in (STATE/'backups').glob('*')
        if re.fullmatch('[0-9a-f]{64}',p.name) and p.is_dir() and not p.is_symlink()),
        key=lambda p:p.stat().st_mtime,reverse=True)
    if not load(STATE/'storage-policy.json', {}).get('minimal_storage'):
        # Existing large recovery copies wait for the owner's verified off-host migration.
        return
    # Keep the latest verified pre-update recovery copy. Software rollback uses
    # current data; it does not need a stack of older journals.
    if backups:
        latest=STATE/'backups'/identifier(operation) if operation else backups[0]
        if latest not in backups:raise ValueError('Latest recovery snapshot missing')
        if {p.name for p in latest.iterdir()} != {'journal.sqlite3','config.json'}:
            raise ValueError('Unexpected recovery files')
        if any(p.is_symlink() or not p.is_file() for p in latest.iterdir()):
            raise ValueError('Unsafe recovery file')
        with closing(sqlite3.connect((latest/'journal.sqlite3').as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Latest recovery snapshot failed verification')
        for p in (p for p in backups if p != latest):
            if {f.name for f in p.iterdir()} != {'journal.sqlite3','config.json'} or any(f.is_symlink() or not f.is_file() for f in p.iterdir()):
                raise ValueError('Unexpected obsolete recovery files')
        for p in (p for p in backups if p != latest):shutil.rmtree(p)


def current_meta():
    meta=load(STATE/'current.json')
    if not meta:raise RuntimeError('Bootstrap release registration missing')
    return meta


def check_release(meta):
    # Candidate code runs unprivileged, without networking or production credentials.
    run('/usr/bin/systemd-run','--quiet','--wait','--pipe','--collect',
        '--unit=coffee-sync-check-'+meta['digest'][:16],
        '-p','User=coffee-sync-check','-p','Group=coffee-sync-check','-p','PrivateNetwork=yes',
        '-p','PrivateTmp=yes','-p','ProtectSystem=strict','-p','ProtectHome=yes',
        '-p','NoNewPrivileges=yes','-p','PrivateDevices=yes','-p','CapabilityBoundingSet=',
        '-p','MemoryMax=256M','-p','TasksMax=32','-p','RuntimeMaxSec=120',
        '-p','InaccessiblePaths=/var/lib/coffee-bridge /etc/coffee-bridge /var/lib/coffee-sync-deploy',
        PYTHON,'-I','-B',str(Path(meta['path']).parent/'tests/deploy_smoke.py'),meta['path'],timeout=135)


def worker():
    with lock('worker.lock'):
        operation=load(STATE/'pending.json')
        if not operation:return
        job=load(STATE/('operation-'+identifier(operation)+'.json'))
        if not job or job['phase'] in TERMINAL:return
        meta=load(STATE/('release-'+job['release']+'.json'))
        previous=job.get('previous') or current_meta()
        stopped=job['phase'] in ('stopping','backed_up','activating','checking','rolling_back')
        switched=job['phase'] in ('activating','checking','rolling_back')
        try:
            compatible(meta)
            if stopped and APP.exists() and APP.resolve()==Path(meta['path']) and job['phase']!='rolling_back':
                # A lost SSH/worker response after successful start is not a reason
                # to restart an application that may already be handling a shot.
                try:
                    checked=health(meta)
                except RuntimeError:
                    journal_idle()  # Never roll code back underneath a new cycle.
                else:
                    save(STATE/'current.json',{**meta,'activation_id':job['id']})
                    update_job(job,'complete',health=checked,message='Recovered completed activation')
                    return
            elif stopped:
                journal_idle()
            if not stopped:
                update_job(job,'testing',previous=previous)
                check_release(meta)
                update_job(job,'waiting_idle')
                deadline=time.monotonic()+300
                while True:
                    try:
                        control({'command':'enter','operation':job['id']})
                        break
                    except (OSError,RuntimeError):
                        if time.monotonic()>=deadline:
                            update_job(job,'deferred',message='Waiting for an idle, connected coffee system');return
                        time.sleep(2)
                update_job(job,'stopping')
                stopped=True
            # Re-entry after a crash always stops again before journal checks.
            run(SYSTEMCTL,'stop',SERVICE)
            if not switched:
                journal_idle()
                path=backup(job['id']);update_job(job,'backed_up',backup=path)
                update_job(job,'activating');switched=True
            if job['phase']=='rolling_back':raise RuntimeError('Resume interrupted rollback')
            select(meta['path']);update_job(job,'checking')
            run(SYSTEMCTL,'start',SERVICE)
            checked=health(meta)
            save(STATE/'current.json',{**meta,'activation_id':job['id']})
            update_job(job,'complete',health=checked,message='Installed and healthy; shot history retained')
            try:prune(meta,previous,job['id'])
            except (OSError, ValueError, sqlite3.Error):
                update_job(job,'complete',retention_warning='Backup retention needs inspection')
        except Exception as exc:
            # Messages are fixed helpers' errors; never return subprocess output/config.
            message=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
            if stopped:
                if switched:
                    try:
                        journal_idle()
                        compatible(previous)
                    except Exception:
                        # A new cycle may have started after activation. Its live
                        # journal takes precedence over automatic code rollback.
                        run(SYSTEMCTL,'start',SERVICE,check=False)
                        update_job(job,'failed',message='Recovery deferred: a coffee cycle or uncertain delivery needs reconciliation')
                        return
                update_job(job,'rolling_back',message=message)
                try:
                    run(SYSTEMCTL,'stop',SERVICE)
                    if switched:select(previous['path'])
                    run(SYSTEMCTL,'start',SERVICE)
                    checked=health(previous)
                    save(STATE/'current.json',previous)
                    update_job(job,'rolled_back',health=checked,message=message)
                except Exception:
                    update_job(job,'failed',message='Update and recovery failed; inspect service diagnostics')
            else:update_job(job,'failed',message=message)
        finally:
            try:control({'command':'release','operation':job['id']})
            except Exception:pass


def submit(digest,rollback=False):
    identifier(digest)
    with lock('submit.lock'):
        pending=load(STATE/'pending.json')
        prior=load(STATE/('operation-'+pending+'.json')) if pending else None
        if prior and prior['phase'] not in TERMINAL:
            if prior['release']==digest:
                run(SYSTEMCTL,'start','--no-block',WORKER)
                return prior
            raise RuntimeError('Another deployment is active')
        old=current_meta()
        if old['digest']==digest:return {'phase':'complete','release':digest,'message':'Already installed'}
        operation=hashlib.sha256((('rollback:' if rollback else 'install:')+digest+':'+old['digest']
                                 +':'+old.get('activation_id','bootstrap')).encode()).hexdigest()
        job=load(STATE/('operation-'+operation+'.json'))
        if job and job['phase']!='deferred':return job
        if rollback:
            meta=load(STATE/('release-'+digest+'.json'))
            if not meta:raise ValueError('Rollback release is not registered')
        else:meta=unpack(seal(digest),digest)
        if shutil.disk_usage(STATE).free<128*1024*1024:raise RuntimeError('Insufficient backup space')
        job={'id':operation,'release':digest,'actor':ACTOR,'created_at':time.time(),'previous':old,'phase':'submitted'}
        save(STATE/('operation-'+operation+'.json'),job);save(STATE/'pending.json',operation)
        run(SYSTEMCTL,'start','--no-block',WORKER)
        return job


def diagnostics():
    # Explicit allowlist, no raw config, credentials, device data or command logs.
    result={'deployment':load(STATE/'current.json')}
    folders = {'daily':Path('/var/lib/coffee-bridge/backups'), 'deployment':STATE/'backups'}
    storage = {}
    for key,folder in folders.items():
        files = list(folder.glob('coffee-*.sqlite3')) if key == 'daily' else list(folder.glob('*/journal.sqlite3'))
        files = [p for p in files if p.is_file() and not p.is_symlink() and not p.parent.is_symlink()]
        storage[key] = {'count':len(files), 'bytes':sum(p.stat().st_size for p in files)}
    storage['backup_budget_bytes']=100*1024*1024
    storage['backup_budget_exceeded']=sum(v['bytes'] for v in storage.values() if isinstance(v,dict))>storage['backup_budget_bytes']
    result['storage']=storage
    try:
        s=api('/api/v1/status');a=s.get('automation',{})
        result['service']={k:s.get(k) for k in ('ready','mode','grinder_connected','last_native_read','error','maintenance')}
        result['controller']={k:a.get(k) for k in ('event_connected','summary','feedback_mode','event_contract','wake','machine')}
        result['shots']=[{k:r.get(k) for k in ('shot_id','created','feedback_status','reason','binding_failure','measurements','delivery')}
                         for r in a.get('shots',[])[:10]]
    except Exception:result['service']={'available':False}
    return result


def main(argv):
    if os.geteuid()!=0:raise RuntimeError('Use the installed restricted sudo command')
    if argv==['worker'] and 'SUDO_USER' not in os.environ:
        worker();return {'worker':'finished'}
    if argv==['status']:
        pending=load(STATE/'pending.json')
        return {'current':load(STATE/'current.json'),'operation':load(STATE/('operation-'+pending+'.json')) if pending else None}
    if len(argv)==2 and argv[0]=='status':
        return load(STATE/('operation-'+identifier(argv[1])+'.json'),{'error':'Unknown operation'})
    if argv==['diagnostics']:return diagnostics()
    if len(argv)==2 and argv[0] in ('submit','rollback'):return submit(argv[1],argv[0]=='rollback')
    raise ValueError('Allowed commands: submit SHA256, status [SHA256], diagnostics, rollback SHA256')


if __name__=='__main__':
    os.umask(0o077)
    try:
        configure()
        print(json.dumps(main(sys.argv[1:]),sort_keys=True))
    except Exception as exc:
        print(json.dumps({'error':str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__}));sys.exit(1)
