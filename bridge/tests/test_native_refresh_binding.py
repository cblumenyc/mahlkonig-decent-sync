"""A cache crossing six seconds must request a fresh read, never lose a valid shot."""
import asyncio
import time
from datetime import datetime, timezone, timedelta

import pytest

from controllers import Controllers
from engine import Engine
from support import event, profile, setup
from support import prepare_done


def stop_events(c, p, start_sequence=2):
    c.accept('classic-de1', event(start_sequence, 'preinfusion_complete', p=p, elapsed=7000))
    c.accept('classic-de1', event(start_sequence+1, 'shot_finished', p=p, elapsed=32763,
        measurement_source='scale', completion_reason='target_weight', quality_flags=[],
        pour_volume={'value':44.411, 'unit':'mL', 'source':'de1_pour_volume'},
        scale_mass={'value':34.8, 'unit':'g', 'source':'scale'}))
    c.accept('classic-de1', event(start_sequence+2, 'shot_finalized', p=p, elapsed=32763,
        quality_flags=[], scale_mass_basis='de1_final_espresso_weight',
        scale_mass={'value':35.9, 'unit':'g', 'source':'scale'}))


@pytest.mark.parametrize('finish_before_read', [False, True])
def test_slow_read_keeps_ack_and_polls_fast_then_delivers_once(tmp_path, finish_before_read):
    async def run():
        p = dict(profile(mode='scale'), adapter_version='classic-0.4')
        e, c, r = await setup(tmp_path, p=p)
        e.last_native = time.time()-6.152
        e.refresh_requested = False
        start = event(1, 'shot_started', p=p, measurement_source='scale')
        before = time.perf_counter()
        ack = c.accept('classic-de1', start)
        assert ack['durable'] and time.perf_counter()-before < .2
        original = e.store.controller_shot('classic-de1', 'shot1')['binding_pending']
        assert c.summary() == 'Verifying grind'
        assert c.accept('classic-de1', start)['duplicate']
        assert e.store.controller_shot('classic-de1', 'shot1')['binding_pending'] == original
        assert e.refresh_requested and not e.store.recent()
        held = asyncio.Event()
        reading = asyncio.Event()
        ordinary_read = r.read
        async def delayed_read():
            reading.set()
            await held.wait()
            return await ordinary_read()
        r.read = delayed_read
        task = asyncio.create_task(e.refresh())
        await reading.wait()
        before = time.perf_counter()
        assert e.mako()['MA_STATUS'] == 3
        assert time.perf_counter()-before < .2
        if finish_before_read:
            stop_events(c, p)
            await c.dispatch()
            assert e.store.controller_shot('classic-de1', 'shot1')['feedback_status'] == 'awaiting_native_validation'
            assert not e.store.recent()
        held.set()
        await task
        r.read = ordinary_read
        record = e.store.controller_shot('classic-de1', 'shot1')
        assert not record.get('binding_pending')
        assert record['binding']['grind_uuid'] == original['grind_uuid']
        assert record['binding_validation']['initial_native_age_s'] >= 6.152
        if not finish_before_read:stop_events(c, p)
        await c.dispatch();await prepare_done(e)
        result = e.mako()
        assert result['MA_STATUS'] == 4
        assert result['REAL_EXTRACTION_TIME_MS'] == 25763
        assert result['PU_SENS_FLOW_METER_VOLUME'] == 35.9
        r.finish(result);await e.refresh();e.verify()
        await c.dispatch()
        assert len(e.store.recent()) == 1
        assert e.store.recent()[0]['state'] == 'verified'
        e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['timeout', 'read_error', 'pending_removed', 'new_grind', 'recipe_changed',
    'profile_changed', 'cycle_changed', 'session_changed', 'mode_changed', 'restart', 'abort', 'no_press'])
def test_refresh_cannot_rebind_or_bypass_delivery_guards(tmp_path, fault):
    async def run():
        p = dict(profile(mode='scale'), adapter_version='classic-0.4')
        e, c, r = await setup(tmp_path, p=p)
        e.last_native = time.time()-6.152
        if fault == 'no_press':e.active['starts'] = 0
        c.accept('classic-de1', event(1, 'shot_started', p=p, measurement_source='scale'))
        if fault == 'read_error':
            r.fail = True
            with pytest.raises(OSError):await e.refresh()
            assert e.store.controller_shot('classic-de1','shot1')['binding_pending']
            r.fail = False
        if fault in ('timeout', 'read_error'):
            record = e.store.controller_shot('classic-de1', 'shot1')
            record['binding_pending']['deadline'] = time.time()-1
            e.store.save_controller_shot(record)
            await c.dispatch()
        if fault == 'pending_removed':r.state['pending_brew'] = None
        if fault == 'new_grind':r.state['grinds'][0]['grind_uuid'] = 'newer-grind'
        if fault == 'recipe_changed':r.state['recipes'][0]['recipe_brew_weight'] = 36000
        if fault == 'profile_changed':c.observed_profile = profile(36, 'new-revision', 'scale')
        if fault == 'cycle_changed':e.active['cycle_id'] = 'replacement-cycle'
        if fault == 'session_changed':c.session = 'replacement-session'
        if fault == 'mode_changed':c.mode = 'xenia_completion_only'
        if fault == 'restart':
            e = Engine(e.store, r)
            c = e.controllers = Controllers(e, {'feedback_mode':'xenia_one_press','supervised_test':True})
        if fault == 'abort':e.active['phase'] = 'aborting'
        await e.refresh()
        stop_events(c, p)
        await c.dispatch()
        record = e.store.controller_shot('classic-de1', 'shot1')
        assert record['feedback_status'] == 'history_only'
        assert not record.get('binding_pending')
        assert record['reason'] not in (None, 'waiting_for_final_scale_mass', 'waiting_for_fresh_grinder_state')
        if fault in ('timeout', 'read_error'):assert record['reason'] == 'grinder_state_refresh_timeout'
        if fault == 'restart':assert record['reason'] == 'bridge_restarted_before_grind_validation'
        assert not e.store.recent()
        assert e.mako()['PU_SENS_FLOW_METER_VOLUME'] == 0
        e.store.close()
    asyncio.run(run())


def test_second_shot_cannot_take_reserved_grind_and_old_events_never_reserve(tmp_path):
    async def run():
        e, c, r = await setup(tmp_path)
        e.last_native = time.time()-7
        delayed = event(1, 'shot_started', shot='old-shot')
        delayed['occurred_at'] = (datetime.now(timezone.utc)-timedelta(seconds=131)).isoformat()
        c.accept('classic-de1', delayed)
        old = e.store.controller_shot('classic-de1', 'old-shot')
        assert old['reason'] == 'delayed_or_reordered_start' and not old.get('binding_pending')
        c.accept('classic-de1', event(2, 'shot_started'))
        c.accept('classic-de1', event(3, 'shot_started', shot='another-shot'))
        other = e.store.controller_shot('classic-de1', 'another-shot')
        assert other['reason'] == 'grind_already_bound_to_another_shot'
        await e.refresh()
        assert e.store.controller_shot('classic-de1', 'shot1')['binding']
        assert not e.store.controller_shot('classic-de1', 'another-shot')['binding']
        e.store.close()
    asyncio.run(run())
