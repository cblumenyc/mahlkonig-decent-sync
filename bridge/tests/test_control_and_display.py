import asyncio
import time

import pytest
from control import command, idle_reason
from grinder_client import BridgeError
from support import setup_v2, lifecycle, advance
from support import prepare_done


@pytest.mark.parametrize('problem', [None, 'sensor_failed', 'missing_flag', 'missing_driver',
    'missing_value', 'nan', 'inf', 'string', 'bool', 'negative', 'stale', 'poll_stale', 'error', 'future'])
def test_grind_display_uses_only_fresh_cached_measured_position(tmp_path, problem):
    async def run():
        e,c,r,p,claim = await setup_v2(tmp_path)
        e.driver = {'data':{'ddValueDum':999, 'ddSensorFailed':False}}
        e.last_native = time.time()-3
        e.last_poll = time.time()
        if problem == 'sensor_failed':e.driver['data']['ddSensorFailed'] = True
        elif problem == 'missing_flag':del e.driver['data']['ddSensorFailed']
        elif problem == 'missing_driver':e.driver = {}
        elif problem == 'missing_value':del e.driver['data']['ddValueDum']
        elif problem in ('nan', 'inf'):e.driver['data']['ddValueDum'] = float(problem)
        elif problem == 'string':e.driver['data']['ddValueDum'] = '999'
        elif problem == 'bool':e.driver['data']['ddValueDum'] = True
        elif problem == 'negative':e.driver['data']['ddValueDum'] = -1
        elif problem == 'stale':e.last_native = time.time()-36
        elif problem == 'poll_stale':e.last_poll = time.time()-11
        elif problem == 'error':e.error = 'read failed'
        elif problem == 'future':e.last_native = time.time()+1
        async def no_io():raise AssertionError('Display must use cached data')
        r.read = no_io
        before = (e.store.controller_history(), e.store.recent())
        display = c.display(c.session)
        if problem is None:
            assert display['grind_position']['um'] == 99.9
            assert 3 <= display['grind_position']['age_s'] < 4
            assert set(display['grind_position']) == {'um', 'age_s'}
        else:
            assert display['grind_position'] is None
        assert (e.store.controller_history(), e.store.recent()) == before
        await e.close();e.store.close()
    asyncio.run(run())


def test_late_arm_blocks_maintenance_and_display_distinguishes_receipt(tmp_path):
    async def run():
        e,c,r,p,claim=await setup_v2(tmp_path)
        rows=lifecycle(p,claim)
        ack=c.accept('classic-de1',rows[0])
        assert ack['display']['notice']['title']=='Shot matched'
        for row in rows[1:]:c.accept('classic-de1',row)
        await advance(e,c)
        assert c.display(c.session)['notice']['title']=='Shot saved'
        assert c.display('old-session')['notice'] is None
        assert c.display(c.session)['grinder_connected']
        assert not c.display(c.session)['sync_ready']
        assert c.display(c.session)['sync_warning']=='grinder_press_required'
        with pytest.raises(BridgeError):command(e,{'command':'enter','operation':'a'*64})
        e.execute({'ID':9});await advance(e,c);e.mako();e.mako();await prepare_done(e)
        assert c.display(c.session)['notice']['title']=='Updating grinder'
        result=e.mako();assert c.display(c.session)['notice']['title']=='Updating grinder'
        r.finish(result);await e.refresh();e.verify()
        assert c.display(c.session)['notice']['title']=='Grinder received shot'
        assert c.display(c.session)['notice']['detail']=='Burr movement confirmed.'
        assert c.display(c.session)['sync_ready']
        e.last_poll=time.time()-30
        assert not c.display(c.session)['grinder_connected']
        await e.close();e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('problem',['attention','aborting','target','disabled','recovery','maintenance','history_only'])
def test_connected_devices_do_not_imply_sync_ready(tmp_path,problem):
    async def run():
        e,c,r,p,claim=await setup_v2(tmp_path)
        assert c.display(c.session)['sync_ready']
        if problem in ('attention','aborting'):e.active['phase']=problem
        elif problem=='target':e.native['recipes'][0]['recipe_brew_weight']=36000
        elif problem=='disabled':c.enabled=False;c.test_mode=False
        elif problem=='recovery':e.recovery=True
        elif problem=='maintenance':e.maintenance={'operation':'test'}
        else:
            rows=lifecycle(p,claim)
            rows[0]['cycle']=None
            for row in rows:c.accept('classic-de1',row)
            e.active=None;e.persist()
        result=c.display(c.session)
        assert result['grinder_connected'] and not result['sync_ready'] and result['sync_warning']
        await e.close();e.store.close()
    asyncio.run(run())


def test_maintenance_gate_atomic_and_new_grind_cancels_lease(tmp_path):
    async def run():
        e,c,r,p,claim=await setup_v2(tmp_path)
        e.active=None;e.persist();e.native['pending_brew']=None
        e.driver={'data':{'grindState':'Idle','gbsState':'Idle'}}
        assert idle_reason(e) is None
        assert command(e,{'command':'enter','operation':'a'*64})['entered']
        with pytest.raises(BridgeError):command(e,{'command':'enter','operation':'b'*64})
        assert command(e,{'command':'status'})['maintenance']['operation']=='a'*64
        e.brewratio({'SYNC_BEVERAGE_WEIGHT':45,'SYNC_FILTER':2})
        assert e.maintenance is None
        with pytest.raises(BridgeError):command(e,{'command':'enter','operation':'a'*64})
        await e.close();e.store.close()
    asyncio.run(run())
