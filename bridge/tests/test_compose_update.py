"""Compose activation uses the same idle lease; rollback never restores a journal."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('compose_update',ROOT/'ops/container/update.py')
update=importlib.util.module_from_spec(spec);spec.loader.exec_module(update)


def installation(tmp_path):
    current,target=tmp_path/'current',tmp_path/'target'
    for folder,version in ((current,'0.1.0-beta.1'),(target,'0.1.0-beta.2')):
        (folder/'app').mkdir(parents=True)
        (folder/'app/release.json').write_text(json.dumps({'version':version,'journal_schema':2,'controller_contracts':[1,2]}))
        (folder/'compose.yaml').write_text('fixture')
    (current/'.env').write_text('SYNC_VERSION=0.1.0-beta.1\nSYNC_ORIGIN=http://192.168.10.2\nSYNC_LAN_CIDR=192.168.10.0/24\n')
    return current,target,tmp_path/'private-transaction.json'


class Docker:
    def __init__(self):
        self.version='0.1.0-beta.1';self.calls=[];self.busy=False;self.fail_activate=False
        self.stopped=False;self.interrupt_stop=False;self.rollback_race=False
    def __call__(self,args):
        self.calls.append(args)
        if args[1:3]==['image','inspect']:return 'image-new' if args[-1].endswith('beta.2') else 'image-old'
        if args[1]=='inspect':return 'image-old'
        if 'ps' in args:return 'container'
        if update.CONTROL in args:
            if self.stopped:raise subprocess.CalledProcessError(1,args)
            request=json.loads(args[-1])
            if request['command']=='enter' and self.busy:raise subprocess.CalledProcessError(1,args)
            return json.dumps({'version':self.version,'healthy':True})
        if 'up' in args:
            self.stopped=False
            self.version='0.1.0-beta.2' if 'target' in args[args.index('--project-directory')+1] else '0.1.0-beta.1'
            if self.fail_activate and self.version.endswith('beta.2'):raise subprocess.CalledProcessError(1,args)
        if 'stop' in args:
            self.stopped=True
            if self.interrupt_stop:
                self.interrupt_stop=False
                raise KeyboardInterrupt
        if 'run' in args and self.rollback_race and self.version.endswith('beta.2'):
            raise subprocess.CalledProcessError(1,args)
        return ''


def test_update_restart_response_loss_and_code_only_rollback(tmp_path):
    current,target,transaction=installation(tmp_path);docker=Docker()
    update.prepare(current,target,transaction,execute=docker)
    assert update.activate(transaction,execute=docker)=='complete'
    calls=len(docker.calls)
    docker.busy=True
    assert update.activate(transaction,execute=docker)=='complete'
    assert not any('stop' in call for call in docker.calls[calls:])
    with pytest.raises(subprocess.CalledProcessError):update.activate(transaction,rollback=True,execute=docker)
    docker.busy=False
    assert update.activate(transaction,rollback=True,execute=docker)=='rolled-back'
    assert any('/app/backup.py' in call for call in docker.calls)
    assert all('--restore-to' not in call and '-v' not in call for call in docker.calls)


def test_idle_rejection_and_activation_failure_keep_current_data(tmp_path):
    args=installation(tmp_path);docker=Docker();update.prepare(*args,execute=docker)
    docker.busy=True
    with pytest.raises(subprocess.CalledProcessError):update.activate(args[2],execute=docker)
    assert not any('stop' in call for call in docker.calls)
    docker.busy=False;docker.fail_activate=True
    with pytest.raises(RuntimeError,match='previous executable'):update.activate(args[2],execute=docker)
    assert docker.version=='0.1.0-beta.1'
    assert json.loads(args[2].read_text())['phase']=='rolled-back'


def test_interrupted_stop_resumes_same_code_before_idle_check(tmp_path):
    args=installation(tmp_path);docker=Docker();update.prepare(*args,execute=docker)
    docker.interrupt_stop=True
    with pytest.raises(KeyboardInterrupt):update.activate(args[2],execute=docker)
    assert docker.stopped and json.loads(args[2].read_text())['phase']=='stopping'
    assert update.activate(args[2],execute=docker)=='complete'


def test_prepared_settings_cannot_redirect_update(tmp_path):
    current,target,transaction=installation(tmp_path);docker=Docker()
    update.prepare(current,target,transaction,execute=docker)
    (target/'.env').write_text('SYNC_VERSION=another-image\n')
    with pytest.raises(ValueError,match='contents changed'):update.activate(transaction,execute=docker)
    assert not any('stop' in call for call in docker.calls)


def test_cycle_racing_failed_activation_defers_rollback(tmp_path):
    args=installation(tmp_path);docker=Docker();update.prepare(*args,execute=docker)
    docker.fail_activate=True;docker.rollback_race=True
    # The new service can be restarted but its first activation loses its response.
    execute=docker
    def once(arguments):
        try:return execute(arguments)
        finally:
            if 'up' in arguments:docker.fail_activate=False
    with pytest.raises(RuntimeError,match='cycle raced rollback'):update.activate(args[2],execute=once)
    assert docker.version.endswith('beta.2') and not docker.stopped
    assert json.loads(args[2].read_text())['phase']=='needs-recovery'
