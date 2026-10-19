"""End-to-end cycle facts, event order, late arming, and unexposed restart recovery."""
from support import setup_v2, lifecycle, advance
import asyncio
import copy
import time
from datetime import datetime, timezone, timedelta

import pytest
from controllers import Controllers
from engine import Engine
from support import prepare_done


@pytest.mark.parametrize('press_at', [0,1,2,3,4])
def test_press_before_during_after_shot_preserves_original_measurements(tmp_path, press_at):
    async def run():
        e,c,r,p,claim=await setup_v2(tmp_path)
        rows=lifecycle(p,claim)
        for i,row in enumerate(rows):
            if i == press_at:e.execute({'ID':9})
            c.accept('classic-de1',row)
        await advance(e,c)
        if press_at==4:
            rec=e.store.controller_shot('classic-de1','shot1')
            assert rec['feedback_status']=='awaiting_arm'
            assert 'Shot saved' in c.summary()
            assert not e.store.recent()
            e.execute({'ID':9})
            initial=copy.deepcopy(e.active['arming'])
            e.execute({'ID':9})
            assert e.active['arming']==initial
            await advance(e,c)
        e.mako();e.mako()
        await prepare_done(e)
        result=e.mako()
        assert result['MA_STATUS']==4
        assert result['REAL_EXTRACTION_TIME_MS']==25763
        assert result['PU_SENS_FLOW_METER_VOLUME']==35.9
        r.finish(result);await e.refresh();e.verify()
        assert e.store.recent()[0]['state']=='verified'
        assert len(e.store.recent())==1
        assert c.accept('classic-de1',rows[-1])['duplicate']
        assert e.store.cycle(claim['cycle_id'])['grind_uuid']==claim['grind_uuid']
        await e.close();e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('order', [(0,1,2,3),(3,2,1,0),(2,0,3,1)])
def test_identity_survives_delayed_reordered_events_and_old_cache(tmp_path,order):
    async def run():
        e,c,r,p,claim=await setup_v2(tmp_path)
        # This is transport delay, not a different grind. No six-second cache gate.
        rows=lifecycle(p,claim)
        for row in rows:row['occurred_at']=(datetime.now(timezone.utc)-timedelta(seconds=100)).isoformat()
        # Native grind predates extraction and remains pending.
        gtime=(datetime.now(timezone.utc)-timedelta(seconds=120)).isoformat()
        e.active['grind_time']=gtime
        r.state['grinds'][0]['grind_date_time']=gtime
        e.last_native=time.time()-40
        for i in order:c.accept('classic-de1',rows[i])
        rec=e.store.controller_shot('classic-de1','shot1')
        assert rec['binding']['cycle_id']==claim['cycle_id']
        await advance(e,c)
        assert e.store.controller_shot('classic-de1','shot1')['feedback_status']=='awaiting_arm'
        await e.close();e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['expired','replacement','pending_removed','manual','target','claim','null_claim','duplicate_shot','session'])
def test_late_press_cannot_resurrect_wrong_or_invalid_cycle(tmp_path,fault):
    async def run():
        e,c,r,p,claim=await setup_v2(tmp_path)
        rows=lifecycle(p,claim)
        if fault=='manual':rows[2]['data']['completion_reason']='manual_or_unknown'
        if fault=='claim':rows[0]['cycle']['cycle_id']='different'
        if fault=='null_claim':rows[0]['cycle']=None
        if fault=='session':rows[0]['session_id']='other'
        for row in rows:c.accept('classic-de1',row)
        await advance(e,c)
        if fault=='expired':e.active['expires_at']=time.time()-1
        if fault=='replacement':r.state['grinds'][0]['grind_uuid']='replacement'
        if fault=='pending_removed':r.state['pending_brew']=None
        if fault=='target':r.state['recipes'][0]['recipe_brew_weight']=36000
        if fault=='duplicate_shot':
            other=copy.deepcopy(rows[0]);other.update(event_id='other',sequence=10,shot_id='other-shot')
            c.accept('classic-de1',other)
            assert e.store.controller_shot('classic-de1','other-shot')['binding'] is None
            await e.close();e.store.close();return
        if fault=='manual':
            from grinder_client import BridgeError
            with pytest.raises(BridgeError):e.execute({'ID':9})
        else:e.execute({'ID':9})
        await advance(e,c)
        assert e.store.controller_shot('classic-de1','shot1')['feedback_status']=='history_only'
        assert not e.store.recent()
        assert e.mako()['PU_SENS_FLOW_METER_VOLUME']==0
        await e.close();e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('stage',['matched','awaiting_arm','queued','validating','exposed'])
def test_restart_resumes_only_unexposed_cycle_facts(tmp_path,stage):
    async def run():
        e,c,r,p,claim=await setup_v2(tmp_path)
        rows=lifecycle(p,claim)
        for row in rows[:1] if stage=='matched' else rows:c.accept('classic-de1',row)
        await advance(e,c)
        if stage in ('queued','validating','exposed'):
            e.execute({'ID':9});await e.refresh()
            rec=e.store.controller_shot('classic-de1','shot1');c.cycles.advance(rec);e.store.save_controller_shot(rec)
        if stage in ('validating','exposed'):
            await c.dispatch()
        if stage=='exposed':
            e.mako();e.mako();await prepare_done(e);assert e.mako()['MA_STATUS']==4
        await e.close()
        e=Engine(e.store,r);c=e.controllers=Controllers(e,{'feedback_mode':'xenia_one_press','supervised_test':True})
        c.machine={'reachable':True,'state':'Idle'};c.last_event_received=time.time()
        await e.refresh();await e.reconcile();e.ready=True;e.mako()
        if stage=='exposed':
            assert e.store.recent()[0]['state']=='uncertain'
            assert e.mako()['PU_SENS_FLOW_METER_VOLUME']==0
        else:
            if stage=='matched':
                for row in rows[1:]:c.accept('classic-de1',row)
            if stage in ('matched','awaiting_arm'):e.execute({'ID':9})
            await advance(e,c);e.mako();e.mako();await prepare_done(e)
            assert e.mako()['PU_SENS_FLOW_METER_VOLUME']==35.9
            assert len(e.store.recent())==1
        await e.close();e.store.close()
    asyncio.run(run())
