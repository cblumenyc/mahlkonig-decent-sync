"""Activate a verified Compose release with an idle gate and code-only recovery.

Run as the installation's Docker operator. No network download or privileged
helper installation occurs here. Keep both release directories and the private
transaction file until the next successful update.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid


def run(args, *, timeout=180):
    result=subprocess.run(args,check=True,capture_output=True,text=True,timeout=timeout)
    return result.stdout.strip()


def compose(directory, *args, execute=run):
    directory=Path(directory)
    return execute(['docker','compose','--project-directory',str(directory),'--env-file',str(directory/'.env'),
        '-f',str(directory/'compose.yaml'),*args])


CONTROL="""import json,socket,sys
s=socket.socket(socket.AF_UNIX);s.settimeout(5);s.connect('/data/deployment.sock')
s.sendall(sys.argv[1].encode()+b'\\n');body=s.makefile('rb').readline(4097)
result=json.loads(body)
if 'error' in result:raise RuntimeError(result['error'])
print(json.dumps(result))
"""


def control(directory, command, operation, *, execute=run):
    # Docker access already grants host administration. The temporary exec needs
    # root DAC access to the existing root-only socket; the server stays nonroot.
    return json.loads(compose(directory,'exec','-T','--user','0','--privileged','sync','python','-c',CONTROL,
        json.dumps({'command':command,'operation':operation}),execute=execute))


def offline_idle(directory, *, execute=run):
    verify="""import json,sqlite3
c=sqlite3.connect('file:/data/bridge.sqlite3?mode=ro',uri=True)
v={k:json.loads(v) for k,v in c.execute('select key,value from kv')}
assert not v.get('active'), 'A cycle raced the stop; restart the current release'
for row in c.execute('select record from controller_shots'):
 r=json.loads(row[0]);assert r.get('feedback_status') not in ('collecting','awaiting_events','awaiting_final_mass','awaiting_arm','awaiting_native','awaiting_native_validation','queued','dispatching'), 'Unfinished controller record'
