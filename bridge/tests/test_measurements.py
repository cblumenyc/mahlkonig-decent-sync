"""Decaid water volume never becomes beverage yield; final records require live proof."""

import pytest

from grinder_client import BridgeError
from measurements import measurement_snapshot, validate_measurements
from support import event, profile


def decaid_record():
    p = dict(profile(mode='scale'),adapter_version='decaid-0.9.0')
    common = {'measurement_source':'scale','quality_flags':[],'completion_reason':'target_weight',
        'water_volume':{'value':72,'unit':'mL','source':'decaid_water_volume'},
        'scale_mass':{'value':43,'unit':'g','source':'scale'},'stop_reason':'targetWeight'}
    rows = [event(1,'shot_started',p=p),event(2,'preinfusion_complete',p=p,elapsed=4000),
        event(3,'shot_finished',p=p,elapsed=28000,**common),
        event(4,'shot_finalized',p=p,elapsed=28000,**{**common,'scale_mass':{'value':45,'unit':'g','source':'scale'},
            'scale_mass_basis':'decaid_finalized_weight',
            'provenance':{'live_shot_id':'shot1','record_id':'shot1','unedited':True}})]
    return {'shot_id':'shot1','events':{row['kind']:row for row in rows}}


@pytest.mark.parametrize('fault',['none','edited','imported','lost_scale','volume_only','missing_source','wrong_basis'])
def test_decaid_final_scale_qualification(fault):
    record = decaid_record()
    finish,final = (record['events'][key]['data'] for key in ('shot_finished','shot_finalized'))
    if fault == 'edited':final['provenance']['unedited'] = False
    if fault == 'imported':final['provenance']['live_shot_id'] = 'other'
    if fault == 'lost_scale':finish['quality_flags'] = ['scale_lost']
    if fault == 'volume_only':
        finish['profile']['mode'] = 'volume'
        final['profile']['mode'] = 'volume'
    if fault == 'missing_source':finish.pop('measurement_source')
    if fault == 'wrong_basis':final['scale_mass_basis'] = 'water_volume'
    data,needs_final,measurements = measurement_snapshot(record,1)
    assert measurements['estimated_mass_g'] is None and measurements['pour_volume_ml'] is None
    assert measurements['water_volume_ml'] == 72
    if fault == 'none':
        assert validate_measurements(record,data,1,needs_final,measurements) == 45
        assert measurements['duration_ms'] == 24000 and measurements['scale_mass_at_stop_g'] == 43
    else:
        with pytest.raises(BridgeError):validate_measurements(record,data,1,needs_final,measurements)
