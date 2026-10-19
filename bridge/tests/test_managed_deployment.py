import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tarfile

import pytest

spec=importlib.util.spec_from_file_location('managed_deploy',Path(__file__).resolve().parents[2]/'ops/deployment/deploy.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)


@pytest.fixture
def env(tmp_path,monkeypatch):
    for name,leaf in [('STATE','state'),('RELEASES','releases'),('APP','app'),('INBOX','inbox')]:
        path=tmp_path/leaf;path.mkdir();monkeypatch.setattr(d,name,path)
    monkeypatch.setattr(d,'CONF',tmp_path/'config.json')
    d.CONF.write_text('{}')
    (d.APP/'requirements.lock').write_text('same dependencies')
    return tmp_path


def archive(path,*,bad=None):
    files={p:b'pass\n' for p in ('app/app.py','app/control.py','app/controllers.py','app/engine.py',
        'app/cycles.py','tests/deploy_smoke.py')}
    files['app/requirements.lock']=b'same dependencies'
    files['app/release.json']=json.dumps({'version':'test-v1','journal_schema':2,'controller_contracts':[1,2]}).encode()
    if bad=='traversal':files['app/../../escape.py']=b'escape'
    if bad=='ops':files['ops/worker.py']=b'pass'
    if bad=='dependency':files['app/requirements.lock']=b'different'
    manifest={'schema':1,'version':'test-v1','journal_schema':2,'controller_contracts':[1,2],
        'files':{p:hashlib.sha256(b).hexdigest() for p,b in files.items()}}
    if bad=='schema':manifest['journal_schema']=3
    if bad=='tamper':files['app/app.py']=b'wrong'
    if bad=='unlisted':files['app/surprise.py']=b'pass'
    files['manifest.json']=json.dumps(manifest).encode()
    with tarfile.open(path,'w:gz') as tar:
        for p,b in files.items():
            m=tarfile.TarInfo(p);m.size=len(b)
            if bad in ('symlink','hardlink','device') and p=='app/app.py':
                m.type={'symlink':tarfile.SYMTYPE,'hardlink':tarfile.LNKTYPE,'device':tarfile.CHRTYPE}[bad]
                m.linkname='/etc/passwd'
            tar.addfile(m,io.BytesIO(b))
        if bad=='duplicate':
            m=tarfile.TarInfo('app/app.py');m.size=0;tar.addfile(m,io.BytesIO())
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('bad',[None,'traversal','ops','dependency','schema','tamper','unlisted','symlink','hardlink','device','duplicate'])
def test_archive_boundary_and_immutable_seal(env,bad):
    file=env/'source.tgz';digest=archive(file,bad=bad)
    file.rename(d.INBOX/(digest+'.tar.gz'))
    sealed=d.seal(digest)
    # Changing the original upload cannot change the verified copy.
    (d.INBOX/(digest+'.tar.gz')).write_bytes(b'changed')
    if bad:
        with pytest.raises(ValueError):d.unpack(sealed,digest)
        assert not (env/'escape.py').exists()
        assert not (d.RELEASES/digest).exists()
    else:
        result=d.unpack(sealed,digest)
        assert Path(result['path']).is_dir()
        assert Path(result['path']).stat().st_mode & 0o777==0o755
        assert d.unpack(sealed,digest)==result


def test_inbox_symlink_and_bad_digest_rejected(env):
    file=d.INBOX/('a'*64+'.tar.gz');file.symlink_to(d.CONF)
    with pytest.raises(OSError):d.seal('a'*64)
    file.unlink();file.write_bytes(b'tamper')
    with pytest.raises(ValueError):d.seal('a'*64)
    with pytest.raises(ValueError):d.identifier('../anything')


def test_current_community_package_is_accepted_by_reviewed_manager(env):
    root=Path(__file__).resolve().parents[2]
    builder_spec=importlib.util.spec_from_file_location('community_builder',root/'ops/release.py')
    builder=importlib.util.module_from_spec(builder_spec);builder_spec.loader.exec_module(builder)
    (d.APP/'requirements.lock').write_bytes((root/'bridge/requirements.lock').read_bytes())
    builder.build(root,'1.0.0-beta.1',env/'packages')
    package=next((env/'packages').glob('*-server.tar.gz'))
    digest=hashlib.sha256(package.read_bytes()).hexdigest()
    meta=d.unpack(package,digest)
    installed=Path(meta['path'])
    assert (installed/'static/mahlkonig.svg').is_file()
    catalogue=json.loads((installed/'updates/classic.json').read_text())
    assert catalogue['version']=='1.0.0-beta.1'
    assert (installed/'updates/classic/plugin.tcl').is_file()


def test_cli_cannot_become_shell_or_worker(env,monkeypatch):
    monkeypatch.setattr(d.os,'geteuid',lambda:0)
    monkeypatch.setenv('SUDO_USER','operator')
    for args in (['worker'],['shell'],['submit','../../etc'],['status','-o'],['diagnostics','/etc/shadow'],['status','a'*64,'extra']):
        with pytest.raises(ValueError):d.main(args)
    assert d.main(['status'])=={'current':None,'operation':None}


def test_checker_uses_fixed_unprivileged_offline_unit(env,monkeypatch):
    calls=[];monkeypatch.setattr(d,'run',lambda *args,**kw:calls.append(args))
    d.check_release({'digest':'a'*64,'path':str(d.RELEASES/'a'/'app')})
    args=calls[0]
    assert 'User=coffee-sync-check' in args and 'PrivateNetwork=yes' in args
    assert 'ProtectSystem=strict' in args and '-I' in args
    assert any(v.startswith('InaccessiblePaths=/var/lib/coffee-bridge') for v in args)


def job_env(env,monkeypatch):
    old={'digest':'a'*64,'version':'old','path':str(d.RELEASES/('a'*64)/'app')}
    new={'digest':'b'*64,'version':'new','path':str(d.RELEASES/('b'*64)/'app')}
    for meta in (old,new):
        Path(meta['path']).mkdir(parents=True)
        d.save(d.STATE/('release-'+meta['digest']+'.json'),meta)
    (d.APP/'requirements.lock').unlink();d.APP.rmdir();d.APP.symlink_to(old['path'])
    d.save(d.STATE/'current.json',old)
    job={'id':'c'*64,'release':new['digest'],'previous':old,'phase':'submitted'}
    d.save(d.STATE/('operation-'+job['id']+'.json'),job);d.save(d.STATE/'pending.json',job['id'])
    database=env/'journal.sqlite3'
    with sqlite3.connect(database) as db:db.execute('CREATE TABLE evidence (value TEXT)');db.execute("INSERT INTO evidence VALUES ('original')")
    monkeypatch.setattr(d,'journal_idle',lambda:database)
    monkeypatch.setattr(d,'check_release',lambda meta:None)
    calls=[];monkeypatch.setattr(d,'run',lambda *args,**kw:calls.append(args))
    monkeypatch.setattr(d,'control',lambda payload:{'entered':True})
    monkeypatch.setattr(d,'health',lambda meta:{'healthy':True,'version':meta['version']})
    return old,new,job,database,calls


def test_success_and_repeat_worker_do_not_reinstall_or_rewind_journal(env,monkeypatch):
    old,new,job,db,calls=job_env(env,monkeypatch)
    d.worker();count=len(calls);d.worker()
    assert len(calls)==count
    assert d.APP.resolve()==Path(new['path'])
    saved=d.load(d.STATE/('operation-'+job['id']+'.json'))
    assert saved['phase']=='complete'
    assert (Path(saved['backup'])/'journal.sqlite3').exists()


def test_health_failure_rolls_back_code_and_retains_new_evidence(env,monkeypatch):
    old,new,job,database,calls=job_env(env,monkeypatch)
    def health(meta):
        if meta==new:
            with sqlite3.connect(database) as db:db.execute("INSERT INTO evidence VALUES ('new delivery evidence')")
            raise RuntimeError('Bad app')
        return {'healthy':True}
    monkeypatch.setattr(d,'health',health)
    d.worker()
    assert d.APP.resolve()==Path(old['path'])
    assert d.load(d.STATE/('operation-'+job['id']+'.json'))['phase']=='rolled_back'
    with sqlite3.connect(database) as db:assert db.execute('SELECT count(*) FROM evidence').fetchone()[0]==2


def test_racing_shot_cancels_update_and_restarts_existing_code(env,monkeypatch):
    old,new,job,db,calls=job_env(env,monkeypatch)
    def busy():raise RuntimeError('A cycle started')
    monkeypatch.setattr(d,'journal_idle',busy)
    d.worker()
    assert d.APP.resolve()==Path(old['path'])
    assert (d.SYSTEMCTL,'start',d.SERVICE) in calls
    assert d.load(d.STATE/('operation-'+job['id']+'.json'))['phase']=='rolled_back'


def test_crash_after_switch_recovers_without_restarting_healthy_app(env,monkeypatch):
    old,new,job,db,calls=job_env(env,monkeypatch)
    class PowerLoss(BaseException):pass
    actual=d.select
    def interrupted(path):actual(path);raise PowerLoss()
    monkeypatch.setattr(d,'select',interrupted)
    with pytest.raises(PowerLoss):d.worker()
    assert d.load(d.STATE/('operation-'+job['id']+'.json'))['phase']=='activating'
    monkeypatch.setattr(d,'select',actual);calls.clear()
    d.worker()
    assert not calls
    assert d.load(d.STATE/('operation-'+job['id']+'.json'))['phase']=='complete'


def test_busy_system_defers_without_stopping_service(env,monkeypatch):
    old,new,job,db,calls=job_env(env,monkeypatch)
    def busy(payload):raise RuntimeError('Late-arming wait')
    monkeypatch.setattr(d,'control',busy)
    clock=iter([0,301]);monkeypatch.setattr(d.time,'monotonic',lambda:next(clock))
    d.worker()
    assert not calls and d.APP.resolve()==Path(old['path'])
    assert d.load(d.STATE/('operation-'+job['id']+'.json'))['phase']=='deferred'


def test_submit_is_idempotent_but_can_reinstall_after_intentional_rollback(env,monkeypatch):
    old,new,job,database,calls=job_env(env,monkeypatch)
    (d.STATE/'pending.json').unlink()
    monkeypatch.setattr(d,'seal',lambda digest:env/'sealed')
    monkeypatch.setattr(d,'unpack',lambda path,digest:new)
    first=d.submit(new['digest'])
    assert d.submit(new['digest'])['id']==first['id']
    assert all(args[-1]==d.WORKER and '--no-block' in args for args in calls)
    first['phase']='complete';d.save(d.STATE/('operation-'+first['id']+'.json'),first)
    # A later explicit rollback changes the installation generation.
    d.save(d.STATE/'current.json',{**old,'activation_id':'d'*64})
    again=d.submit(new['digest'])
    assert again['id']!=first['id'] and again['phase']=='submitted'


def test_retired_bootstrap_release_is_rejected():
    with pytest.raises(RuntimeError, match='Pre-contract-2'):
        d.compatible({'legacy': True})
