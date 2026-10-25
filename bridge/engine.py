"""Durable Xenia state machine. HTTP polling never waits for grinder I/O."""
import asyncio
import copy
import logging
import time
import uuid
from datetime import datetime

from grinder_client import BridgeError, make_plan, normalize_shot
from result_analysis import assess_xenia, small_change_within_tolerance, movement_warning_due, POSITION_TOLERANCE_DUM

IDENTITY = {'MA_TYPE': 3, 'MA_SN': 'DE1-SYNTHETIC-BRIDGE',
            'FW_VERSION_MAJOR': 1, 'FW_VERSION_MINOR': 0, 'ESP_FW_MAJOR': 1, 'ESP_FW_MINOR': 0}
DEVICE_ID = 'Xenia_Xenia DB with Power Controller_DE1-SYNTHETIC-BRIDGE'


class Reader:
    def __init__(self, factory):
        self.factory = factory

    async def read(self):
        async with asyncio.timeout(45), self.factory() as client:
            state = await client.read()
            # Database confirmation remains usable if the separate sensor socket fails.
            try:
                driver = await client.command({'type': 'RequestDriverState'})
            except Exception:
                driver = {}
            return state, driver


def target(state, index=None):
    recipes = state.get('recipes', [])
    recipe = next((r for r in recipes if r['recipe_index'] == index), None) if index else None
    recipe = recipe or next((r for r in recipes if r['recipe_active']), {})
    return (recipe.get('recipe_gbs_data') or {}).get('coursenessDeciMym')


def public_shot(record):
    keys = ('id', 'grind_id', 'grind_uuid', 'created', 'state', 'input', 'message',
            'outcome', 'target_before_um', 'target_after_um', 'physical_after_um', 'source')
    return {k: record[k] for k in keys if k in record}


