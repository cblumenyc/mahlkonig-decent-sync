from support import profile, event, setup, complete, Tablet
import asyncio
import copy
from datetime import datetime, timezone, timedelta
import time
import uuid

import pytest

from support import FakeReader, prepare_done
from controllers import Controllers
from engine import Engine
from grinder_client import BridgeError
from store import Store


@pytest.mark.parametrize('fault', [None, 'missing', 'flags', 'session', 'elapsed', 'profile',
                                  'late', 'null', 'wrong_unit', 'restart', 'timeout'])
def test_scale_finalization_waits_for_cup_mass_without_extending_extraction(tmp_path, fault):
    async def run():
        p = profile(mode='scale')
        p['adapter_version'] = 'classic-0.4'
        e, c, r = await setup(tmp_path, mode='xenia_one_press', p=p)
        c.accept('classic-de1', event(1, 'shot_started', p=p, measurement_source='scale'))
        c.accept('classic-de1', event(2, 'preinfusion_complete', p=p, elapsed=7000))
        stopped = event(3, 'shot_finished', p=p, elapsed=27095, measurement_source='scale',
            completion_reason='target_weight', quality_flags=[], scale_mass_basis='extraction_stop',
            pour_volume={'value':44.54, 'unit':'mL', 'source':'de1_pour_volume'},
            scale_mass={'value':35, 'unit':'g', 'source':'scale'})
        c.accept('classic-de1', stopped)
        await c.dispatch()
        record = e.store.controller_shot('classic-de1', 'shot1')
        assert record['feedback_status'] == 'awaiting_final_mass'
        assert record['measurements']['scale_mass_g'] is None
        assert record['measurements']['scale_mass_at_stop_g'] == 35
        assert not e.store.recent()
        if fault == 'missing':
            e.store.close()
            return
        if fault == 'timeout':
            record['events']['shot_finished']['occurred_at'] = (datetime.now(timezone.utc)-timedelta(seconds=21)).isoformat()
            e.store.save_controller_shot(record)
            await c.dispatch()
            assert e.store.controller_shot('classic-de1','shot1')['reason'] == 'final_scale_timeout'
            assert not e.store.recent()
            e.store.close()
            return
        if fault == 'restart':
            c = e.controllers = Controllers(e, {'feedback_mode':'xenia_one_press','supervised_test':True})
            c.machine = {'reachable':True,'state':'Idle','substate':'ready'}
        finalized = event(4, 'shot_finalized', p=p, elapsed=27095, quality_flags=[],
            scale_mass_basis='de1_final_espresso_weight', scale_mass={'value':36.2,'unit':'g','source':'scale'})
        if fault == 'flags': finalized['data']['quality_flags'] = ['missing_final_scale_mass']
        if fault == 'session': finalized['session_id'] = 'session2'
        if fault == 'elapsed': finalized['data']['elapsed_ms'] += 5000
        if fault == 'profile': finalized['data']['profile'] = profile(value=36, revision='changed', mode='scale')
        if fault == 'late': finalized['occurred_at'] = (datetime.now(timezone.utc)+timedelta(seconds=25)).isoformat()
        if fault == 'null': finalized['data']['scale_mass'] = None
        if fault == 'wrong_unit': finalized['data']['scale_mass']['unit'] = 'mL'
        c.accept('classic-de1', finalized)
        assert c.accept('classic-de1', finalized)['duplicate'] is True
        await c.dispatch()
        record = e.store.controller_shot('classic-de1', 'shot1')
        if fault is None:
            assert record['feedback_status'] == 'journaled'
            assert record['delivery_input']['weight_g'] == 36.2
            assert record['delivery_input']['duration_ms'] == 20095
            assert record['measurements']['scale_mass_at_stop_g'] == 35
            assert record['measurements']['scale_mass_basis'] == 'finalized'
            assert len(e.store.recent()) == 1
        else:
            assert record['feedback_status'] == 'history_only'
            assert not e.store.recent()
        e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('scenario',['scale','absent','lost','reconnected','missing_actual','bad_stop','no_reason','at_start_only','manual','backup_mismatch'])
