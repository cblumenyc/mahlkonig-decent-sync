"""Actual Tcl settings, exact queued bytes and interrupted rename recovery."""
import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('classic_rename',ROOT/'ops/migrations/classic_rename.py')
migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
spec=importlib.util.spec_from_file_location('migration_release',ROOT/'ops/release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
SERVER='ba0d4af0-563c-41c5-ab22-bfe89bb978de'


def installation(tmp_path,combined=True):
    import json
    code=tmp_path/'app/plugins/decent_sync';code.mkdir(parents=True)
    data=code if combined else tmp_path/'data/plugins/decent_sync'
    data.mkdir(parents=True,exist_ok=True)
    (code/'plugin.tcl').write_text('namespace eval ::plugins::decent_sync {variable version 0.5.6}\n')
    (code/'README.md').write_text('original code')
    (data/'outbox').mkdir()
    queued=b'{"event_id":"frozen-1", "data":{"comment":"caf\xc3\xa9"}}\n'
    (data/'outbox/frozen-00000001.json').write_bytes(queued)
    settings={'bridge_url':'http://sync.local','controller_id':'classic-de1',
        'measurement_mode':'auto','banner_duration':'4','private_preference':'[error must-not-evaluate] $secret; café'}
    (data/'settings.tdb').write_bytes(migration.settings_bytes(settings))
    global_path=tmp_path/'settings.tdb'
    global_path.write_bytes(migration.settings_bytes({'enabled_plugins':'other decent_sync more','profile':'A {B} C','history':'retained'}))
    model=json.loads((ROOT/'release-spec.json').read_text())
    package=tmp_path/'classic.zip'
    with zipfile.ZipFile(package,'w') as output:
        for name,body in release.classic_package(ROOT,'0.1.0-beta.1',model).items():output.writestr(migration.NEW+'/'+name,body)
    backup=tmp_path/'private-recovery'
    original=(migration.tree(code),migration.tree(data),global_path.read_bytes())
    digest=hashlib.sha256(package.read_bytes()).hexdigest()
    args=(code,data,global_path,package,digest,backup,SERVER)
    return args,original,queued,settings


def identity(url):
    assert url=='http://sync.local'
    return {'product':'mahlkonig-decent-sync','server_id':SERVER,'controller_id':'classic-de1','controller_contracts':[1,2]}


@pytest.mark.parametrize('combined',[True,False])
@pytest.mark.parametrize('phase',['disabled','code','data','retired','settings'])
def test_every_interrupted_boundary_resumes_without_losing_state(tmp_path,combined,phase):
    args,original,queued,settings=installation(tmp_path,combined)
    migration.prepare(*args,fetch=identity)
    def interrupt(current):
        if current==phase:raise RuntimeError('simulated power interruption')
    with pytest.raises(RuntimeError):migration.apply(args[5],checkpoint=interrupt)
    assert migration.apply(args[5])=='complete'
    assert not args[0].exists() and not args[1].exists()
    new_data=args[1].with_name(migration.NEW)
    assert (new_data/'outbox/frozen-00000001.json').read_bytes()==queued
    assert migration.read_settings((new_data/'settings.tdb').read_bytes())==dict(settings,server_id=SERVER)
    global_values=migration.read_settings(args[2].read_bytes())
    assert global_values['enabled_plugins']=='other mahlkonig_decent_sync more'
    assert global_values['history']=='retained' and global_values['profile']=='A {B} C'
    assert migration.apply(args[5],rollback=True)=='rolled-back'
    assert (migration.tree(args[0]),migration.tree(args[1]),args[2].read_bytes())==original


def test_recovery_refuses_rebinding_changed_data_and_damaged_backup(tmp_path):
    args,original,*_=installation(tmp_path)
    def wrong(url):return dict(identity(url),server_id='ccb9be79-4ee3-4308-82de-771997bd3d00')
    with pytest.raises(ValueError,match='existing server'):migration.prepare(*args,fetch=wrong)
    assert not args[5].exists()
    migration.prepare(*args,fetch=identity)
    migration.apply(args[5])
    next_event=args[1].with_name(migration.NEW)/'outbox/new-event.json';next_event.write_text('new event after app restart')
    with pytest.raises(ValueError,match='data changed'):migration.apply(args[5],rollback=True)
    next_event.unlink()
    (args[5]/'original-code/plugin.tcl').write_text('damaged')
    with pytest.raises(ValueError,match='Recovery material'):migration.apply(args[5],rollback=True)


def test_disabled_plugin_stays_disabled(tmp_path):
    args,*_=installation(tmp_path)
    args[2].write_bytes(migration.settings_bytes({'enabled_plugins':'other more'}))
    migration.prepare(*args,fetch=identity);migration.apply(args[5])
    assert migration.read_settings(args[2].read_bytes())['enabled_plugins']=='other more'
