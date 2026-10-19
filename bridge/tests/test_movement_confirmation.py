"""Delayed native movement is quiet; faults and persistent uncertainty stay visible."""
import asyncio
import copy
import time

import pytest

from engine import Engine
from controllers import Controllers
from result_analysis import MOVEMENT_CONFIRMATION_GRACE_S
from support import setup_v2, lifecycle, advance
from support import prepare_done


async def received_before_motion(tmp_path):
    e,c,r,p,claim = await setup_v2(tmp_path)
    for row in lifecycle(p,claim):c.accept('classic-de1',row)
    await advance(e,c);e.execute({'ID':9});await advance(e,c)
    e.mako();e.mako();await prepare_done(e)
    before = copy.deepcopy(r.driver)
    r.finish(e.mako())
    r.driver = before
    await e.refresh();e.verify()
    assert e.store.recent(1)[0]['outcome'] == 'movement_pending'
    # Native receipt closes the extraction cycle before movement is confirmed.
    e.active = None;e.persist()
    return e,c,r


def keep_connected(e,c,now):
    e.last_poll = e.last_native = c.last_event_received = now


def test_delayed_motion_stays_quiet_then_confirms_without_resending(tmp_path,monkeypatch):
    async def run():
        e,c,r = await received_before_motion(tmp_path)
        saved = e.store.recent(1)[0]
        now = [saved['movement_pending_since']]
        monkeypatch.setattr(time,'time',lambda:now[0])
        for elapsed in (0,4,12,36,44):
            now[0] = saved['movement_pending_since'] + elapsed
            keep_connected(e,c,now[0])
            await e.refresh();e.verify()
            display = c.display(c.session)
            assert display['sync_ready'] and display['sync_warning'] is None
            assert display['notice']['id'].endswith(':movement_checking')
            assert e.native_refresh_interval() == 4
            assert e.store.shot(saved['id'])['movement_pending_since'] == saved['movement_pending_since']
        r.driver['data'].update(ddValueDum=1244,agsaHallSensorCounts=234)
        await e.refresh();e.verify()
        assert e.store.shot(saved['id'])['outcome'] == 'adjusted'
        assert c.display(c.session)['notice']['id'].endswith(':adjusted')
        assert c.display(c.session)['sync_ready']
        assert e.native_refresh_interval() == 30
        assert len(r.state['brews']) == len(e.store.recent()) == 1
        assert e.mako()['MA_STATUS'] == 1
        await e.close();e.store.close()
    asyncio.run(run())


def test_persistent_uncertainty_deadline_survives_restart_and_later_resolves(tmp_path,monkeypatch):
    async def run():
        e,c,r = await received_before_motion(tmp_path)
        saved = e.store.recent(1)[0]
        now = [saved['movement_pending_since'] + 20]
        monkeypatch.setattr(time,'time',lambda:now[0])
        await e.close()
        restarted = Engine(e.store,r)
        c = restarted.controllers = Controllers(restarted, {'feedback_mode':'xenia_one_press','supervised_test':True})
        c.machine = {'reachable':True,'state':'Idle','substate':'ready'}
        keep_connected(restarted,c,now[0])
        await restarted.refresh();await restarted.reconcile();restarted.ready=True
        restarted.verify()
        assert restarted.store.shot(saved['id'])['movement_pending_since'] == saved['movement_pending_since']
        assert restarted.native_refresh_interval() == 4
        for elapsed in (MOVEMENT_CONFIRMATION_GRACE_S-0.01,MOVEMENT_CONFIRMATION_GRACE_S,90):
            now[0] = saved['movement_pending_since'] + elapsed
            keep_connected(restarted,c,now[0])
            await restarted.refresh();restarted.verify()
            due = elapsed >= MOVEMENT_CONFIRMATION_GRACE_S
            display = c.display(c.session)
            assert display['sync_ready'] is not due
            assert display['sync_warning'] == ('grinder_movement_unconfirmed' if due else None)
            assert display['notice']['id'].endswith(':movement_pending' if due else ':movement_checking')
        r.driver['data'].update(ddValueDum=1244,agsaHallSensorCounts=234)
        await restarted.refresh();restarted.verify()
        assert c.display(c.session)['sync_ready']
        assert restarted.store.shot(saved['id'])['outcome'] == 'adjusted'
        assert len(r.state['brews']) == len(e.store.recent()) == 1
        await restarted.close();e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('problem',['sensor_failed','motion_alert','connection','history_only','required_press'])
def test_real_problems_do_not_wait_for_movement_grace(tmp_path,problem):
    async def run():
        e,c,r = await received_before_motion(tmp_path)
        if problem == 'sensor_failed':
            r.driver['data']['ddSensorFailed'] = True
            await e.refresh();e.verify()
        elif problem == 'motion_alert':
            r.state['motion_alert_events'] = [{'alert_active':1,'alert_type_name':'AgsaBlockage'}]
            await e.refresh();e.verify()
        elif problem == 'connection':e.last_poll = time.time()-11
        else:
            record = e.store.controller_history(1)[0]
            record.pop('delivery_id')
            record.update(feedback_status='history_only' if problem == 'history_only' else 'awaiting_arm',
                reason='shot_not_sent' if problem == 'history_only' else 'grinder_press_required')
            e.store.save_controller_shot(record)
        display = c.display(c.session)
        assert not display['sync_ready'] and display['sync_warning']
        if problem in ('sensor_failed','motion_alert'):
            assert display['notice']['id'].endswith(':movement_pending')
        await e.close();e.store.close()
    asyncio.run(run())


def test_replaced_grind_ends_fast_checks_and_cannot_confirm_old_motion(tmp_path):
    async def run():
        e,c,r = await received_before_motion(tmp_path)
        saved = e.store.recent(1)[0]
        r.state['grinds'][0].update(grind_id=22,grind_uuid='replacement')
        r.driver['data'].update(ddValueDum=1244,agsaHallSensorCounts=234)
        await e.refresh();e.verify()
        assert e.store.shot(saved['id'])['outcome'] == 'movement_pending'
        assert e.native_refresh_interval() == 30
        assert len(r.state['brews']) == 1
        await e.close();e.store.close()
    asyncio.run(run())
