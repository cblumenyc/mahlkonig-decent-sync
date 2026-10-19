import asyncio
import copy
import json
import time

import pytest

from support import event, profile, setup
from support import fixture, payload, prepare_done


@pytest.mark.parametrize('failure', ['stale_native_state', 'no_fresh_correlated_grind'])
def test_final_scale_weight_preserves_original_binding_failure(tmp_path, failure):
    async def run():
        p = profile(mode='scale')
        p['adapter_version'] = 'classic-0.4'
        e, c, r = await setup(tmp_path, p=p)
        if failure == 'stale_native_state':
            e.recovery = True
        else:
            e.active['phase'] = 'attention'
        c.accept('classic-de1', event(1, 'shot_started', p=p, measurement_source='scale'))
        c.accept('classic-de1', event(2, 'preinfusion_complete', p=p, elapsed=7000))
        c.accept('classic-de1', event(3, 'shot_finished', p=p, elapsed=23000,
            measurement_source='scale', completion_reason='target_weight', quality_flags=[],
            pour_volume={'value':47.6, 'unit':'mL', 'source':'de1_pour_volume'},
            scale_mass={'value':35.1, 'unit':'g', 'source':'scale'}))
        record = e.store.controller_shot('classic-de1', 'shot1')
        assert record['feedback_status'] == 'history_only'
        assert record['reason'] == failure
        assert record['binding_failure']['reason'] == failure
        finalized = event(4, 'shot_finalized', p=p, elapsed=23000, quality_flags=[],
            scale_mass_basis='de1_final_espresso_weight', scale_mass={'value':36.7, 'unit':'g', 'source':'scale'})
        c.accept('classic-de1', finalized)
        c.accept('classic-de1', finalized)
        await c.dispatch()
        record = e.store.controller_shot('classic-de1', 'shot1')
        assert record['reason'] == failure
        assert record['feedback_status'] == 'history_only'
        assert record['measurements']['scale_mass_g'] == 36.7
        assert not e.store.recent()
        rows = e.store.db.execute("SELECT data FROM events WHERE kind='shot_binding_rejected'").fetchall()
        assert len(rows) == 1 and json.loads(rows[0][0])['reason'] == failure
        e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('blocked', [None, 'pending', 'young', 'exposed', 'driver_unavailable',
                                    'driver_busy', 'delivery', 'stale_native', 'armed'])
def test_only_expired_unsent_native_idle_waits_clear(tmp_path, blocked):
    async def run():
        e, r = await fixture(tmp_path)
        if blocked == 'delivery':
            await e.submit(payload(e))
            await prepare_done(e)
        e.active.update(phase='attention', created=time.time()-600)
        e.persist()
        original = copy.deepcopy(e.active)
        r.state['pending_brew'] = None
        r.driver['data'].update(gbsState='Idle', grindState='Idle')
        if blocked == 'pending': r.state['pending_brew'] = {'grinduuid': original['grind_uuid']}
        if blocked == 'young': e.active['created'] = time.time()-100
        if blocked == 'exposed': e.active['exposed'] = True
        if blocked == 'driver_unavailable': r.driver = {}
        if blocked == 'driver_busy': r.driver['data']['grindState'] = 'Grinding'
        if blocked == 'armed': e.active['phase'] = 'armed'
        if blocked == 'stale_native':
            e.native, e.driver = copy.deepcopy(r.state), copy.deepcopy(r.driver)
            e.last_native = time.time()-7
            e.clear_expired_unsent_wait()
        else:
            await e.refresh()
        if blocked:
            assert e.active is not None
        else:
            assert e.active is None and e.store.get('active') is None
            assert not e.store.recent()
            assert e.mako()['MA_STATUS'] == 1
            # Fresh announcements can proceed after recovery.
            e.brewratio({'SYNC_BEVERAGE_WEIGHT':33, 'SYNC_FILTER':2})
            assert e.active['cycle_id'] != original['cycle_id']
        e.store.close()
    asyncio.run(run())


def test_attention_status_does_not_claim_automatic_capture_is_ready(tmp_path):
    async def run():
        e, c, r = await setup(tmp_path)
        e.active.update(phase='attention', message='Expired cycle.')
        assert c.summary().startswith('Feedback paused') and 'Expired cycle' in c.summary()
        e.store.close()
    asyncio.run(run())
