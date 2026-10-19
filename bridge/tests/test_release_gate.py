"""A software pass cannot substitute for acceptance of exact shipped bytes."""
import copy
import importlib.util
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('release_gate',Path(__file__).resolve().parents[2]/'ops/release_gate.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)


@pytest.mark.parametrize('fault',['none','missing','changed_bytes','unchecked','wrong_decaid','no_evidence','duplicate'])
def test_release_needs_exact_artifacts_and_both_physical_controllers(fault):
    manifest={'version':'1.0.0-beta.1','artifacts':{'fixture.zip':{'sha256':'a'*64,'bytes':10}}}
    # Synthetic observations belong only to this isolated validator test.
    app={'app_version':'fixture','tablet_model':'fixture','tablet_os':'fixture','grinder_firmware':'fixture',
        'observed_at':'test only','reviewer':'test only','grinder_model':'E64 WS',
        'checks':dict.fromkeys(gate.CHECKS,True),'private_evidence_sha256':'b'*64}
    release={**copy.deepcopy(manifest),'controllers':{'classic':copy.deepcopy(app),'decaid':copy.deepcopy(app)}}
    release['controllers']['decaid']['app_version']='0.9.0'
    evidence={'schema':1,'releases':[release]}
    if fault=='missing':evidence['releases']=[]
    if fault=='changed_bytes':release['artifacts']['fixture.zip']['bytes']=11
    if fault=='unchecked':release['controllers']['classic']['checks']['mid_shot_scale_loss']=False
    if fault=='wrong_decaid':release['controllers']['decaid']['app_version']='0.8.0'
    if fault=='no_evidence':release['controllers']['decaid'].pop('private_evidence_sha256')
    if fault=='duplicate':evidence['releases'].append(copy.deepcopy(release))
    if fault=='none':assert gate.validate(manifest,evidence)
    else:
        with pytest.raises(ValueError):gate.validate(manifest,evidence)