def test_automatic_measurement_fallback_has_explicit_source_and_matching_stop(tmp_path,scenario):
    async def run():
        p=profile(mode='auto')
        p.update(fallback_target={'value':50 if scenario=='backup_mismatch' else 45,'unit':'mL'},
                 fallback_policy='at_start' if scenario=='at_start_only' else 'during_shot')
        e,c,r=await setup(tmp_path,mode='xenia_one_press',p=p)
        initial='volume_estimate' if scenario=='absent' else 'scale'
        source='scale' if scenario in ('scale','missing_actual','backup_mismatch') else 'volume_estimate'
        reason=None if source=='scale' or scenario=='no_reason' else ('scale_unavailable_at_start' if scenario=='absent' else 'scale_lost_during_shot')
        c.accept('classic-de1',event(1,'shot_started',p=p,measurement_source=initial))
        c.accept('classic-de1',event(2,'preinfusion_complete',p=p,elapsed=4000))
        actual={'value':44.9,'unit':'g','source':'scale'} if scenario in ('scale','reconnected') else None
        stop='manual_or_unknown' if scenario=='manual' else ('target_weight' if source=='scale' or scenario=='bad_stop' else 'target_volume')
        c.accept('classic-de1',event(3,'shot_finished',p=p,elapsed=28000,completion_reason=stop,quality_flags=[],
            measurement_source=source,fallback_reason=reason,pour_volume={'value':45.5,'unit':'mL','source':'de1_pour_volume'},scale_mass=actual))
        await c.dispatch()
        shot=e.store.controller_shot('classic-de1','shot1')
        if scenario in ('scale','absent','lost','reconnected'):
            assert shot['feedback_status']=='journaled'
            assert shot['delivery_input']['weight_g']==(44.9 if source=='scale' else 45.5)
            assert shot['measurements']['selected_source']==source
            assert shot['measurements']['fallback_reason']==reason
            assert shot['measurements']['scale_mass_g']==(44.9 if actual else None)
        else:
            assert shot['feedback_status']=='history_only'
            assert not e.store.recent()
        e.store.close()
    asyncio.run(run())


def test_automatic_result_keeps_measurements_and_native_verification(tmp_path):
    async def run():
        e,c,r = await setup(tmp_path)
        events = complete(c)
        assert c.accept('classic-de1', events[-1])['duplicate']
        await c.dispatch();await prepare_done(e)
        result=e.mako()
        assert result['MA_STATUS']==4
        assert result['REAL_EXTRACTION_TIME_MS']==24000
        assert result['PU_SENS_FLOW_METER_VOLUME']==45.5
        rec=e.store.controller_shot('classic-de1','shot1')
        assert rec['measurements']['estimate_calibrated'] is False
        assert rec['measurements']['scale_mass_g'] is None
        assert rec['binding']['targets']['yield_g']==45
        r.finish(result);await e.refresh();e.verify()
        assert e.store.shot(rec['delivery_id'])['state']=='verified'
        assert len(e.store.recent())==1
        e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['manual','missing_volume','wrong_unit','bad_quality','profile_change',
                                  'missing_scale','expired','new_grind','no_press','offline','recipe_change'])
