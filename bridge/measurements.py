"""Normalize history and qualify feedback without selecting a grinder cycle."""
import copy

from event_fields import number, quantity, timestamp
from grinder_client import BridgeError

# The unversioned/0.3 contract is retained for existing HTTP clients and records.
# Classic 0.4 introduced final scale readings; 0.5 added automatic fallback.
CAPABILITIES = {
    None: {'pour':True, 'final_basis':None},
    'classic-0.3': {'pour':True, 'final_basis':None},
    'classic-0.4': {'pour':True, 'final_basis':'de1_final_espresso_weight'},
    'classic-0.5': {'pour':True, 'final_basis':'de1_final_espresso_weight'},
    'decaid-0.9.0': {'pour':False, 'final_basis':'decaid_finalized_weight'},
}


def measurement_snapshot(record, factor):
    data = copy.deepcopy(record['events']['shot_finished']['data'])
    p = data.get('profile') or {}
    caps = CAPABILITIES.get(p.get('adapter_version'), {})
    final = record['events'].get('shot_finalized')
    needs_final = bool(caps.get('final_basis') and data.get('measurement_source') == 'scale')
    stop_mass = data.get('scale_mass')
    if needs_final and final:
        data['scale_mass'] = final['data'].get('scale_mass')
        if p.get('adapter_version') == 'decaid-0.9.0':
            data['water_volume'] = final['data'].get('water_volume')
    flags = list(data['quality_flags']) if isinstance(data.get('quality_flags'),list) else ['invalid_quality_flags']
    if needs_final and final:
        final_flags = final['data'].get('quality_flags')
        flags.extend(final_flags if isinstance(final_flags,list) else ['invalid_final_quality_flags'])

    def reading(key, unit, source):
        if data.get(key) is None:return None
        try:return quantity(data[key],unit,source)
        except BridgeError:
            flags.append('invalid_'+key)
            return None

    pour = reading('pour_volume','mL','de1_pour_volume') if caps.get('pour') else None
    actual = reading('scale_mass','g','scale')
    water = reading('water_volume','mL','decaid_water_volume')
    pi = record['events'].get('preinfusion_complete')
    duration = None
    if pi and pi['session_id'] == record['events']['shot_finished']['session_id']:
        elapsed = data['elapsed_ms'] - pi['data']['elapsed_ms']
        if elapsed >= 0:duration = round(elapsed)
    result = {'pour_volume_ml':pour,'estimated_mass_g':pour*factor if pour is not None else None,
        'grams_per_ml':factor,'estimate_calibrated':False,'scale_mass_g':actual,
        'scale_mass_basis':data.get('scale_mass_basis','extraction_stop'),
        'duration_ms':duration,'timing_basis':'after_preinfusion','mode':p.get('mode'),
        'completion_reason':data.get('completion_reason'),'quality_flags':flags,
        'selected_source':data.get('measurement_source'),'fallback_reason':data.get('fallback_reason')}
    if water is not None:result['water_volume_ml'] = water
    if data.get('stop_reason') is not None:result['stop_reason'] = data['stop_reason']
    if needs_final:
        result.update(scale_mass_g=actual if final else None,
            scale_mass_at_stop_g=stop_mass.get('value') if isinstance(stop_mass,dict) else None,
            scale_mass_basis='finalized' if final else 'awaiting_finalization')
    return data, needs_final, result


def validate_measurements(record, data, factor, needs_final, measurements):
    start, pi, finish = (record['events'][key] for key in ('shot_started','preinfusion_complete','shot_finished'))
    p = start['data']['profile']
    caps = CAPABILITIES.get(p.get('adapter_version'))
    if caps is None:raise BridgeError('unsupported_measurement_adapter')
    if needs_final:
        if record.get('reason') == 'bridge_restarted_before_final_mass':
            raise BridgeError('bridge_restarted_before_final_mass')
        final = record['events']['shot_finalized']
        fd = final['data']
        if (final['session_id'] != finish['session_id'] or final['sequence'] <= finish['sequence']
                or fd.get('profile') != finish['data']['profile'] or fd['elapsed_ms'] != finish['data']['elapsed_ms']):
            raise BridgeError('invalid_scale_finalization')
        if not 0 <= timestamp(final['occurred_at'])-timestamp(finish['occurred_at']) <= 20:
            raise BridgeError('late_scale_finalization')
        if fd.get('scale_mass_basis') != caps['final_basis'] or fd.get('quality_flags') != []:
            raise BridgeError('invalid_final_scale_mass')
        if p.get('adapter_version') == 'decaid-0.9.0':
            proof = fd.get('provenance',{})
            if (proof.get('live_shot_id') != record['shot_id'] or proof.get('record_id') != record['shot_id']
                    or proof.get('unedited') is not True):
                raise BridgeError('invalid_final_record_provenance')
    if data.get('completion_reason') not in ('target_volume','target_weight'):
        raise BridgeError('not_an_automatic_target_stop')
    if data.get('quality_flags') != []:raise BridgeError('measurement_quality_flags')
    pour = quantity(data.get('pour_volume'),'mL','de1_pour_volume') if caps['pour'] else None
    actual = quantity(data['scale_mass'],'g','scale') if data.get('scale_mass') is not None else None
    if not caps['pour'] and (p['mode'] != 'scale' or not needs_final):
        raise BridgeError('adapter_requires_finalized_scale')
    if not caps['pour'] and data.get('water_volume') is not None:
        quantity(data['water_volume'],'mL','decaid_water_volume')
    duration = round(data['elapsed_ms']-pi['data']['elapsed_ms'])
    number(duration,'extraction duration',10001,80000)
    if pi['data']['elapsed_ms'] < start['data']['elapsed_ms']:raise BridgeError('invalid_preinfusion_time')
    source = 'volume_estimate' if p['mode'] == 'volume' else 'scale'
    if p['mode'] == 'auto':
        source, initial, reason = data.get('measurement_source'),start['data'].get('measurement_source'),data.get('fallback_reason')
        if initial not in ('scale','volume_estimate') or source not in ('scale','volume_estimate'):
            raise BridgeError('missing_automatic_measurement_source')
        if initial == 'volume_estimate' and source != initial:raise BridgeError('measurement_source_changed_after_fallback')
        if initial == 'scale' and source == 'volume_estimate' and p['fallback_policy'] != 'during_shot':
            raise BridgeError('mid_shot_fallback_not_enabled')
        if source == 'volume_estimate' and reason not in ('scale_unavailable_at_start','scale_lost_during_shot'):
            raise BridgeError('missing_fallback_reason')
        if source == 'scale' and reason is not None:raise BridgeError('unexpected_fallback_reason')
        if data['completion_reason'] != ('target_weight' if source == 'scale' else 'target_volume'):
            raise BridgeError('automatic_source_stop_mismatch')
    mass = pour*factor if source == 'volume_estimate' and pour is not None else actual
    if p['mode'] == 'scale' and (actual is None or data['completion_reason'] != 'target_weight'):
        raise BridgeError('missing_scale_or_weight_stop')
    if p['mode'] == 'volume' and data['completion_reason'] != 'target_volume':
        raise BridgeError('measurement_mode_stop_mismatch')
    number(mass,'feedback mass',1,250)
    measurements.update(selected_source=source,duration_ms=duration,quality_flags=[])
    return mass
