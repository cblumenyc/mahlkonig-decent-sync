"""Durable, application-independent controller events and conservative correlation."""
import copy
import hashlib
import math
import re
import sqlite3
import time

from grinder_client import BridgeError, make_plan
from cycles import Cycles, versioned, WAITING
from result_analysis import movement_warning_due
from event_fields import canonical, number, timestamp
from measurements import measurement_snapshot, validate_measurements

ID = re.compile(r'^[A-Za-z0-9_.:-]{1,120}$')
KINDS = {'status', 'profile', 'shot_started', 'preinfusion_complete', 'shot_finished', 'shot_finalized'}


def recipe_for(native, index):
    matches = [r for r in native.get('recipes', []) if r['recipe_index'] == index]
    return matches[0] if len(matches) == 1 else None


def recipe_signature(recipe):
    return canonical({k: recipe.get(k) for k in ('recipe_index', 'recipe_data_id', 'recipe_type_id',
        'recipe_brew_time', 'recipe_brew_weight', 'recipe_amount', 'recipe_gbs_data')})


def execution_signature(profile):
    settings = dict(profile['effective_settings'])
    for key in ('final_desired_shot_volume', 'final_desired_shot_volume_advanced',
                'final_desired_shot_weight', 'final_desired_shot_weight_advanced'):
        settings.pop(key, None)
    if profile.get('mode') == 'auto':
        settings['sync_fallback_policy'] = profile['fallback_policy']
    return canonical(settings)


