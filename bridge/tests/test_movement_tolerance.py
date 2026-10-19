import asyncio
import copy

import pytest

from engine import Engine
from result_analysis import small_change_within_tolerance
from support import fixture, payload, prepare_done
from support import setup_v2, lifecycle, advance


def evidence():
    driver = {'data': {'ddValueDum': 999, 'agsaHallSensorCounts': 100,
        'ddSensorFailed': False, 'agsaStatus': 'Normal', 'grindState': 'Idle', 'gbsState': 'Idle'}}
    return driver, copy.deepcopy(driver), {'pending_brew': None, 'motion_alert_events': []}


@pytest.mark.parametrize('target,position,expected', [
    (997,999,True), (993,1003,True), (992,999,False), (1003,999,False),
    (997,1008,False), (997,986,False), (1013,1003,True), (1014,1004,False)])
def test_tiny_target_and_position_boundaries(target,position,expected):
    before,after,native=evidence()
    before['data']['ddValueDum']=after['data']['ddValueDum']=position
    assert small_change_within_tolerance(1003,target,before,after,native) is expected


@pytest.mark.parametrize('problem', ['failed_sensor','missing_sensor','missing_hall','nan','infinity','boolean',
    'before_far','before_sensor_failed','actuator_fault','actuator_unknown','grinding','gbs_busy',
    'active_alert','unknown_alert','missing_alerts','pending','missing_pending'])
def test_tolerance_never_hides_missing_evidence_or_native_problem(problem):
    before,after,native=evidence()
    d=after['data']
    if problem=='failed_sensor':d['ddSensorFailed']=True
    elif problem=='missing_sensor':del d['ddSensorFailed']
    elif problem=='missing_hall':del d['agsaHallSensorCounts']
    elif problem=='nan':d['ddValueDum']=float('nan')
    elif problem=='infinity':d['agsaHallSensorCounts']=float('inf')
    elif problem=='boolean':d['ddValueDum']=True
    elif problem=='before_far':before['data']['ddValueDum']=1010
    elif problem=='before_sensor_failed':before['data']['ddSensorFailed']=True
    elif problem=='actuator_fault':d['agsaStatus']='Fault'
    elif problem=='actuator_unknown':del d['agsaStatus']
    elif problem=='grinding':d['grindState']='Grinding'
    elif problem=='gbs_busy':d['gbsState']='Brewing'
    elif problem=='active_alert':native['motion_alert_events']=[{'alert_active':1}]
    elif problem=='unknown_alert':native['motion_alert_events']=[{}]
    elif problem=='missing_alerts':del native['motion_alert_events']
    elif problem=='pending':native['pending_brew']={'grindId':99}
    elif problem=='missing_pending':del native['pending_brew']
    assert not small_change_within_tolerance(1003,997,before,after,native)


def test_latest_pending_result_reclassified_after_restart_without_resending(tmp_path):
    async def run():
        e,r=await fixture(tmp_path)
        before,after,_=evidence()
        r.driver=before
        r.state['recipes'][0]['recipe_gbs_data']['coursenessDeciMym']=1003
        await e.refresh()
        rec=await e.submit(payload(e));await prepare_done(e)
        r.finish(e.mako())
        r.state['recipes'][0]['recipe_gbs_data']['coursenessDeciMym']=997
        r.driver=after
        await e.refresh();e.verify()
        saved=e.store.shot(rec['id'])
        assert saved['outcome']=='within_tolerance'
        assert saved['assessment']['physical_movement_observed'] is False
        assert saved['assessment']['within_target_tolerance'] is True
        # Simulate the persisted pre-update warning. Restart only reads native
        # history and sensors; it must not send a second result.
        e.store.update_shot(rec['id'],outcome='movement_pending')
        await e.close()
        e2=Engine(e.store,r)
        await e2.refresh();await e2.reconcile();e2.ready=True;e2.verify()
        assert e2.store.shot(rec['id'])['outcome']=='within_tolerance'
        assert len(r.state['brews'])==1 and len(e2.store.recent())==1
        assert e2.active is None and e2.mako()['MA_STATUS']==1
        await e2.close();e2.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('outcome,ready,detail', [
    ('within_tolerance',True,'within tolerance'),
    ('movement_pending',False,'movement unconfirmed')])
def test_tablet_result_and_warning_distinguish_tolerance(tmp_path,outcome,ready,detail):
    async def run():
        e,c,r,p,claim=await setup_v2(tmp_path)
        for row in lifecycle(p,claim):c.accept('classic-de1',row)
        await advance(e,c);e.execute({'ID':9});await advance(e,c)
        e.mako();e.mako();await prepare_done(e)
        r.finish(e.mako());await e.refresh();e.verify()
        rec=e.store.recent(1)[0]
        e.store.update_shot(rec['id'],outcome=outcome)
        result=c.display(c.session)
        assert result['sync_ready'] is ready
        assert detail in result['notice']['detail'].lower()
        assert 'prompt' not in result['notice']['detail']
        await e.close();e.store.close()
    asyncio.run(run())
