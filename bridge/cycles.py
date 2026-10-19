"""Cycle-ID contract: identity joins facts; fresh reads authorize delivery.

No I/O is performed here. Read barriers refer to operations, not cache-age offsets.
Native expiry and device liveness still have explicit time limits.
"""
import copy
import time

from grinder_client import BridgeError, make_plan

PENDING_SECONDS = 290  # Native five-minute window, retaining delivery headroom.
WAITING = {'awaiting_events', 'awaiting_final_mass', 'awaiting_arm', 'awaiting_native', 'queued'}


def versioned(record):
    return any(e.get('schema_version') == 2 for e in record.get('events', {}).values())


class Cycles:
    def __init__(self, controllers):
        self.c = controllers
        self.e = controllers.engine
        self.s = controllers.store

    def barrier(self):
        self.e.refresh_requested = True
        return {'boot': self.e.boot_id, 'after': self.e.read_started}

    def satisfied(self, barrier):
        return (barrier and barrier['boot'] == self.e.boot_id
                and self.e.read_completed > barrier['after'])

    def stop_rejected(self, record):
        """A rejected bound shot ends the virtual extraction without brew feedback."""
        a = self.e.active
        if (not a or a.get('protocol') != 2 or a.get('shot_id') or a.get('exposed')
                or record.get('delivery_id') or record.get('feedback_status') != 'history_only'
                or (record.get('binding') or {}).get('cycle_id') != a['cycle_id']
                or a.get('controller_shot_id') != record['shot_id']
                or a['phase'] not in ('armed', 'brewing')):
            return
        a.update(phase='aborting', finish_polls=0,
            controller_invalid_reason=record.get('reason') or 'shot_not_eligible',
            message='Shot ended without eligible feedback. Clearing the grinder wait.',
            abort_read=self.barrier())
        self.s.event('controller_shot_aborted', cycle_id=a['cycle_id'], shot_id=record['shot_id'],
            reason=record.get('reason'))
        self.e.persist()

    def reconcile_terminal(self):
        """Fresh native evidence retires only unexposed, terminal cycle waits."""
        a = self.e.active
        if (not a or a.get('protocol') != 2 or a['phase'] not in ('aborting', 'attention')
                or a.get('shot_id') or a.get('exposed') or not a.get('grind_uuid')):
            return False
        pending = self.e.native.get('pending_brew') or {}
        latest = next(iter(self.e.native.get('grinds', [])), {})
        replaced = (pending.get('grinduuid') != a['grind_uuid']
            and pending.get('grinduuid') == latest.get('grind_uuid')
            and pending.get('grindId') == latest.get('grind_id')
            and latest.get('grind_id', -1) > a.get('grind_id', -1))
        driver = self.e.driver.get('data', {})
        cleared = (not pending and driver.get('gbsState') == 'Idle'
            and driver.get('grindState') == 'Idle')
        if not replaced and not cleared:
            return False
        announcement = a.get('replacement_announcement')
        with self.s.atomic():
            self.s.event('terminal_cycle_cleared', cycle_id=a['cycle_id'],
                grind_uuid=a['grind_uuid'], pending_grind_uuid=pending.get('grinduuid'))
            self.e.active = None
            self.e.recovery = False
            self.e.persist()
            if announcement and time.time()-announcement['received_at'] < PENDING_SECONDS:
                # The request itself is durable. The next fresh read must still
                # identify a previously unclaimed native grind before any offer.
                self.c.confirm_idle_targets()
                self.e.brewratio(announcement['recipe'])
        return True

    def abort_authorized(self):
        a = self.e.active
        barrier = a.get('abort_read')
        if not barrier or barrier.get('boot') != self.e.boot_id:
            a['abort_read'] = self.barrier()
            self.e.persist()
            return False
        pending = self.e.native.get('pending_brew') or {}
        return (self.satisfied(barrier) and pending.get('grinduuid') == a.get('grind_uuid')
            and pending.get('grindId') == a.get('grind_id'))

    def capture(self):
        """An announcement identifies a cycle; native pending UUID identifies its grind."""
        from event_fields import timestamp
        from controllers import recipe_signature
        a = self.e.active
        if (not a or a.get('protocol') != 2 or a.get('grind_uuid')
                or a['phase'] not in ('armed', 'brewing')
                or not self.satisfied(a.get('announcement_read'))):
            return
        pending = self.e.native.get('pending_brew') or {}
        rows = [g for g in self.e.native.get('grinds', [])
                if g['grind_uuid'] == pending.get('grinduuid') and g['grind_id'] == pending.get('grindId')]
        if len(rows) != 1 or rows[0] != self.e.native.get('grinds', [None])[0]:
            return
        g = rows[0]
        if self.s.by_grind(g['grind_uuid']) or self.s.cycle_for_grind(g['grind_uuid']):
            return
        if not 0 <= time.time() - timestamp(g['grind_date_time']) < PENDING_SECONDS:
            return
        try:
            make_plan(self.e.native, {'source':'de1', 'shot_id':a['cycle_id'], 'valid':True,
                'duration_ms':24000, 'weight_g':36}, g['grind_id'])
        except BridgeError:
            return
        a.update(grind_uuid=g['grind_uuid'], grind_id=g['grind_id'], grind_time=g['grind_date_time'],
            expires_at=timestamp(g['grind_date_time']) + PENDING_SECONDS,
            recipe_index=g['recipe_index'], dose_g=g['grind_weight']/1000)
        proof = a.get('controller_target_proof', {}).get('targets')
        if (not proof or g['recipe_index'] != proof['recipe_index']
                or g['recipe_data_id'] != proof['native']['recipe_data_id']
                or g['recipe_brew_weight'] != round(proof['yield_g']*1000)
                or g['recipe_brew_time'] != round(proof['target_time_s']*1000)):
            a['controller_invalid_reason'] = 'grind_recipe_not_confirmed'
        else:
            a['recipe_signature'] = recipe_signature(proof['native'])
        self.e.persist()

    def offer(self, session=None):
        a = self.e.active
        if (not a or a.get('protocol') != 2 or not a.get('grind_uuid') or self.e.recovery
                or not self.e.ready or self.e.maintenance or a.get('controller_invalid_reason')
                or a.get('controller_shot_id') or a['phase'] not in ('armed', 'brewing')
                or time.time() >= a.get('expires_at', 0) or not self.c.session
                or (session is not None and session != self.c.session)):
            return None
        proof = a['controller_target_proof']['targets']
        try:self.native_guard(a)
        except BridgeError:return None
        offer = {'cycle_id':a['cycle_id'], 'grind_uuid':a['grind_uuid'],
            'session_id':self.c.session, 'profile_revision':proof['profile']['revision'],
            'expires_at':a['expires_at']}
        if a.get('offer') != offer:
            a['offer'] = copy.deepcopy(offer)
            self.e.persist()
        return offer

    def bind(self, record, event):
        from event_fields import timestamp
        try:
            a = self.e.active
            claim = event.get('cycle')
            if not claim:
                raise BridgeError('no_cycle_id_at_shot_start')
            if not a or a.get('protocol') != 2 or claim != a.get('offer'):
                raise BridgeError('cycle_id_not_offered_or_no_longer_active')
            if claim['session_id'] != event['session_id'] or event['session_id'] != self.c.session:
                raise BridgeError('cycle_controller_session_changed')
            if a.get('controller_shot_id') not in (None, record['shot_id']):
                raise BridgeError('grind_already_bound_to_another_shot')
            if a['phase'] not in ('armed', 'brewing') or a.get('controller_invalid_reason'):
                raise BridgeError(a.get('controller_invalid_reason', 'cycle_no_longer_eligible'))
            p = event['data'].get('profile')
            targets = a['controller_target_proof']['targets']
            if p != targets['profile']:
                raise BridgeError('profile_does_not_match_cycle')
            if not timestamp(a['grind_time']) <= timestamp(event['occurred_at']) < a['expires_at']:
                raise BridgeError('shot_outside_native_pending_window')
            # Delayed/reordered durable events keep their original identity. Current
            # native state will be verified at delivery, never guessed at receipt.
            record.update(binding={'cycle_id':a['cycle_id'], 'grind_uuid':a['grind_uuid'],
                'grind_id':a['grind_id'], 'grind_time':a['grind_time'], 'targets':copy.deepcopy(targets),
                'recipe_signature':a['recipe_signature'], 'session_id':event['session_id'],
                'transport':self.c.mode}, feedback_status='collecting', reason=None)
            a['controller_shot_id'] = record['shot_id']
            self.e.persist()
        except BridgeError as exc:
            self.c.reject_binding(record, event, str(exc))

    def native_guard(self, a):
        from controllers import recipe_for, recipe_signature
        if time.time() >= a.get('expires_at', 0):
            raise BridgeError('native_pending_window_expired')
        pending = self.e.native.get('pending_brew') or {}
        if pending.get('grinduuid') != a.get('grind_uuid') or pending.get('grindId') != a.get('grind_id'):
            raise BridgeError('native_pending_grind_changed')
        grinds = self.e.native.get('grinds', [])
        if not grinds or grinds[0]['grind_uuid'] != a.get('grind_uuid'):
            raise BridgeError('newer_grind_detected')
        recipe = recipe_for(self.e.native, a.get('recipe_index'))
        if not recipe or recipe_signature(recipe) != a.get('recipe_signature'):
            raise BridgeError('native_recipe_changed')
        if any(b['grind_id'] == a['grind_id'] for b in self.e.native.get('brews', [])):
            raise BridgeError('grind_already_consumed')

    def arm(self):
        a = self.e.active
        if not a.get('arming'):
            a['arming'] = {'requested_at':time.time(), 'read':self.barrier(), 'confirmed':False}
        # Repeated button callbacks do not reset identity, deadlines or read barriers.
        a['starts'] = 1
        a['phase'] = 'brewing'
        a.setdefault('started', time.time())
        self.e.persist()

    def validate(self, record, *, require_arm=True):
        b, a = record.get('binding'), self.e.active
        if not b or not a or a['cycle_id'] != b['cycle_id'] or a.get('grind_uuid') != b['grind_uuid']:
            raise BridgeError('active_cycle_changed')
        if self.e.recovery:
            raise BridgeError('waiting_for_reconciliation')
        self.c.check_frozen_cycle()
        if a.get('controller_invalid_reason'):
            raise BridgeError(a['controller_invalid_reason'])
        if b['transport'] != self.c.mode or self.c.mode == 'observe':
            raise BridgeError('feedback_transport_unavailable')
        self.native_guard(a)
        arming = a.get('arming')
        if arming and self.satisfied(arming.get('read')) and not arming.get('confirmed'):
            arming['confirmed'] = True
            self.e.persist()
        if not self.c.machine['reachable'] or time.time()-self.c.last_event_received > 15:
            raise BridgeError('tablet_unreachable')
        if self.c.observed_profile != b['targets']['profile']:
            raise BridgeError('effective_profile_changed')
        if require_arm and self.c.mode == 'xenia_one_press' and not a.get('arming', {}).get('confirmed'):
            raise BridgeError('grinder_press_required')

    def advance(self, record):
        """Retain eligible results while waiting for independent arming/read facts."""
        if record.get('delivery_id') or not record.get('delivery_input'):
            return
        a = self.e.active
        try:
            if not a or (record.get('binding') or {}).get('cycle_id') != a['cycle_id']:
                raise BridgeError('active_cycle_changed')
            if time.time() >= a.get('expires_at', 0):
                raise BridgeError('native_pending_window_expired')
            if self.e.recovery:
                record.update(feedback_status='awaiting_native', reason='waiting_for_reconciliation')
                return
            check = record.get('delivery_read')
            if not check or check['boot'] != self.e.boot_id:
                record['delivery_read'] = self.barrier()
                record.update(feedback_status='awaiting_native', reason='waiting_for_native_validation')
                return
            if not self.satisfied(check):
                record.update(feedback_status='awaiting_native', reason='waiting_for_native_validation')
                return
            self.validate(record, require_arm=False)
            if self.c.mode == 'xenia_one_press':
                arming = a.get('arming')
                if not arming:
                    record.update(feedback_status='awaiting_arm', reason='grinder_press_required')
                    return
                if arming['read']['boot'] != self.e.boot_id:
                    arming.update(read=self.barrier(), confirmed=False)
                    self.e.persist()
                if not self.satisfied(arming['read']):
                    record.update(feedback_status='awaiting_native', reason='verifying_late_grinder_press')
                    return
                arming['confirmed'] = True
                self.e.persist()
            record.update(feedback_status='queued', reason=None)
        except BridgeError as exc:
            reason = str(exc)
            if reason in ('tablet_unreachable', 'waiting_for_reconciliation'):
                record.update(feedback_status='awaiting_native', reason=reason)
            else:
                record.update(feedback_status='history_only', reason=reason)

    def recover(self):
        """Only provably unexposed facts resume; exposed deliveries use Engine reconciliation."""
        a = self.e.active
        self.native_guard(a)
        if a.get('arming'):
            a['arming'].update(read=self.barrier(), confirmed=False)
        a.pop('delivery_grant', None)
        self.e.persist()