class Controllers:
    def __init__(self, engine, config=None, adapter=None):
        self.engine, self.store = engine, engine.store
        self.config = config or {}
        self.adapter = adapter
        self.mode = self.config.get('feedback_mode', 'observe')
        if self.mode not in ('observe', 'xenia_one_press'):
            raise ValueError('No verified implementation exists for the requested feedback transport')
        self.enabled = self.config.get('enabled', False)
        self.wake_enabled = self.config.get('wake_enabled', False)
        self.controller_id = self.config.get('controller_id', 'classic-de1')
        # Full acceptance and owner-requested daily use are distinct rollout states.
        # The privileged installer validates either record against the real journal.
        proof = self.config.get('acceptance', {})
        self.validation_complete = (proof.get('mode') == self.mode
            and proof.get('consecutive_real_cycles', 0) >= 3
            and proof.get('prompt_clearance') is True
            and proof.get('native_adjustment_verified') is True)
        daily = self.config.get('daily_use', {})
        daily_requested = (self.mode == 'xenia_one_press' and daily.get('schema_version') == 1
            and daily.get('mode') == self.mode and daily.get('owner_requested') is True
            and daily.get('validation_complete') is False
            and isinstance(daily.get('owner_request'), str) and bool(daily['owner_request'].strip())
            and isinstance(daily.get('outstanding_checks'), list) and bool(daily['outstanding_checks'])
            and len(daily.get('cycles', [])) >= 2)
        beta = self.config.get('community_beta', {})
        beta_requested = (self.mode == 'xenia_one_press' and beta.get('schema_version') == 1
            and beta.get('owner_requested') is True and beta.get('validation_complete') is False)
        if self.enabled and not (self.validation_complete or daily_requested or beta_requested):
            raise ValueError('Automatic feedback requires full acceptance or reviewed one-press daily-use authorization')
        self.test_mode = self.config.get('supervised_test', False)
        if self.test_mode and self.enabled:
            raise ValueError('Supervised test and production modes are separate')
        self.store.select_controller(self.controller_id,self.config.get('capture_identity'))
        self.last_adapter_check = 0
        self.machine = {'reachable': False, 'state': 'unknown', 'substate': 'unknown'}
        self.wake = self.store.get('controller_wake', {'state': 'idle'})
        self.seen = set(self.store.get('controller_seen_grinds', []))
        self.initialized = False
        self.history_started = False
        self.history_reconciliation = self.store.get('controller_history_reconciliation', {'state':'not_checked'})
        self.observed_profile = self.store.get('controller_profile')
        self.confirmed_targets = self.store.get('controller_confirmed_targets')
        self.last_event_received = 0
        self.measurement_ready = False
        stream = self.store.get('controller_stream', {})
        self.session = stream.get('session_id')
        self.sequence = stream.get('sequence', -1)
        self.cycle_protocol = self.store.get('controller_contract', 1) == 2
        self.cycles = Cycles(self)
        # A restart never synthesizes a fresh shot or blindly replays an intent.
        for record in self.store.controller_history(controller_id=self.controller_id):
            if versioned(record):
                # Durable cycle facts survive restart. Only Engine decides whether a
                # delivery was exposed and therefore requires uncertain reconciliation.
                prior = self.store.by_external('de1', record['external_id'])
                if prior:
                    record.update(feedback_status='journaled', delivery_id=prior['id'])
                else:
                    record.pop('delivery_read', None)
                self.store.save_controller_shot(record)
                continue
            if record.get('binding_pending'):
                record.pop('binding_pending')
                record.update(binding=None, feedback_status='history_only',
                    reason='bridge_restarted_before_grind_validation')
                record['binding_failure'] = {'reason': record['reason']}
                self.store.save_controller_shot(record)
            if record.get('feedback_status') == 'awaiting_final_mass':
                record.update(feedback_status='history_only', reason='bridge_restarted_before_final_mass')
                self.store.save_controller_shot(record)
            if record.get('feedback_status') in ('queued', 'dispatching'):
                prior = self.store.by_external('de1', record['external_id'])
                if prior:
                    record.update(feedback_status='journaled', delivery_id=prior['id'])
                else:
                    record.update(feedback_status='history_only', reason='bridge_restarted_before_delivery')
                self.store.save_controller_shot(record)

    def validate_event(self, controller, event):
        if controller != self.controller_id or not ID.fullmatch(controller):
            raise BridgeError('Controller is not configured')
        if not isinstance(event, dict) or event.get('schema_version') not in (1, 2):
            raise BridgeError('Unsupported controller event version')
        for field in ('event_id', 'session_id'):
            if not isinstance(event.get(field), str) or not ID.fullmatch(event[field]):
                raise BridgeError(f'Invalid {field}')
        if type(event.get('sequence')) is not int or event['sequence'] < 0:
            raise BridgeError('Sequence must be a nonnegative integer')
        if event.get('kind') not in KINDS or not isinstance(event.get('data'), dict):
            raise BridgeError('Invalid event kind or data')
        timestamp(event.get('occurred_at'))
        if event.get('cycle') is not None:
            cycle = event['cycle']
            if (event['schema_version'] != 2 or not isinstance(cycle, dict)
                    or set(cycle) != {'cycle_id','grind_uuid','session_id','profile_revision','expires_at'}
                    or any(not isinstance(cycle.get(k), str) or not ID.fullmatch(cycle[k])
                           for k in ('cycle_id','grind_uuid','session_id','profile_revision'))):
                raise BridgeError('Invalid cycle identity')
            number(cycle.get('expires_at'), 'cycle deadline', 0, 9999999999)
        if event['kind'].startswith('shot_') or event['kind'] == 'preinfusion_complete':
            if not isinstance(event.get('shot_id'), str) or not ID.fullmatch(event['shot_id']):
                raise BridgeError('Lifecycle events require a stable shot_id')
            number(event['data'].get('elapsed_ms'), 'elapsed_ms', 0, 3600000)
        if 'profile' in event['data']:
            self.validate_profile(event['data']['profile'])
        return canonical(event)

    def validate_profile(self, profile):
        if not isinstance(profile, dict):
            raise BridgeError('Invalid profile')
        for key in ('id', 'revision'):
            if not isinstance(profile.get(key), str) or not ID.fullmatch(profile[key]):
                raise BridgeError('Invalid profile identity/revision')
        if profile.get('mode') not in ('volume', 'scale', 'auto'):
            raise BridgeError('Unknown profile measurement mode')
        target = profile.get('target', {})
        if target.get('unit') != ('mL' if profile['mode'] == 'volume' else 'g'):
            raise BridgeError('Profile target unit does not match measurement mode')
        number(target.get('value'), 'effective target', 0, 250)
        if profile['mode'] == 'auto':
            fallback = profile.get('fallback_target', {})
            if fallback.get('unit') != 'mL' or profile.get('fallback_policy') not in ('during_shot', 'at_start'):
                raise BridgeError('Automatic mode requires an explicit volume backup and fallback policy')
            number(fallback.get('value'), 'backup volume target', 0, 250)
        if not isinstance(profile.get('effective_settings'), dict):
            raise BridgeError('Effective execution settings are required')

    def accept(self, controller, event):
        encoded = self.validate_event(controller, event)
        old = self.store.db.execute('SELECT payload FROM controller_events WHERE controller=? AND event_id=?',
                                   (controller, event['event_id'])).fetchone()
        if old:
            if not self.store.payload_matches(old[0], encoded):
                raise BridgeError('Event ID already has different data')
            return {'event_id': event['event_id'], 'durable': True, 'duplicate': True,
                    'summary':self.summary(), 'cycle':self.cycles.offer(event['session_id']),
                    'display':self.display(event['session_id'])}
        received = time.time()
        memory = (self.session, self.sequence, self.last_event_received, self.observed_profile, self.confirmed_targets, self.measurement_ready)
        active_before = copy.deepcopy(self.engine.active)
        try:
            with self.store.atomic():
                self.store.db.execute('INSERT INTO controller_events VALUES (?,?,?,?,?,?,?)',
                    (controller, event['event_id'], event['session_id'], event['sequence'],
                     event.get('shot_id'), received, encoded))
                live = -2 <= received - timestamp(event['occurred_at']) <= 10
                newest = live and (self.session != event['session_id'] or event['sequence'] > self.sequence)
                if newest:
                    # Session IDs are retired durably; old outbox traffic cannot take ownership again.
                    retired = self.store.get('controller_retired_sessions', [])
                    if event['session_id'] in retired:
                        newest = False
                    else:
                        if self.session and self.session != event['session_id']:
                            retired.append(self.session)
                            self.store.set('controller_retired_sessions', retired[-100:])
                        self.session, self.sequence = event['session_id'], event['sequence']
                        self.store.set('controller_stream', {'session_id':self.session, 'sequence':self.sequence})
                        self.last_event_received = received
                        if self.config.get('controller_type') == 'decaid':
                            self.measurement_ready = event['data'].get('measurement_ready',self.measurement_ready) is True
                        if 'profile' in event['data']:
                            if event['kind'] == 'profile':
                                self.cycle_protocol = event['schema_version'] == 2
                                self.store.set('controller_contract', event['schema_version'])
                            self.observed_profile = event['data']['profile']
                            self.store.set('controller_profile', self.observed_profile)
                            known = self.store.profile(controller, self.observed_profile['id']) or {}
                            known['observed'] = self.observed_profile
                            self.store.save_profile(controller, self.observed_profile['id'], known)
                        self.check_frozen_cycle()
                        self.confirm_idle_targets()
                if event.get('shot_id'):
                    record = self.store.controller_shot(controller, event['shot_id']) or {
                        'controller_id': controller, 'shot_id': event['shot_id'], 'created': received,
                        'external_id': hashlib.sha256((controller + ':' + event['shot_id']).encode()).hexdigest(),
                        'feedback_status': 'collecting', 'events': {}}
                    if event['kind'] in record['events']:
                        raise BridgeError('Shot lifecycle phase already exists under another event ID')
                    record['events'][event['kind']] = event
                    if event['kind'] == 'shot_finished':
                        record['reported_measurements'] = {key:event['data'].get(key) for key in
                            ('pour_volume', 'scale_mass', 'completion_reason', 'quality_flags', 'elapsed_ms',
                             'measurement_source', 'fallback_reason', 'water_volume', 'stop_reason')}
                    if event['kind'] == 'shot_started':
                        if event['schema_version'] == 2:
                            self.cycles.bind(record, event)
                        else:
                            self.bind(record, event, newest)
                    if event['kind'] == 'shot_started' or event['data'].get('state') == 'Espresso':
                        self.engine.maintenance = None
                    if 'shot_finished' in record['events']:
                        self.finalize(record)
                    self.store.save_controller_shot(record)
                if event['kind'] == 'status' and event['data'].get('state') == 'Espresso':
                    self.engine.maintenance = None
        except sqlite3.IntegrityError:
            self.session, self.sequence, self.last_event_received, self.observed_profile, self.confirmed_targets, self.measurement_ready = memory
            self.engine.active = active_before
            raise BridgeError('Session sequence already belongs to another event') from None
        except BaseException:
            self.session, self.sequence, self.last_event_received, self.observed_profile, self.confirmed_targets, self.measurement_ready = memory
            self.engine.active = active_before
            raise
        return {'event_id': event['event_id'], 'durable': True, 'duplicate': False,
                'summary': self.summary(), 'cycle':self.cycles.offer(event['session_id']),
                'display':self.display(event['session_id'])}

    def poll_status(self, session, measurement_ready=None):
        """Poll without journaling; a reconnect first needs a fresh profile."""
        accepted = bool(session and session == self.session
            and 0 <= time.time()-self.last_event_received < 15)
        if accepted:
            self.last_event_received = time.time()
            if self.config.get('controller_type') == 'decaid' and type(measurement_ready) is bool:
                self.measurement_ready = measurement_ready
        return {'session_id':session, 'accepted':accepted}

    def sync_warning(self):
        e, a = self.engine, self.engine.active or {}
        if not e.last_poll or time.time()-e.last_poll >= 10 or time.time()-e.last_native >= 35 or e.error:
            return 'grinder_connection_unavailable'
        if not e.ready or e.recovery:return 'waiting_for_reconciliation'
        if e.maintenance:return 'server_update_in_progress'
        if not self.machine.get('reachable') or time.time()-self.last_event_received >= 15:
            return 'tablet_connection_unavailable'
        if not self.enabled and not self.test_mode:return 'automatic_feedback_disabled'
        if self.config.get('controller_type') == 'decaid' and not self.measurement_ready:
            return 'scale_connection_unavailable'
        targets = self.targets()
        if not targets['confirmed']:return targets.get('reason') or 'targets_not_confirmed'
        if a.get('controller_invalid_reason'):return a['controller_invalid_reason']
        if a.get('phase') in ('attention','aborting'):return 'cycle_needs_attention'
        record = self.store.controller_shot(self.controller_id,a['controller_shot_id']) if a.get('controller_shot_id') else None
        if not a:
            record = next(iter(self.store.controller_history(1, controller_id=self.controller_id)),None)
        if record:
            state = record.get('feedback_status')
            if state == 'history_only':return record.get('reason') or 'shot_not_sent'
            if state in ('awaiting_arm','awaiting_native','awaiting_native_validation'):
                return record.get('reason') or state
            delivery = self.store.shot(record['delivery_id']) if record.get('delivery_id') else None
            if delivery:
                if delivery['state'] in ('uncertain','rejected','failed'):return 'delivery_needs_attention'
                if movement_warning_due(delivery, time.time()):return 'grinder_movement_unconfirmed'
        return None

    def display(self, session):
        """Small skin/popup contract; messages distinguish sending from native receipt."""
        e = self.engine
        warning = self.sync_warning()
        result = {'sync_ready':warning is None, 'sync_warning':warning,
                  'grinder_connected':bool(e.last_poll and time.time()-e.last_poll < 10
                  and time.time()-e.last_native < 35 and not e.error),
                  'active_cycle':(e.active or {}).get('cycle_id'),
                  'cycle_expires_at':(e.active or {}).get('expires_at'), 'notice':None,
                  'grind_position':None}
        # Cached sensor evidence only. A target or stored recipe value is not a
        # measured position; the tablet must also expire this reading locally.
        sensor = e.driver.get('data', {})
        position = sensor.get('ddValueDum')
        age = time.time()-e.last_native
        if (result['grinder_connected'] and 0 <= age < 35
                and sensor.get('ddSensorFailed') is False
                and type(position) in (int, float) and math.isfinite(position) and position >= 0):
            result['grind_position'] = {'um':position/10, 'age_s':age}
        for record in self.store.controller_history(10, controller_id=self.controller_id):
            start = record['events'].get('shot_started', {})
            if start.get('session_id') != session:
                continue
            phase = record.get('feedback_status')
            title, detail = None, None
            delivery = self.store.shot(record['delivery_id']) if record.get('delivery_id') else None
            if delivery:
                phase = delivery.get('outcome') or delivery['state']
                if delivery['state'] == 'verified':
                    title = 'Grinder received shot'
                    detail = {'adjusted':'Burr movement confirmed.', 'movement_pending':'Target updated; burr movement unconfirmed.',
                        'within_tolerance':'Within tolerance · Movement unconfirmed',
                        'held':'Setting held', 'unchanged':'Target unchanged'}.get(phase, delivery.get('message',''))
                    if phase == 'movement_pending' and not movement_warning_due(delivery, time.time()):
                        # Existing tablets ignore this progress phase: no warning
                        # banner or red status while normal readback catches up.
                        phase, title, detail = 'movement_checking', 'Checking adjustment', 'Received · Checking burr position'
                elif delivery['state'] in ('validating','delivering'):
                    title, detail = 'Updating grinder', 'Sending saved result'
                else:
                    title, detail = 'Check feedback', delivery.get('message','Check the server dashboard.')
            elif phase == 'awaiting_arm':
                title, detail = 'Shot saved', 'Press the grinder button to send feedback.'
            elif phase == 'history_only':
                title, detail = 'History only', (record.get('reason') or 'Not eligible').replace('_',' ')
            elif record.get('binding'):
                title, detail, phase = 'Shot matched', 'Matched grind #'+str(record['binding']['grind_id'])+'.', 'matched'
            if title:
                result['notice'] = {'id':record['shot_id']+':'+phase, 'title':title, 'detail':detail}
            break
        return result

    def summary(self):
        active = self.engine.active or {}
        if self.engine.maintenance:
            return 'Updating · Wait before grinding'
        if active.get('phase') == 'attention':
            return 'Feedback paused · ' + active.get('message', 'Check cycle')
        if active.get('phase') == 'aborting':
            return 'History only · Clearing wait'
        if active.get('controller_shot_id'):
            shot = self.store.controller_shot(self.controller_id, active['controller_shot_id']) or {}
            if shot.get('feedback_status') == 'awaiting_arm':
                remaining = max(0, int(active.get('expires_at', 0)-time.time()))
                return f'Shot saved · Press grinder · {remaining} s remaining'
            if shot.get('feedback_status') == 'awaiting_native':
                return 'Shot saved · Verifying devices'
            if shot.get('binding_pending'):
                return 'Verifying grind'
        targets = self.targets()
        if not targets['confirmed']:
            if targets.get('reason') == 'set_grinder_targets':
                return f"Set grinder slot {targets['recipe_index']} to {targets['yield_g']:g} g / {targets['target_time_s']:g} s · Keep dose"
            return 'Profile setup required · History only'
        if not self.enabled and not self.test_mode:
            return 'Feedback disabled · Events saved'
        if not active:
            recent = next(iter(self.store.controller_history(1, controller_id=self.controller_id)), {})
            if recent.get('feedback_status') == 'history_only':
                return 'Last shot: history only · New grind required'
        if self.mode == 'xenia_one_press' and not (self.engine.active or {}).get('starts'):
            if self.cycle_protocol:
                return 'Extract on Decent · One grinder press before or after'
            return 'Targets verified · Arm feedback → extract on Decent'
        return 'Targets verified · Extract on Decent'

    def commission(self, profile_id, data):
        p = self.observed_profile
        if not p or p['id'] != profile_id or data.get('revision') != p['revision']:
            raise BridgeError('Commission the currently observed profile revision')
        if self.engine.active or self.machine.get('state') == 'Espresso':
            raise BridgeError('Wait until the current cycle has finished')
        if data.get('suitable_for_feedback') is not True:
            raise BridgeError('Confirm that brew time can respond to grind changes for this profile')
        index = data.get('recipe_index')
        if type(index) is not int or not recipe_for(self.engine.native, index):
            raise BridgeError('Choose an existing grinder recipe slot')
        factor = number(data.get('grams_per_ml', 1.0), 'conversion factor', 0.1, 3)
        target_ms = round(number(data.get('target_time_s', 24), 'target time', 10.001, 80) * 1000)
        record = {'observed': p, 'commissioned_revision': p['revision'], 'mode': p['mode'],
                  'execution_signature':execution_signature(p),
                  'recipe_index': index, 'grams_per_ml': factor, 'target_time_ms': target_ms,
                  'suitable_for_feedback': True}
        self.store.save_profile(self.controller_id, profile_id, record)
        self.confirm_idle_targets()
        return self.targets(p)

    def confirm_idle_targets(self):
        if self.engine.active:
            return
        targets = self.targets()
        if not targets['confirmed'] or time.time()-self.last_event_received > 15:
            self.confirmed_targets = None
        elif not self.confirmed_targets or self.confirmed_targets['targets'] != targets:
            self.confirmed_targets = {'targets':copy.deepcopy(targets), 'confirmed_at':time.time()}
        self.store.set('controller_confirmed_targets', self.confirmed_targets)

    def freeze_cycle(self, active):
        proof = self.confirmed_targets
        if not proof or not self.targets()['confirmed'] or time.time()-self.last_event_received > 15:
            active['controller_invalid_reason'] = 'targets_not_confirmed_before_grind'
        else:
            active['controller_target_proof'] = copy.deepcopy(proof)

    def check_frozen_cycle(self):
        a = self.engine.active
        if not a or a.get('exposed') or not a.get('controller_target_proof'):
            return
        targets = a['controller_target_proof']['targets']
        native = recipe_for(self.engine.native, targets['recipe_index'])
        if self.observed_profile != targets['profile']:
            a['controller_invalid_reason'] = 'effective_profile_changed_during_cycle'
        elif not native or recipe_signature(native) != recipe_signature(targets['native']):
            a['controller_invalid_reason'] = 'native_recipe_changed_during_cycle'
        self.engine.persist()

    def targets(self, profile=None):
        p = profile or self.observed_profile
        if not p:
            return {'confirmed': False, 'reason': 'waiting_for_controller_profile'}
        c = self.store.profile(self.controller_id, p['id']) or {}
        if c.get('execution_signature') != execution_signature(p) or c.get('mode') != p['mode']:
            return {'confirmed': False, 'reason': 'profile_setup_required', 'profile': p}
        factor = c['grams_per_ml']
        desired = round(p['target']['value'] * (factor if p['mode'] == 'volume' else 1) * 1000)
        native = recipe_for(self.engine.native, c['recipe_index'])
        backup_matches = p['mode'] != 'auto' or round(p['fallback_target']['value'] * factor * 1000) == desired
        confirmed = bool(native and desired > 0 and native['recipe_brew_weight'] == desired
                         and native['recipe_brew_time'] == c['target_time_ms']
                         and time.time() - self.engine.last_native <= 35 and backup_matches)
        label = {'volume':'Uncalibrated volume estimate', 'scale':'Scale measurement',
                 'auto':'Scale preferred; labelled volume-estimate backup'}[p['mode']]
        return {'confirmed': confirmed, 'reason': None if confirmed else ('backup_target_mismatch' if not backup_matches else 'set_grinder_targets'),
                'profile': p, 'recipe_index': c['recipe_index'], 'yield_g': desired / 1000,
                'target_time_s': c['target_time_ms'] / 1000, 'grams_per_ml': c['grams_per_ml'],
                'measurement_label': label,
                'native': native}

    def bind(self, record, event, live, *, recheck=False):
        try:
            if not live or ('shot_finished' in record['events'] and not recheck):
                raise BridgeError('delayed_or_reordered_start')
            e, a = self.engine, self.engine.active
            if a and a.get('protocol') == 2:
                raise BridgeError('controller_contract_changed_during_cycle')
            if recheck:
                reserved = record.get('binding_pending') or {}
                if time.time() >= reserved.get('deadline', 0):
                    raise BridgeError('grinder_state_refresh_timeout')
                if (not a or reserved.get('cycle_id') != a['cycle_id']
                        or reserved.get('grind_uuid') != a.get('grind_uuid')
                        or reserved.get('grind_id') != a.get('grind_id')
                        or a.get('controller_shot_id') != record['shot_id']
                        or reserved.get('event_id') != event['event_id']):
                    raise BridgeError('active_cycle_changed_during_grind_validation')
                if reserved.get('session_id') != self.session:
                    raise BridgeError('controller_restarted_during_shot')
                if reserved.get('transport') != self.mode:
                    raise BridgeError('feedback_transport_changed_during_grind_validation')
            p = event['data'].get('profile')
            if not p or p != self.observed_profile:
                raise BridgeError('profile_not_current')
            targets = self.targets(p)
            if not targets['confirmed']:
                raise BridgeError(targets['reason'])
            if not e.ready or not a or not a.get('grind_uuid') or a['phase'] not in ('armed', 'brewing'):
                raise BridgeError('no_fresh_correlated_grind')
            self.check_frozen_cycle()
            if a.get('controller_invalid_reason'):
                raise BridgeError(a['controller_invalid_reason'])
            proof = a.get('controller_target_proof')
            if not proof or proof['targets'] != targets:
                raise BridgeError('targets_not_frozen_for_grind')
            if a.get('controller_shot_id') and a['controller_shot_id'] != record['shot_id']:
                raise BridgeError('grind_already_bound_to_another_shot')
            if e.recovery:
                raise BridgeError('stale_native_state')
            native = e.native
            grind = native.get('grinds', [{}])[0]
            if grind.get('grind_uuid') != a['grind_uuid'] or grind['recipe_index'] != targets['recipe_index']:
                raise BridgeError('grinder_recipe_mismatch')
            if (grind.get('recipe_data_id') != targets['native']['recipe_data_id']
                    or grind.get('recipe_brew_weight') != round(targets['yield_g']*1000)
                    or grind.get('recipe_brew_time') != round(targets['target_time_s']*1000)
                    or timestamp(grind['grind_date_time']) < proof['confirmed_at'] - 2):
                raise BridgeError('grind_predates_confirmed_recipe')
            age = timestamp(event['occurred_at']) - timestamp(grind['grind_date_time'])
            if not 0 <= age <= 290:
                raise BridgeError('grind_expired')
            # Reuse native eligibility validation, without creating any delivery.
            make_plan(native, {'source':'de1','shot_id':record['external_id'], 'valid':True,
                              'duration_ms':24000,'weight_g':45}, grind['grind_id'])
            pending = native.get('pending_brew') or {}
            if pending.get('grinduuid') != grind['grind_uuid']:
                raise BridgeError('native_pending_mismatch')
            if time.time() - e.last_native > 6:
                if recheck:
                    raise BridgeError('stale_native_state')
                # Reserve the observed cycle, acknowledge storage immediately, and
                # request fresh native validation without blocking either HTTP peer.
                # Never select a different grind when that read returns.
                now = time.time()
                reserved = {'requested_at': now, 'deadline': now + 10,
                    'cycle_id': a['cycle_id'], 'grind_uuid': a['grind_uuid'],
                    'grind_id': a['grind_id'], 'event_id': event['event_id'],
                    'session_id': event['session_id'], 'transport': self.mode,
                    'initial_native_age_s': now - e.last_native}
                record.update(binding=None, binding_pending=reserved,
                    feedback_status='awaiting_native_validation', reason='waiting_for_fresh_grinder_state')
                a['controller_shot_id'] = record['shot_id']
                self.engine.persist()
                self.engine.refresh_requested = True
                self.store.event('shot_binding_refresh_requested', shot_id=record['shot_id'], **reserved)
                return
            record.update(binding={'cycle_id':a['cycle_id'], 'grind_uuid':a['grind_uuid'],
                'grind_id':grind['grind_id'], 'grind_time':grind['grind_date_time'],
                'recipe_signature':recipe_signature(targets['native']), 'targets':targets,
                'session_id':event['session_id'], 'transport':self.mode}, reason=None)
            if recheck:
                record['binding_validation'] = dict(record.pop('binding_pending'), verified_at=time.time())
                record['feedback_status'] = 'collecting'
            a['controller_shot_id'] = record['shot_id']
            self.engine.persist()
        except BridgeError as exc:
            self.reject_binding(record, event, str(exc))

    def reject_binding(self, record, event, reason):
        # Keep correlation failure separate from later measurement progress.
        record.pop('binding_pending', None)
        a = self.engine.active or {}
        evidence = {'reason': reason, 'received_at': time.time(),
            'event_age_s': time.time() - timestamp(event['occurred_at']),
            'native_age_s': time.time() - self.engine.last_native,
            'cycle_id': a.get('cycle_id'), 'phase': a.get('phase'),
            'grind_uuid': a.get('grind_uuid'),
            'pending_grind_uuid': (self.engine.native.get('pending_brew') or {}).get('grinduuid'),
            'pending_brew': copy.deepcopy(self.engine.native.get('pending_brew')),
            'latest_grind': copy.deepcopy((self.engine.native.get('grinds') or [None])[0]),
            'native_recipe': copy.deepcopy(recipe_for(self.engine.native, a.get('recipe_index'))),
            'ready': self.engine.ready, 'recovery': self.engine.recovery}
        record.update(reason=reason, binding=None, binding_failure=evidence, feedback_status='history_only')
        self.store.event('shot_binding_rejected', shot_id=record['shot_id'], **evidence)

    def resolve_pending_bindings(self):
        for record in self.store.controller_history(controller_id=self.controller_id):
            pending = record.get('binding_pending')
            if not pending:
                continue
            now = time.time()
            expired = now >= pending['deadline']
            fresh = self.engine.last_native > pending['requested_at'] and now - self.engine.last_native <= 6
            if not expired and not fresh:
                continue
            active_before = copy.deepcopy(self.engine.active)
            try:
                with self.store.atomic():
                    start = record['events']['shot_started']
                    if expired:
                        self.reject_binding(record, start, 'grinder_state_refresh_timeout')
                    else:
                        self.bind(record, start, True, recheck=True)
                    if 'shot_finished' in record['events']:
                        self.finalize(record)
                    self.store.save_controller_shot(record)
            except BaseException:
                self.engine.active = active_before
                raise

    def finalize(self, record):
        if record.get('delivery_id'):
            return
        p = record['events']['shot_finished']['data'].get('profile') or {}
        setup = self.store.profile(self.controller_id, p.get('id', '')) or {}
        factor = (record.get('binding') or {}).get('targets', {}).get('grams_per_ml', setup.get('grams_per_ml', 1.0))
        data, needs_final, measurements = measurement_snapshot(record, factor)
        record['measurements'] = measurements
        final = record['events'].get('shot_finalized')
        if record.get('binding_pending'):
            record.update(feedback_status='awaiting_native_validation', reason='waiting_for_fresh_grinder_state')
            return
        if versioned(record) and record.get('binding') and data.get('completion_reason') not in ('target_volume', 'target_weight'):
            record.update(feedback_status='history_only', reason='not_an_automatic_target_stop')
            return
        if needs_final:
            if not final:
                if not record.get('binding'):
                    record.update(feedback_status='history_only',
                        reason=record.get('binding_failure', {}).get('reason') or record.get('reason') or 'unmatched_shot')
                else:
                    record.update(feedback_status='awaiting_final_mass', reason='waiting_for_final_scale_mass')
                return
        if versioned(record) and not record.get('binding_failure') and (
                not record['events'].get('shot_started') or not record['events'].get('preinfusion_complete')):
            record.update(feedback_status='awaiting_events', reason='waiting_for_shot_lifecycle')
            return
        try:
            start = record['events'].get('shot_started')
            pi = record['events'].get('preinfusion_complete')
            finish = record['events']['shot_finished']
            if not start or not pi:
                raise BridgeError('missing_shot_lifecycle')
            if not record.get('binding'):
                raise BridgeError(record.get('binding_failure', {}).get('reason') or record.get('reason') or 'unmatched_shot')
            if not start['sequence'] < pi['sequence'] < finish['sequence']:
                raise BridgeError('invalid_event_order')
            if len({x['session_id'] for x in (start, pi, finish)}) != 1:
                raise BridgeError('controller_restarted_during_shot')
            if versioned(record) and any(e.get('cycle') != start.get('cycle') or e.get('schema_version') != 2
                                        for e in record['events'].values()):
                raise BridgeError('shot_cycle_identity_changed')
            p = start['data']['profile']
            if finish['data'].get('profile') != p:
                raise BridgeError('profile_changed_during_shot')
            mass = validate_measurements(record, data, factor, needs_final, measurements)
            duration = measurements['duration_ms']
            record['delivery_input'] = {'source':'de1', 'shot_id':record['external_id'],
                'grind_uuid':record['binding']['grind_uuid'], 'duration_ms':duration,
                'weight_g':mass, 'timing_basis':'after_preinfusion', 'valid':True, 'record_only':False}
            if not self.enabled and not self.test_mode:
                raise BridgeError('observation_only')
            if versioned(record):
                if record.get('feedback_status') != 'history_only':
                    self.cycles.advance(record)
            else:
                self.validate_delivery(record)
                record.update(feedback_status='queued', reason=None)
        except BridgeError as exc:
            record.update(feedback_status='history_only', reason=str(exc))

    def validate_delivery(self, record):
        if versioned(record):
            return self.cycles.validate(record)
        b = record.get('binding')
        if not b:
            raise BridgeError('No frozen grind binding')
        e, a = self.engine, self.engine.active
        if not a or a['cycle_id'] != b['cycle_id'] or a.get('grind_uuid') != b['grind_uuid']:
            raise BridgeError('active_cycle_changed')
        self.check_frozen_cycle()
        if a.get('controller_invalid_reason'):
            raise BridgeError(a['controller_invalid_reason'])
        if b['transport'] != self.mode or self.mode == 'observe':
            raise BridgeError('feedback_transport_unavailable')
        if self.mode == 'xenia_one_press' and not a.get('starts'):
            raise BridgeError('grinder_press_required')
        if time.time() - timestamp(b['grind_time']) > 290:
            raise BridgeError('native_pending_window_expired')
        if time.time() - self.last_event_received > 15 or not self.machine['reachable']:
            raise BridgeError('tablet_unreachable')
        if self.observed_profile != b['targets']['profile']:
            raise BridgeError('effective_profile_changed')
        recipe = recipe_for(e.native, b['targets']['recipe_index'])
        if not recipe or recipe_signature(recipe) != b['recipe_signature']:
            raise BridgeError('native_recipe_changed')
        pending = e.native.get('pending_brew') or {}
        if pending.get('grinduuid') != b['grind_uuid'] or pending.get('grindId') != b['grind_id']:
            raise BridgeError('native_pending_grind_changed')
        if not e.native.get('grinds') or e.native['grinds'][0]['grind_uuid'] != b['grind_uuid']:
            raise BridgeError('newer_grind_detected')

    async def dispatch(self):
        self.resolve_pending_bindings()
        for record in self.store.controller_history(controller_id=self.controller_id):
            if (record.get('feedback_status') == 'collecting' and 'shot_finished' not in record.get('events',{})
                    and time.time()-record['created'] > 290):
                record.update(feedback_status='history_only',reason='incomplete_shot_lifecycle')
                self.store.save_controller_shot(record)
            if versioned(record):
                if record.get('feedback_status') in WAITING and 'shot_finished' in record['events']:
                    self.finalize(record)
                    if record.get('feedback_status') == 'awaiting_final_mass' and time.time()-timestamp(
                            record['events']['shot_finished']['occurred_at']) > 20:
                        record.update(feedback_status='history_only', reason='final_scale_timeout')
                    elif record.get('feedback_status') == 'awaiting_events' and time.time()-record['created'] > 290:
                        record.update(feedback_status='history_only', reason='incomplete_shot_lifecycle')
                    self.store.save_controller_shot(record)
                self.cycles.stop_rejected(record)
                if record.get('feedback_status') != 'queued':
                    continue
            if record.get('feedback_status') == 'awaiting_final_mass':
                stopped = record['events']['shot_finished']
                if time.time()-timestamp(stopped['occurred_at']) > 20:
                    record.update(feedback_status='history_only', reason='final_scale_timeout')
                    self.store.save_controller_shot(record)
            if record.get('feedback_status') != 'queued':
                continue
            try:
                self.validate_delivery(record)
                delivery = await self.engine.submit(record['delivery_input'], controller=record)
                record.update(feedback_status='journaled', delivery_id=delivery['id'])
            except BridgeError as exc:
                if versioned(record) and str(exc) in ('tablet_unreachable', 'waiting_for_reconciliation'):
                    record.update(feedback_status='awaiting_native', reason=str(exc))
                else:
                    record.update(feedback_status='history_only', reason=str(exc))
            self.store.save_controller_shot(record)

    def observe_grinds(self):
        grinds = self.engine.native.get('grinds', [])
        if not self.initialized:
            # Establish a baseline on every start. Historical records never wake the machine.
            self.seen.update(g['grind_uuid'] for g in grinds)
            self.initialized = True
        else:
            for g in reversed(grinds):
                uid = g['grind_uuid']
                if uid in self.seen:
                    continue
                self.seen.add(uid)
                age = time.time() - timestamp(g['grind_date_time'])
                if (self.wake_enabled and g['grind_successfull'] and g['recipe_gbs_enabled']
                        and g['recipe_type_id'] in (1, 2, 5, 6) and g['grind_weight'] > 0 and 0 <= age < 30
                        and self.wake.get('state') not in ('requested', 'warming', 'requesting')):
                    self.wake = {'state':'requested', 'grind_uuid':uid, 'created':time.time()}
                    self.store.set('controller_wake', self.wake)
        self.store.set('controller_seen_grinds', list(self.seen)[-200:])

    async def poll_machine(self):
        if not self.adapter or time.time() - self.last_adapter_check < 2:
            return
        self.last_adapter_check = time.time()
        try:
            status = await self.adapter.status()
            self.machine = {'reachable':True, **status, 'checked_at':time.time()}
            state = status.get('state')
            if self.wake.get('state') == 'requested':
                if state == 'Sleep':
                    # Persist before the request. An uncertain response is checked, never blindly replayed.
                    self.wake['state'] = 'requesting'
                    self.store.set('controller_wake', self.wake)
                    await self.adapter.wake()
                    self.wake['state'] = 'warming'
                else:
                    self.wake['state'] = 'ready' if state == 'Idle' and status.get('substate') == 'ready' else 'warming'
            elif self.wake.get('state') in ('requesting', 'warming', 'uncertain'):
                if state == 'Idle' and status.get('substate') == 'ready':
                    self.wake['state'] = 'ready'
                elif time.time() - self.wake['created'] > 290:
                    self.wake['state'] = 'expired'
        except Exception:
            self.machine = {'reachable':False, 'state':'unknown', 'substate':'unknown', 'checked_at':time.time()}
            if self.wake.get('state') == 'requesting':
                self.wake['state'] = 'uncertain'
        if self.wake.get('state') in ('requested', 'warming', 'requesting', 'uncertain') and time.time()-self.wake['created'] > 290:
            self.wake['state'] = 'expired'
        self.store.set('controller_wake', self.wake)

    async def reconcile_history(self):
        """Archive recovery evidence only; a finalized file cannot retroactively bind a grind."""
        try:
            rows = await self.adapter.history()
            added = 0
            with self.store.atomic():
                for row in rows:
                    digest = hashlib.sha256(row['raw'].encode()).hexdigest()
                    changed = self.store.db.execute('INSERT OR IGNORE INTO controller_archives VALUES (?,?,?,?,?)',
                        (self.controller_id,row['filename'],digest,time.time(),row['raw']))
                    added += changed.rowcount
                self.history_reconciliation = {'state':'archived', 'checked_at':time.time(),
                    'records_checked':len(rows), 'new_versions':added,
                    'note':'Historical files retained for review. No feedback is reconstructed from them.'}
        except Exception:
            self.history_reconciliation = {'state':'unavailable','checked_at':time.time()}
        self.store.set('controller_history_reconciliation',self.history_reconciliation)

    def status(self):
        history = self.store.controller_history(30, controller_id=self.controller_id)
        for record in history:
            record.pop('events', None)
            if record.get('delivery_id'):
                delivery = self.store.shot(record['delivery_id'])
                record['delivery'] = {k:delivery.get(k) for k in ('state','outcome','message','exposed_at',
                    'verified_at','target_before_um','target_after_um','physical_after_um')} if delivery else None
        return {'controller_id':self.controller_id, 'feedback_mode':self.mode, 'enabled':self.enabled,
                'event_contract':2 if self.cycle_protocol else 1, 'cycle':self.cycles.offer(),
                'supervised_test':self.test_mode, 'wake_enabled':self.wake_enabled, 'recipe_control':'manual_verified',
                'validation':{'complete':self.validation_complete,
                    'daily_use':self.enabled and not self.validation_complete,
                    'outstanding_checks':self.config.get('daily_use', {}).get('outstanding_checks',
                        ['Physical acceptance remains incomplete'] if self.config.get('community_beta') else [])},
                'machine':self.machine, 'wake':self.wake, 'targets':self.targets(),
                'history_reconciliation':self.history_reconciliation,
                'summary':self.summary(), 'sync_ready':self.sync_warning() is None,
                'sync_warning':self.sync_warning(), 'recipes':[{'index':r['recipe_index'],
                    'dose_g':r.get('recipe_amount', 0)/1000, 'yield_g':r.get('recipe_brew_weight', 0)/1000,
                    'time_s':r.get('recipe_brew_time', 0)/1000} for r in self.engine.native.get('recipes', [])],
                'profile':self.observed_profile, 'profiles':self.store.profiles(),
                'event_connected':time.time()-self.last_event_received < 15, 'shots':history}