class Engine:
    def __init__(self, store, reader):
        self.store, self.reader = store, reader
        self.active = store.get('active')
        self.native = store.get('native', {})
        self.driver = {}
        self.last_native = 0
        self.last_poll = None
        self.last_identity = None
        self.poll_count = 0
        self.error = None
        self.ready = False
        self.io_lock = asyncio.Lock()
        self.submit_lock = asyncio.Lock()
        self.tasks = set()
        self.refresh_requested = True
        self.recovery = bool(self.active)
        self.controllers = None
        self.boot_id = str(uuid.uuid4())
        self.read_started = self.read_completed = 0
        self.maintenance = None
        self.configuration_epoch = 0

    def persist(self):
        with self.store.atomic():
            previous = self.store.get('active')
            if previous and previous.get('protocol') == 2 and (not self.active or previous['cycle_id'] != self.active['cycle_id']):
                self.store.save_cycle({**previous, 'closed_at':time.time()})
            if self.active and self.active.get('protocol') == 2:
                self.store.save_cycle(self.active)
            self.store.set('active', self.active)

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        def finished(t):
            self.tasks.discard(t)
            if not t.cancelled() and t.exception():
                self.error = 'A background operation failed. Check the service log.'
                self.store.event('task_error', error=type(t.exception()).__name__)
        task.add_done_callback(finished)
        return task

    async def refresh(self):
        async with self.io_lock:
            self.read_started += 1
            generation = self.read_started
            configuration_epoch = self.configuration_epoch
            state, driver = await self.reader.read()
            if configuration_epoch != self.configuration_epoch:
                raise BridgeError('Configuration changed during native read')
            self.native, self.driver = state, driver
            self.last_native = time.time()
            self.read_completed = generation
            self.error = None
            self.store.set('native', state)
            if self.controllers:
                self.controllers.cycles.reconcile_terminal()
            self.clear_expired_unsent_wait()
            if self.controllers:
                self.controllers.cycles.capture()
                self.controllers.observe_grinds()
                self.controllers.check_frozen_cycle()
                self.controllers.resolve_pending_bindings()
                self.controllers.confirm_idle_targets()
            return state, driver

    def clear_expired_unsent_wait(self):
        """Release an expired empty wait only after native pending state has cleared.

        Deliveries, including uncertain ones, retain their reconciliation path.
        This never reconstructs a result or sends completion to the grinder.
        """
        a = self.active
        if (not a or a.get('phase') != 'attention' or a.get('shot_id') or a.get('exposed')
                or time.time() - a.get('created', time.time()) <= 300
                or time.time() - self.last_native > 6 or self.native.get('pending_brew')):
            return False
        driver = self.driver.get('data', {})
        if driver.get('gbsState') != 'Idle' or driver.get('grindState') != 'Idle':
            return False
        with self.store.atomic():
            self.store.event('expired_unsent_wait_cleared', cycle_id=a['cycle_id'],
                grind_uuid=a.get('grind_uuid'), native_pending=None)
            self.active = None
            self.persist()
        return True

    async def start(self):
        # Keep Xenia unavailable until restart reconciliation has completed.
        self.spawn(self.loop())
        self.spawn(self.retention_loop())
        if self.controllers:
            self.spawn(self.controller_loop())

    async def retention_loop(self):
        from store import retain_telemetry
        while True:
            count = 0
            if not self.active and not self.recovery and not self.maintenance:
                try:
                    count = await asyncio.to_thread(retain_telemetry, self.store.path)
                except Exception:
                    logging.exception('Routine telemetry retention failed')
            await asyncio.sleep(0.1 if count else 60)

    async def controller_loop(self):
        while True:
            try:
                await self.controllers.poll_machine()
                if self.ready and not self.recovery:
                    await self.controllers.dispatch()
                    c = self.controllers
                    if (not c.history_started and not self.active and c.machine['reachable']
                            and c.machine.get('state') != 'Espresso' and hasattr(c.adapter, 'history')):
                        c.history_started = True
                        self.spawn(c.reconcile_history())
            except Exception as exc:
                self.store.event('controller_error', error=type(exc).__name__)
            await asyncio.sleep(0.5)

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def loop(self):
        while True:
            interval = self.native_refresh_interval()
            if self.refresh_requested or time.time() - self.last_native >= interval:
                self.refresh_requested = False
                try:
                    await self.refresh()
                    if self.recovery:
                        await self.reconcile()
                    self.ready = True
                    self.capture_grind()
                    self.verify()
                except Exception as exc:
                    self.error = 'Cannot read the grinder. Check its power, network and Owner password.'
                    self.store.event('read_error', error=type(exc).__name__)
                    await asyncio.sleep(4)
            a = self.active
            if a and a['phase'] == 'finishing':
                shot = self.store.shot(a['shot_id'])
                if a.get('finish_polls', 0) >= 2 and shot['state'] == 'verified':
                    self.active = None
                    self.persist()
                elif time.time() - a['finish_at'] > 45:
                    self.store.update_shot(a['shot_id'], state='uncertain',
                        message='Delivery could not be confirmed. Check the grinder; this result will not be replayed.')
                    a['phase'] = 'attention'
                    self.persist()
            elif a and a['phase'] == 'aborting' and a.get('finish_polls', 0) >= 2:
                a['phase'] = 'attention'
                a['message'] = 'Wait cancelled. Let the grinder clear its pending shot before grinding again.'
                self.persist()
            elif a and a['phase'] in ('armed', 'brewing') and time.time() - a['created'] > 290:
                a['phase'] = 'aborting'
                a['message'] = 'This shot timed out. No result was sent.'
                a['finish_polls'] = 0
                self.persist()
            await asyncio.sleep(0.5)

    def native_refresh_interval(self):
        if self.active:
            return 4
        recent = self.store.recent(1)
        if (recent and recent[0].get('outcome') == 'movement_pending'
                and self.native.get('grinds')
                and self.native['grinds'][0]['grind_uuid'] == recent[0]['grind_uuid']
                and not movement_warning_due(recent[0], time.time())):
            return 4
        return 30

    async def reconcile(self):
        a = self.active
        if a and a.get('protocol') == 2 and a['phase'] in ('aborting', 'attention') and not a.get('shot_id') and not a.get('exposed'):
            self.recovery = False
            self.persist()
            return
        if a and a.get('protocol') == 2 and not a.get('exposed') and self.controllers:
            try:
                if not a.get('grind_uuid'):
                    if time.time()-a['created'] > 290:
                        raise BridgeError('native_pending_window_expired')
                    # This read was started after the persisted announcement and
                    # after this process restarted; no time-offset matching needed.
                    a['announcement_read'] = {'boot':self.boot_id, 'after':self.read_completed-1}
                    self.controllers.cycles.capture()
                    if not a.get('grind_uuid'):
                        self.refresh_requested = True
                        return
                self.controllers.cycles.recover()
                self.recovery = False
                if a.get('shot_id'):
                    record = self.store.shot(a['shot_id'])
                    if record and record['state'] == 'validating':
                        a['phase'] = 'validating'
                        self.spawn(self.prepare(record['id'], a['cycle_id']))
                self.persist()
                return
            except BridgeError as exc:
                a.update(phase='attention', message=str(exc), controller_invalid_reason=str(exc))
                self.recovery = False
                self.persist()
                return
        if a and a.get('shot_id'):
            record = self.store.shot(a['shot_id'])
            if record and record.get('plan'):
                if self.verify_record(record):
                    self.active = None
                else:
                    self.store.update_shot(record['id'], state='uncertain',
                        message='Service restarted during this shot. No feedback will be replayed. Check the grinder.')
                    a['phase'] = 'attention'
                    a['message'] = 'Interrupted shot: check the grinder before clearing this wait.'
            else:
                a['phase'] = 'attention'
        elif a:
            a['phase'] = 'attention'
            a['message'] = 'Service restarted during a shot. Check the grinder before clearing this wait.'
        self.recovery = False
        self.persist()

    def capture_grind(self):
        a = self.active
        if a and a.get('protocol') == 2:
            return self.controllers.cycles.capture()
        if not a or a.get('grind_uuid') or a['phase'] not in ('armed', 'brewing'):
            return
        grinds = self.native.get('grinds', [])
        if not grinds:
            return
        grind = grinds[0]
        stamp = datetime.fromisoformat(grind['grind_date_time'].replace('Z', '+00:00')).timestamp()
        if not a['created'] - 30 <= stamp <= a['created'] + 10:
            return
        if self.store.by_grind(grind['grind_uuid']):
            return
        if any(b['grind_id'] == grind['grind_id'] for b in self.native.get('brews', [])):
            return
        a.update(grind_uuid=grind['grind_uuid'], grind_id=grind['grind_id'],
                 recipe_index=grind['recipe_index'],
                 dose_g=grind['grind_weight'] / 1000)
        self.persist()

    def brewratio(self, data):
        self.maintenance = None  # A new grind cancels an idle maintenance lease.
        if (not isinstance(data, dict) or set(data) != {'SYNC_BEVERAGE_WEIGHT', 'SYNC_FILTER'}
                or type(data['SYNC_FILTER']) is not int or data['SYNC_FILTER'] not in (1, 2)
                or type(data['SYNC_BEVERAGE_WEIGHT']) not in (int, float)
                or not 1 <= data['SYNC_BEVERAGE_WEIGHT'] <= 250):
            raise BridgeError('Unrecognized grinder recipe data')
        # A retry during a live cycle does not erase correlation or restart its clock.
        if self.active:
            if (self.active.get('protocol') == 2 and self.active['phase'] in ('aborting', 'attention')
                    and not self.active.get('shot_id') and not self.active.get('exposed')):
                self.active['replacement_announcement'] = {
                    'recipe':copy.deepcopy(data), 'received_at':time.time()}
                self.refresh_requested = True
                self.persist()
                return
            if self.active['phase'] in ('armed', 'brewing') and self.active['recipe'] == data:
                self.refresh_requested = True
                return
            raise BridgeError('Finish or clear the current shot first')
        self.active = {'cycle_id': str(uuid.uuid4()), 'created': time.time(), 'phase': 'armed',
                       'recipe': data, 'brew_polls': 0, 'finish_polls': 0, 'starts': 0}
        if self.controllers:
            self.controllers.freeze_cycle(self.active)
            if self.controllers.cycle_protocol:
                self.active.update(protocol=2, announcement_read=self.controllers.cycles.barrier())
        self.persist()
        self.store.event('grind_announced', cycle_id=self.active['cycle_id'], recipe=data)
        self.refresh_requested = True

    def execute(self, data):
        if not isinstance(data, dict) or set(data) != {'ID'} or data['ID'] != 9:
            raise BridgeError('Unsupported brew script')
        if not self.ready or not self.active or self.active['phase'] not in ('armed', 'brewing', 'validating', 'finishing'):
            raise BridgeError('No active grind is ready to brew')
        a = self.active
        if a.get('protocol') == 2:
            if a['phase'] in ('armed', 'brewing'):
                self.controllers.cycles.arm()
            return
        if a['phase'] == 'armed':
            a['phase'] = 'brewing'
            a['started'] = time.time()
            self.store.event('brew_started', cycle_id=a['cycle_id'])
        a['starts'] += 1
        self.persist()

    def mako(self):
        self.last_poll = time.time()
        self.poll_count += 1
        status, extraction, duration, weight = 1 if self.ready else 0, 0, 0, 0
        a = self.active
        if a and a.get('controller_record') and a['phase'] == 'finishing' and not a.get('exposed'):
            try:
                self.controllers.validate_delivery(a['controller_record'])
                if a.get('protocol') == 2 and not a.get('delivery_grant'):
                    raise BridgeError('No delivery authorization for this cycle')
                if a.get('protocol') != 2 and time.time() - self.last_native > 6:
                    raise BridgeError('Native validation became stale before delivery')
            except BridgeError as exc:
                if a.get('protocol') == 2 and str(exc) == 'tablet_unreachable':
                    # Connectivity loss holds the result; it never changes its identity.
                    return self.waiting_poll()
                self.store.update_shot(a['shot_id'], state='rejected', message=str(exc))
                a.update(phase='attention', message=str(exc))
                self.persist()
        if self.ready and a:
            if a['phase'] in ('brewing', 'validating'):
                status = 3
                a['brew_polls'] += 1
            elif a['phase'] == 'finishing':
                status = 4
                a['finish_polls'] += 1
                shot = self.store.shot(a['shot_id'])
                duration = shot['plan']['shot']['duration_ms']
                weight = shot['plan']['shot']['weight_mg'] / 1000
                # Persist before exposing completion; restart never blindly replays it.
                if not a.get('exposed'):
                    with self.store.atomic():
                        self.store.set('active', {**a, 'exposed':True})
                        self.store.update_shot(shot['id'], state='delivering', exposed_at=time.time())
                    a['exposed'] = True
                    self.refresh_requested = True
            elif a['phase'] == 'aborting':
                if a.get('protocol') != 2 or self.controllers.cycles.abort_authorized():
                    status, extraction = 4, 2
                    a['finish_polls'] += 1
                    if a.get('protocol') == 2:
                        a['abort_read'] = self.controllers.cycles.barrier()
                        self.persist()
        return {'MA_STATUS': status, 'MA_EXTRACTION_STATUS': extraction,
                'REAL_EXTRACTION_TIME_MS': duration, 'PU_SENS_FLOW_METER_VOLUME': weight,
                'PU_SENS_FLOW_METER_FLOWRATE': 0, 'BG_SET_TEMP': 93.0, 'BG_SENS_TEMP': 93.0,
                'TANK_LEVEL': 1, 'script': 'COFFEE-BRIDGE-MANUAL', 'TIMESTAMP': str(int(time.time()))}

    async def submit(self, data, *, controller=None):
        if self.maintenance:
            raise BridgeError('Service update is in progress')
        if controller is not None:
            if not self.controllers or data.get('timing_basis') != 'after_preinfusion':
                raise BridgeError('Invalid controller timing contract')
            self.controllers.validate_delivery(controller)
        elif data.get('timing_basis') != 'first_drip':
            raise BridgeError('Use elapsed time from first drip for this recipe')
        normalized = normalize_shot(data)
        if data['source'] not in ('manual', 'de1'):
            raise BridgeError('Production accepts manual or DE1 results only')
        if type(data.get('record_only', False)) is not bool:
            raise BridgeError('record_only must be true or false')
        async with self.submit_lock:
            existing = self.store.by_external(data['source'], data['shot_id'])
            if existing:
                if existing['input'] != data:
                    raise BridgeError('This shot ID already has different data')
                return public_shot(existing)
            a = self.active
            if not self.ready or not a or a['phase'] != 'brewing':
                raise BridgeError('Grind, then press the grinder’s brew knob once before submitting')
            if not a.get('grind_uuid') or data.get('grind_uuid') != a['grind_uuid']:
                raise BridgeError('The result does not match the active grind. Refresh and check the shot')
            if not self.last_poll or time.time() - self.last_poll > 10:
                raise BridgeError('The grinder is not polling this bridge')
            if self.store.by_grind(a['grind_uuid']):
                raise BridgeError('This grind already has a submitted result')
            record = {'id': str(uuid.uuid4()), 'grind_uuid': a['grind_uuid'], 'grind_id': a['grind_id'],
                      'source': data['source'], 'external_id': data['shot_id'], 'created': time.time(),
                      'state': 'validating', 'input': data, 'message': 'Checking the active grind…'}
            with self.store.atomic():
                self.store.add_shot(record)
                a.update(phase='validating', shot_id=record['id'])
                if controller:
                    a.update(controller_record=controller, feedback_mode=self.controllers.mode)
                self.persist()
            self.spawn(self.prepare(record['id'], a['cycle_id']))
            return public_shot(record)

    async def prepare(self, shot_id, cycle_id):
        try:
            record = self.store.shot(shot_id)
            state, driver = await self.refresh()
            a = self.active
            if not a or a['cycle_id'] != cycle_id or a['phase'] != 'validating':
                raise BridgeError('Active shot changed during validation')
            if not state.get('grinds') or state['grinds'][0]['grind_uuid'] != record['grind_uuid']:
                raise BridgeError('A newer grind was found. This result will not be sent')
            plan = make_plan(state, record['input'], record['grind_id'],
                             record_only=record['input'].get('record_only', False))
            if plan['grind_uuid'] != record['grind_uuid']:
                raise BridgeError('Grind identity changed')
            pending = state.get('pending_brew')
            if pending and pending.get('grinduuid') != record['grind_uuid']:
                raise BridgeError('Native pending-shot identity differs')
            if a.get('controller_record'):
                self.controllers.validate_delivery(a['controller_record'])
            self.store.update_shot(shot_id, plan=plan, driver_before=driver,
                                  message='Ready; sending on the next grinder poll.')
            # Two observed brewing polls establish the native extraction transition.
            deadline = time.time() + 12
            while a['brew_polls'] < 2:
                if time.time() > deadline or self.active is not a or a['phase'] != 'validating':
                    raise BridgeError('Grinder did not acknowledge brewing in time')
                await asyncio.sleep(0.1)
            if a.get('protocol') == 2:
                # A new native read after the Xenia brewing handshake authorizes the
                # next completion poll. There is no cache-age correlation cutoff.
                await self.refresh()
                if self.active is not a or a['phase'] != 'validating':
                    raise BridgeError('Active cycle changed before delivery')
            if a.get('controller_record'):
                self.controllers.validate_delivery(a['controller_record'])
            if a.get('protocol') != 2 and time.time() - self.last_native > 15:
                raise BridgeError('Grinder validation is stale; no result was sent')
            a.update(phase='finishing', finish_at=time.time(), finish_polls=0, exposed=False)
            if a.get('protocol') == 2:
                a['delivery_grant'] = {'boot':self.boot_id, 'read':self.read_completed, 'cycle_id':cycle_id}
            self.persist()
        except Exception as exc:
            message = str(exc) if isinstance(exc, BridgeError) else 'Cannot validate the grinder; no result was sent.'
            self.store.update_shot(shot_id, state='rejected', message=message)
            if self.active and self.active['cycle_id'] == cycle_id:
                self.active.update(phase='attention', message=message)
                self.persist()

    def verify_record(self, record):
        plan = record.get('plan')
        if not plan:
            return False
        # Require this bridge's exact native device identity as well as grind/time/mass.
        rows = [b for b in self.native.get('brews', []) if b['grind_id'] == record['grind_id']
                and b['brew_duration'] == plan['shot']['duration_ms']
                and b['brew_mass'] == plan['shot']['weight_mg'] and b['brew_validity'] == 1
                and b['brew_device_id'] == DEVICE_ID
                and b['brew_id'] not in {v['brew_id'] for v in plan['baseline']['brews']}]
        if len(rows) != 1:
            return False
        if not plan['record_only'] and (self.native.get('pending_brew') or {}).get('grindId') == record['grind_id']:
            return False
        assessment = assess_xenia(plan, self.native, record.get('driver_before', {}), self.driver)
        index = next(g['recipe_index'] for g in plan['baseline']['grinds'] if g['grind_id'] == record['grind_id'])
        before, after = target(plan['baseline'], index), target(self.native, index)
        physical = self.driver.get('data', {}).get('ddValueDum')
        assessment['within_target_tolerance'] = small_change_within_tolerance(
            before, after, record.get('driver_before', {}), self.driver, self.native)
        assessment['position_tolerance_um'] = POSITION_TOLERANCE_DUM / 10
        if plan['record_only']:
            outcome, message = 'recorded', 'Saved in grinder history without a pending adjustment.'
        elif assessment['hold_after_large_adjustment']:
            outcome, message = 'held', 'Recorded. The grinder held its setting after the previous large adjustment.'
        elif before != after:
            if (assessment['physical_movement_observed'] and isinstance(physical, (int, float))
                    and isinstance(after, (int, float)) and abs(physical - after) <= POSITION_TOLERANCE_DUM):
                outcome, message = 'adjusted', 'Recorded. The grinder changed its target and the burr sensors confirm movement.'
            elif assessment['within_target_tolerance']:
                outcome, message = 'within_tolerance', 'Shot received; tiny grind target update. Burr position is within 1.0 µm of the new target; movement was not confirmed.'
            else:
                outcome, message = 'movement_pending', 'Shot received; grind target updated. Burr movement unconfirmed. Check the grinder if this persists.'
        else:
            outcome, message = 'unchanged', 'Recorded. The grinder kept its current target.'
        changes = {}
        if outcome == 'movement_pending' and 'movement_pending_since' not in record:
            # Keep the first check time across rechecks/restarts. Never use the
            # repeatedly updated verified_at as a rolling warning deadline.
            changes['movement_pending_since'] = record.get('verified_at', time.time())
        self.store.update_shot(record['id'], state='verified', outcome=outcome, message=message,
                              assessment=assessment, verified_at=time.time(),
                              target_before_um=before / 10 if before is not None else None,
                              target_after_um=after / 10 if after is not None else None,
                              physical_after_um=physical / 10 if physical is not None else None, **changes)
        return True

    def verify(self):
        a = self.active
        if a and a.get('shot_id') and a['phase'] in ('finishing', 'attention'):
            record = self.store.shot(a['shot_id'])
            if record and record.get('plan'):
                self.verify_record(record)
        # Observe delayed physical motion only until another grind changes the context.
        recent = self.store.recent(1)
        if recent and recent[0].get('outcome') == 'movement_pending':
            if self.native.get('grinds') and self.native['grinds'][0]['grind_id'] == recent[0]['grind_id']:
                self.verify_record(recent[0])

    def cancel(self):
        a = self.active
        if not a:
            return
        if a['phase'] in ('validating', 'finishing'):
            raise BridgeError('Delivery is in progress; wait for its outcome')
        if a['phase'] == 'attention':
            if time.time() - self.last_native > 6:
                self.refresh_requested = True
                raise BridgeError('Refresh grinder state before clearing this wait')
            if self.native.get('pending_brew'):
                raise BridgeError('The grinder still has a pending shot. Wait for its five-minute timeout, then try again')
            self.active = None
        else:
            a.update(phase='aborting', finish_polls=0)
        self.persist()
        self.refresh_requested = True

    def status(self):
        now = time.time()
        recipes = self.native.get('recipes', [])
        # recipe_active means available, not necessarily the most recently used slot.
        latest = self.native.get('grinds', [{}])[0] if self.native.get('grinds') else {}
        index = (self.active or {}).get('recipe_index', latest.get('recipe_index'))
        recipe = next((r for r in recipes if r['recipe_index'] == index), {})
        pending = self.native.get('pending_brew') or {}
        active = copy.deepcopy(self.active)
        if active:
            active['adjustment_eligible'] = (pending.get('grindId') == active.get('grind_id')
                                             and pending.get('grinduuid') == active.get('grind_uuid'))
        physical = self.driver.get('data', {}).get('ddValueDum')
        gap = target(self.native, index)
        result = {'ready': self.ready, 'mode': 'manual', 'grinder_connected': bool(self.last_poll and now-self.last_poll < 10),
                'last_poll': self.last_poll, 'poll_count': self.poll_count,
                'last_native_read': self.last_native or None, 'error': self.error,
                'active': active, 'recipe': {'dose_g': recipe.get('recipe_amount', 0)/1000,
                    'yield_g': recipe.get('recipe_brew_weight', 0)/1000,
                    'time_s': recipe.get('recipe_brew_time', 0)/1000,
                    'target_um': gap / 10 if gap is not None else None},
                'physical_um': physical / 10 if physical is not None else None,
                'sensor_valid': self.driver.get('data', {}).get('ddSensorFailed') is False,
                'shots': [public_shot(r) for r in self.store.recent()],
                'native_history': self.native.get('brews', [])[:30],
                'maintenance': self.maintenance}
        result['journal_retention'] = self.store.get('telemetry_retention')
        if self.controllers:
            result.update(mode=self.controllers.mode, automation=self.controllers.status())
        return result

    def waiting_poll(self):
        return {'MA_STATUS':3, 'MA_EXTRACTION_STATUS':0, 'REAL_EXTRACTION_TIME_MS':0,
            'PU_SENS_FLOW_METER_VOLUME':0, 'PU_SENS_FLOW_METER_FLOWRATE':0,
            'BG_SET_TEMP':93.0, 'BG_SENS_TEMP':93.0, 'TANK_LEVEL':1,
            'script':'COFFEE-BRIDGE-MANUAL', 'TIMESTAMP':str(int(time.time()))}