"""
    compose(directory,'run','--rm','--no-deps','--entrypoint','python','sync','-c',verify,execute=execute)


def save(path, value):
    temporary=path.with_name(path.name+'.pending')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(value,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def fingerprint(directory):
    """Bind the transaction to the extracted, provenance-verified package bytes."""
    directory=Path(directory)
    files={}
    for path in directory.rglob('*'):
        if path.is_symlink():raise ValueError('Release contents cannot be symlinks')
        if path.is_file():files[str(path.relative_to(directory))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def prepare(current, target, transaction, *, execute=run):
    current,target,transaction=map(lambda p:Path(p).resolve(),(current,target,transaction))
    if transaction.exists():raise ValueError('Transaction exists; use resume')
    if any(transaction.is_relative_to(p) for p in (current,target)):
        raise ValueError('Keep the private transaction outside release directories')
    if current==target:raise ValueError('Use a separate versioned release directory')
    old=json.loads((current/'app/release.json').read_text())
    new=json.loads((target/'app/release.json').read_text())
    if any(meta.get('journal_schema')!=2 or meta.get('controller_contracts')!=[1,2] for meta in (old,new)):
        raise ValueError('Journal/controller compatibility differs')
    if (target/'.env').exists():raise ValueError('New release already has installation settings')
    lines=(current/'.env').read_text().splitlines()
    if len([line for line in lines if line.startswith('SYNC_VERSION=')])!=1:raise ValueError('Invalid current release environment')
    body='\n'.join('SYNC_VERSION='+new['version'] if line.startswith('SYNC_VERSION=') else line for line in lines)+'\n'
    fd=os.open(target/'.env',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write(body);stream.flush();os.fsync(stream.fileno())
    compose(target,'config','--quiet',execute=execute)
    compose(target,'build',execute=execute)  # Does not stop the current service.
    target_image=execute(['docker','image','inspect','--format','{{.Id}}','mahlkonig-decent-sync:'+new['version']])
    container=compose(current,'ps','-q','sync',execute=execute)
    if not container:raise ValueError('Current service is not running')
    image=execute(['docker','inspect','--format','{{.Image}}',container])
    operation=uuid.uuid4().hex+uuid.uuid4().hex
    rollback_image='mahlkonig-decent-sync:'+old['version']
    # Refuse to rely on a tag which has already been replaced behind the running service.
    if execute(['docker','image','inspect','--format','{{.Id}}',rollback_image])!=image:
        raise ValueError('Previous image tag differs; recover its exact image before updating')
    plan={'schema':1,'operation':operation,'phase':'prepared','current':str(current),'target':str(target),
        'old_version':old['version'],'new_version':new['version'],'old_image':image,'new_image':target_image,
        'current_files':fingerprint(current),'target_files':fingerprint(target)}
    save(transaction,plan)
    return plan


def activate(transaction, *, rollback=False, execute=run):
    transaction=Path(transaction).resolve()
    with transaction.with_name(transaction.name+'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        plan=json.loads(transaction.read_text())
        if plan.get('schema')!=1:raise ValueError('Unsupported update transaction')
        for key in ('current','target'):
            if fingerprint(plan[key])!=plan[key+'_files']:raise ValueError('Release contents changed after preparation')
        if execute(['docker','image','inspect','--format','{{.Id}}','mahlkonig-decent-sync:'+plan['old_version']])!=plan['old_image']:
            raise ValueError('Verified rollback image is unavailable')
        if execute(['docker','image','inspect','--format','{{.Id}}','mahlkonig-decent-sync:'+plan['new_version']])!=plan['new_image']:
            raise ValueError('Prepared image changed')
        source=plan['target'] if rollback else plan['current']
        target=plan['current'] if rollback else plan['target']
        expected=plan['old_version'] if rollback else plan['new_version']
        operation=plan['operation']
        def phase(name):plan['phase']=name;save(transaction,plan)
        # A response lost after activation must not restart a now-busy system.
        try:state=control(target,'status',operation,execute=execute)
        except (subprocess.SubprocessError,ValueError):state={}
        if state.get('version')==expected and state.get('healthy'):
            phase('rolled-back' if rollback else 'complete');return plan['phase']
        # Re-entering always proves idle again. If a newly active service cannot
        # prove that, retain its live journal and require operator recovery.
        try:state=control(source,'status',operation,execute=execute)
        except (subprocess.SubprocessError,ValueError):
            if plan['phase'] not in ('stopping','stopped'):raise
            # A stopped transaction has not selected new code. Restart the same
            # release, reconcile native state, then let the idle gate decide.
            compose(source,'up','-d','--no-build','--wait','--wait-timeout','90',execute=execute)
            state=control(source,'status',operation,execute=execute)
        source_version=plan['new_version'] if rollback else plan['old_version']
        if state.get('version')!=source_version:raise ValueError('Running release differs; inspect recovery state before continuing')
        control(source,'enter',operation,execute=execute)
        phase('idle')
        try:
            compose(source,'exec','-T','sync','python','/app/backup.py','/data/bridge.sqlite3',execute=execute)
            phase('backed-up')
            phase('stopping')
            compose(source,'stop','sync',execute=execute);phase('stopped')
            # A grind can cancel the idle lease immediately before the stop.
            # Check the frozen journal offline before replacing the executable.
            offline_idle(source,execute=execute)
            phase('activating')
            compose(target,'up','-d','--no-build','--wait','--wait-timeout','90',execute=execute)
            state=control(target,'status',operation,execute=execute)
            if state.get('version')!=expected or not state.get('healthy'):raise ValueError('Activated release health differs')
            phase('rolled-back' if rollback else 'complete')
            return plan['phase']
        except (subprocess.SubprocessError,ValueError):
            # Before activation, restart the original executable. After activation,
            # automatic rollback is allowed only if that service proves idle.
            if plan['phase']=='activating':
                try:control(target,'enter',operation,execute=execute)
                except (subprocess.SubprocessError,ValueError):
                    phase('needs-recovery')
                    raise RuntimeError('New service could not prove idle; retained its journal and deferred rollback') from None
                phase('recovery-stopping')
                compose(target,'stop','sync',execute=execute)
                try:offline_idle(target,execute=execute)
                except (subprocess.SubprocessError,ValueError):
                    compose(target,'up','-d','--no-build','--wait','--wait-timeout','90',execute=execute)
                    phase('needs-recovery')
                    raise RuntimeError('A cycle raced rollback; restarted new code with its live journal') from None
            compose(source,'up','-d','--no-build','--wait','--wait-timeout','90',execute=execute)
            phase('rolled-back' if not rollback else 'rollback-failed')
            raise RuntimeError('Activation failed; previous executable restarted with the current journal') from None
        finally:
            try:control(source,'release',operation,execute=execute)
            except (subprocess.SubprocessError,ValueError):pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','resume','rollback'])
    parser.add_argument('--transaction',type=Path,required=True)
    parser.add_argument('--current',type=Path)
    parser.add_argument('--target',type=Path)
    args=parser.parse_args()
    os.umask(0o077)
    if args.action=='prepare':
        if not args.current or not args.target:parser.error('Preparation needs current and verified target directories')
        prepare(args.current,args.target,args.transaction)
        print('Built and recorded. Run resume while the coffee system is idle.')
    else:print(activate(args.transaction,rollback=args.action=='rollback'))


if __name__=='__main__':main()