def test_ineligible_shots_never_expose_feedback(tmp_path, fault):
    async def run():
        p=profile(mode='scale') if fault=='missing_scale' else profile()
        e,c,r=await setup(tmp_path,p=p)
        e1=event(1,'shot_started',p=p);c.accept('classic-de1',e1)
        c.accept('classic-de1',event(2,'preinfusion_complete',p=p,elapsed=4000))
        data={'completion_reason':'target_weight' if fault=='missing_scale' else 'target_volume',
              'quality_flags':[], 'pour_volume':{'value':45,'unit':'mL','source':'de1_pour_volume'},'scale_mass':None}
        if fault=='manual':data['completion_reason']='manual_or_unknown'
        if fault=='missing_volume':data['pour_volume']=None
        if fault=='wrong_unit':data['pour_volume']['unit']='g'
        if fault=='bad_quality':data['quality_flags']=['lost_connection']
        if fault=='profile_change':p=profile(36,'rev2')
        if fault=='expired':
            rec=e.store.controller_shot('classic-de1','shot1')
            rec['binding']['grind_time']=(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()
            e.store.save_controller_shot(rec)
        if fault=='new_grind':e.native['grinds'][0]['grind_uuid']=str(uuid.uuid4())
        if fault=='no_press':e.active['starts']=0
        if fault=='offline':c.machine['reachable']=False
        if fault=='recipe_change':e.native['recipes'][0]['recipe_amount']=18000
        c.accept('classic-de1',event(3,'shot_finished',p=p,elapsed=28000,**data))
        await c.dispatch()
        assert not e.store.recent()
        assert e.store.controller_shot('classic-de1','shot1')['feedback_status']=='history_only'
        assert e.mako()['MA_STATUS']!=4
        e.store.close()
    asyncio.run(run())


def test_target_override_change_requires_native_match_not_new_commission(tmp_path):
    async def run():
        e,c,r=await setup(tmp_path)
        c.accept('classic-de1',event(1,'profile',p=profile(36,'rev2')))
        assert c.targets()['reason']=='set_grinder_targets'
        assert c.targets()['yield_g']==36
        r.state['recipes'][0]['recipe_brew_weight']=36000
        await e.refresh()
        assert c.targets()['confirmed']
        e.store.close()
    asyncio.run(run())


def test_event_identity_and_nested_transaction_rollback(tmp_path):
    async def run():
        e,c,r=await setup(tmp_path)
        original=event(1,'shot_started');c.accept('classic-de1',original)
        changed=copy.deepcopy(original);changed['data']['elapsed_ms']=1
        with pytest.raises(BridgeError):c.accept('classic-de1',changed)
        changed['event_id']='another-id'
        with pytest.raises(BridgeError):c.accept('classic-de1',changed)
        count=e.store.db.execute('SELECT count(*) FROM controller_events').fetchone()[0]
        assert count==2
        with pytest.raises(RuntimeError), e.store.atomic():
            e.store.set('should_not_commit',True)
            e.store.event('also_should_not_commit')
            raise RuntimeError()
        assert e.store.get('should_not_commit') is None
        e.store.close()
    asyncio.run(run())


def test_reordered_start_and_missing_preinfusion_are_history_only(tmp_path):
    async def run():
        e,c,r=await setup(tmp_path)
        finish=event(3,'shot_finished',elapsed=28000,completion_reason='target_volume',quality_flags=[],
                     pour_volume={'value':45,'unit':'mL','source':'de1_pour_volume'},scale_mass=None)
        c.accept('classic-de1',finish)
        c.accept('classic-de1',event(1,'shot_started'))
        c.accept('classic-de1',event(2,'preinfusion_complete',elapsed=4000))
        await c.dispatch()
        assert not e.store.recent()
        assert e.store.controller_shot('classic-de1','shot1')['binding'] is None
        e.store.close()
    asyncio.run(run())


def test_restart_at_saved_and_exposed_phases_never_replays(tmp_path):
    async def run():
        e,c,r=await setup(tmp_path)
        complete(c)
        restarted=Controllers(e, {'feedback_mode':'xenia_one_press','supervised_test':True})
        assert e.store.controller_shot('classic-de1','shot1')['reason']=='bridge_restarted_before_delivery'
        await restarted.dispatch();assert not e.store.recent()
        e.store.close()
    asyncio.run(run())


def test_changes_between_validation_and_poll_block_feedback(tmp_path):
    async def run():
        e,c,r=await setup(tmp_path)
        complete(c);await c.dispatch();await prepare_done(e)
        assert e.active['phase']=='finishing'
        c.accept('classic-de1',event(4,'profile',p=profile(36,'rev2')))
        assert e.mako()['MA_STATUS']!=4
        assert e.store.recent()[0]['state']=='rejected'
        e.store.close()
    asyncio.run(run())


def test_wake_fresh_grind_once_and_never_purge_or_replay(tmp_path):
    async def run():
        e,c,r=await setup(tmp_path)
        t=c.adapter=Tablet();c.wake_enabled=True
        await c.poll_machine();assert t.wakes==0
        g=copy.deepcopy(r.state['grinds'][0]);g.update(grind_id=22,grind_uuid=str(uuid.uuid4()))
        r.state['grinds'].insert(0,g);await e.refresh()
        c.last_adapter_check=0;await c.poll_machine();assert t.wakes==1
        c.last_adapter_check=0;await c.poll_machine();assert c.wake['state']=='ready'
        await e.refresh();c.last_adapter_check=0;await c.poll_machine();assert t.wakes==1
        g=copy.deepcopy(g);g.update(grind_id=23,grind_uuid=str(uuid.uuid4()),recipe_type_id=4)
        r.state['grinds'].insert(0,g);await e.refresh()
        c.last_adapter_check=0;await c.poll_machine();assert t.wakes==1
        e.store.close()
    asyncio.run(run())


def test_production_cannot_enable_unverified_transport(tmp_path):
    e=Engine(Store(tmp_path/'test.db'),FakeReader())
    for config in ({'enabled':True}, {'supervised_test':True}, {}):
        with pytest.raises(ValueError):Controllers(e,{**config,'feedback_mode':'xenia_completion_only'})
    with pytest.raises(ValueError):Controllers(e,{'feedback_mode':'cloud'})
    e.store.close()


def test_history_measurements_are_kept_without_a_match_or_automatic_stop(tmp_path):
    async def run():
        e,c,r=await setup(tmp_path)
        e.active=None
        c.accept('classic-de1',event(1,'shot_started'))
        c.accept('classic-de1',event(2,'preinfusion_complete',elapsed=4000))
        c.accept('classic-de1',event(3,'shot_finished',elapsed=13000,completion_reason='manual_or_unknown',
            quality_flags=[],pour_volume={'value':15,'unit':'mL','source':'de1_pour_volume'},
            scale_mass={'value':12.5,'unit':'g','source':'scale'}))
        rec=e.store.controller_shot('classic-de1','shot1')
        assert rec['feedback_status']=='history_only'
        assert rec['measurements']['pour_volume_ml']==15
        assert rec['measurements']['estimated_mass_g']==15
        assert rec['measurements']['scale_mass_g']==12.5
        assert rec['measurements']['duration_ms']==9000
        assert not e.store.recent()
        e.store.close()
    asyncio.run(run())


def test_scale_mode_delivers_measured_grams_and_keeps_volume_separate(tmp_path):
    async def run():
        p=profile(mode='scale');e,c,r=await setup(tmp_path,p=p)
        c.accept('classic-de1',event(1,'shot_started',p=p))
        c.accept('classic-de1',event(2,'preinfusion_complete',p=p,elapsed=4000))
        c.accept('classic-de1',event(3,'shot_finished',p=p,elapsed=28000,completion_reason='target_weight',
            quality_flags=[],pour_volume={'value':48,'unit':'mL','source':'de1_pour_volume'},
            scale_mass={'value':44.7,'unit':'g','source':'scale'}))
        await c.dispatch();await prepare_done(e)
        assert e.mako()['PU_SENS_FLOW_METER_VOLUME']==44.7
        rec=e.store.controller_shot('classic-de1','shot1')
        assert rec['measurements']['estimated_mass_g']==48
        assert rec['measurements']['scale_mass_g']==44.7
        e.store.close()
    asyncio.run(run())


def test_retired_session_cannot_take_control_and_delayed_start_is_history_only(tmp_path):
    async def run():
        e,c,r=await setup(tmp_path)
        first=event(1,'profile');c.accept('classic-de1',first)
        newer=event(1,'profile');newer.update(session_id='new-session',event_id='new-session-1')
        c.accept('classic-de1',newer)
        old=event(2,'shot_started');c.accept('classic-de1',old)
        assert c.session=='new-session'
        assert e.store.controller_shot('classic-de1','shot1')['binding'] is None
        e.store.close()
    asyncio.run(run())


def test_warmup_coalesces_wake_and_uncertain_request_is_not_replayed(tmp_path):
    class AmbiguousTablet(Tablet):
        async def wake(self):
            self.wakes+=1
            raise TimeoutError()
    async def run():
        e,c,r=await setup(tmp_path)
        c.adapter=t=AmbiguousTablet();c.wake_enabled=True
        c.wake={'state':'requested','created':time.time(),'grind_uuid':'new'}
        await c.poll_machine();assert t.wakes==1 and c.wake['state']=='uncertain'
        c.last_adapter_check=0;await c.poll_machine();assert t.wakes==1
        t.state='Idle';c.last_adapter_check=0;await c.poll_machine();assert c.wake['state']=='ready'
        c.wake={'state':'warming','created':time.time()-300,'grind_uuid':'old'}
        t.state='Sleep';c.last_adapter_check=0;await c.poll_machine();assert c.wake['state']=='expired'
        assert t.wakes==1
        e.store.close()
    asyncio.run(run())


def test_cycle_stays_ineligible_after_profile_changes_back(tmp_path):
    async def run():
        e,c,r=await setup(tmp_path)
        c.accept('classic-de1',event(1,'profile',p=profile(36,'rev2')))
        c.accept('classic-de1',event(2,'profile',p=profile()))
        c.accept('classic-de1',event(3,'shot_started'))
        rec=e.store.controller_shot('classic-de1','shot1')
        assert rec['binding'] is None and rec['reason']=='effective_profile_changed_during_cycle'
        e.store.close()
    asyncio.run(run())


def test_targets_must_already_match_the_recorded_grind(tmp_path):
    async def run():
        e,c,r=await setup(tmp_path)
        e.native['grinds'][0]['recipe_brew_weight']=36000
        c.accept('classic-de1',event(1,'shot_started'))
        assert e.store.controller_shot('classic-de1','shot1')['reason']=='grind_predates_confirmed_recipe'
        e.store.close()
    asyncio.run(run())


def test_failed_exposure_commit_cannot_skip_durable_intent_on_next_poll(tmp_path, monkeypatch):
    async def run():
        e,c,r=await setup(tmp_path)
        complete(c);await c.dispatch();await prepare_done(e)
        original=e.store.update_shot
        def fail(*args,**kwargs):raise OSError('simulated journal failure')
        monkeypatch.setattr(e.store,'update_shot',fail)
        with pytest.raises(OSError):e.mako()
        assert not e.active['exposed']
        assert not e.store.get('active')['exposed']
        monkeypatch.setattr(e.store,'update_shot',original)
        assert e.mako()['MA_STATUS']==4
        assert e.store.get('active')['exposed']
        e.store.close()
    asyncio.run(run())
