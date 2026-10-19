"""Promotion checks run against real isolated SQLite files, never real systemd units."""
from support import accepted
from pathlib import Path
import sys
import time
import pytest
from store import Store

OPS=Path(__file__).resolve().parents[2]/'ops'
sys.path.insert(0,str(OPS))
from validate_receipt import validate


@pytest.mark.parametrize('fault',['none','duplicate','missing_observation','wrong_mode','not_verified','old_release'])
def test_promotion_requires_journal_and_hardware_observations(tmp_path,fault):
    db,proof=accepted(tmp_path)
    if fault=='duplicate':proof['cycles'][2]['shot_id']='shot-0'
    if fault=='missing_observation':proof['cycles'][1]['prompt_cleared']=False
    if fault=='wrong_mode':proof['mode']='xenia_completion_only'
    if fault=='not_verified':
        s=Store(db);s.update_shot('delivery-2',state='uncertain');s.close()
    if fault=='none':assert validate(proof,db,'xenia_one_press')
    else:
        with pytest.raises(ValueError):validate(proof,db,'xenia_one_press',not_before=time.time()+5 if fault=='old_release' else 0)
