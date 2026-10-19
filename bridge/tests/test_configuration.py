"""Setup is disabled by default; controller changes cannot inherit readiness."""
import asyncio
import copy
import json

import pytest

import configuration
from configuration import configure_devices, configure_feedback, initial_config, save_config
from controllers import Controllers
from engine import Engine
from grinder_client import BridgeError
from store import Store
from support import FakeReader, profile


def installation(tmp_path):
    config = initial_config('http://192.168.10.2','192.168.10.0/24',tmp_path)
    config['_config_path'] = str(tmp_path/'config.json')
    save_config(config)
    engine = Engine(Store(config['database']),FakeReader())
    engine.controllers = Controllers(engine,config['automation'])
    return config,engine


def test_new_configuration_is_private_disabled_and_port_80(tmp_path):
    config,engine = installation(tmp_path)
    assert not engine.controllers.enabled
    assert engine.controllers.mode == 'observe'
    assert (tmp_path/'config.json').stat().st_mode & 0o777 == 0o600
    assert '_config_path' not in json.loads((tmp_path/'config.json').read_text())
    for url in ('http://192.168.10.2:8080','http://example.com','https://192.168.10.2'):
        with pytest.raises(ValueError):initial_config(url,'192.168.10.0/24',tmp_path)
    engine.store.close()


def test_controller_selection_restarts_without_cross_controller_profile_or_history(tmp_path):
    config,engine = installation(tmp_path)
    s = engine.store
    s.set('controller_profile',profile())
    s.set('controller_stream',{'session_id':'classic-session','sequence':1})
    s.save_profile('classic-de1','profile1',{'commissioned_revision':'rev1','execution_signature':'old'})
    s.save_controller_shot({'controller_id':'classic-de1','shot_id':'saved','feedback_status':'history_only'})
    engine.controllers = Controllers(engine,{'controller_id':'decaid-de1','controller_type':'decaid'})
    assert engine.controllers.observed_profile is None and engine.controllers.session is None
    assert engine.controllers.status()['shots'] == []
    assert s.controller_shot('classic-de1','saved')
    # Returning to Classic requires commissioning, while the record remains readable.
    engine.controllers = Controllers(engine,config['automation'])
    assert s.profile('classic-de1','profile1')['commissioned_revision'] is None
    assert len(engine.controllers.status()['shots']) == 1
    s.close()


def test_enrollment_pins_controller_and_crash_reconciliation(tmp_path,monkeypatch):
    async def run():
        config,engine = installation(tmp_path)
        async def resolve(host):return {'192.168.10.3'}
        async def certificates(host):return {'443':'a'*64,'9997':'b'*64}
        monkeypatch.setattr(configuration,'private_ipv4',resolve)
        monkeypatch.setattr(configuration,'certificate_fingerprints',certificates)
        data = {'schema_version':1,'controller_type':'decaid','controller_url':'http://192.168.10.4:8080',
            'grinder_host':'192.168.10.3','certificates':await certificates(''),'owner_password':'local-test-only'}
        engine.active = {'cycle_id':'busy'}
        with pytest.raises(BridgeError,match='current cycle'):await configure_devices(config,engine,data)
        engine.active = None
        wrong = copy.deepcopy(data);wrong['certificates']['443'] = 'c'*64
        with pytest.raises(BridgeError,match='changed'):await configure_devices(config,engine,wrong)
        result = await configure_devices(config,engine,data)
        assert result['complete'] and not result['feedback_enabled'] and not result['ready']
        assert engine.controllers.controller_id == 'decaid-de1'
        assert engine.store.get('controller_capture_identity') == config['automation']['capture_identity']
        assert 'local-test-only' not in json.dumps(result)
        # Simulate a crash after durable config replacement but before the DB commit.
        engine.store.set('controller_capture_identity','before-transition')
        engine.store.set('controller_profile',profile())
        restarted = Controllers(engine,config['automation'])
        assert restarted.observed_profile is None
        engine.store.close()
    asyncio.run(run())


def test_beta_enable_requires_idle_verified_targets_and_explicit_ack(tmp_path,monkeypatch):
    config,engine = installation(tmp_path)
    config['setup_complete'] = True
    monkeypatch.setattr(configuration,'idle_reason',lambda e:None)
    c = engine.controllers
    monkeypatch.setattr(c,'targets',lambda:{'confirmed':False})
    data = {'schema_version':1,'enabled':True,'acknowledge_beta':True}
    with pytest.raises(BridgeError,match='Commission'):configure_feedback(config,engine,data)
    monkeypatch.setattr(c,'targets',lambda:{'confirmed':True})
    with pytest.raises(BridgeError,match='Acknowledge'):configure_feedback(config,engine,{**data,'acknowledge_beta':False})
    configure_feedback(config,engine,data)
    assert c.enabled and c.mode == 'xenia_one_press' and not c.validation_complete
    restarted = Controllers(engine,config['automation'])
    assert restarted.enabled and not restarted.validation_complete
    engine.store.close()
