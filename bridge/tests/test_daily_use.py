"""Persistent operation is distinct from complete hardware acceptance."""
import asyncio
from pathlib import Path
import sys
import time

import pytest

from controllers import Controllers
from engine import Engine
from store import Store
from support import setup, complete
from support import FakeReader, prepare_done
from support import accepted

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ops'))
from validate_receipt import validate, validate_daily_use


def authorization(cycles):
    return {'schema_version':1, 'mode':'xenia_one_press', 'owner_requested':True,
            'owner_request':'Make sync the normal service.', 'validation_complete':False,
            'outstanding_checks':['Wi-Fi recovery', 'Mid-shot scale loss'],
            'wake_from_sleep_verified':True, 'cycles':cycles[:2]}


@pytest.mark.parametrize('fault', ['none','no_request','claims_acceptance','no_checks','no_button',
                                  'duplicate','unverified','wrong_native','no_prompt'])
def test_daily_authorization_keeps_actual_evidence_and_incomplete_status(tmp_path, fault):
    db, full = accepted(tmp_path)
    proof = authorization(full['cycles'])
    # An intervening observation stays in history; it cannot become acceptance evidence.
    s=Store(db)
    s.save_controller_shot({'controller_id':'classic-de1','shot_id':'observation','created':time.time(),
                           'feedback_status':'history_only','reason':'observation_only'})
    if fault=='unverified':s.update_shot('delivery-0',state='uncertain')
    if fault=='wrong_native':s.update_shot('delivery-0',grind_uuid='wrong-grind')
    s.close()
    if fault=='no_request':proof['owner_requested']=False
    if fault=='claims_acceptance':proof['validation_complete']=True
    if fault=='no_checks':proof['outstanding_checks']=[]
    if fault=='no_button':proof['mode']='xenia_completion_only'
    if fault=='duplicate':proof['cycles'][1]=proof['cycles'][0]
    if fault=='no_prompt':proof['cycles'][0]['prompt_cleared']=False
    if fault=='none':
        assert validate_daily_use(proof,db)
        with pytest.raises(ValueError):validate(proof,db,'xenia_one_press')
    else:
        with pytest.raises(ValueError):validate_daily_use(proof,db)


@pytest.mark.parametrize('fault', ['none','offline','manual','missing_press'])
def test_persistent_feedback_retains_cycle_guards(tmp_path, fault):
    async def run():
        e,c,r=await setup(tmp_path)
        conf={'enabled':True,'feedback_mode':'xenia_one_press','wake_enabled':True,
              'daily_use':authorization([{'shot_id':'reviewed-1'},{'shot_id':'reviewed-2'}])}
        persistent=Controllers(e,conf)
        persistent.machine=c.machine
        persistent.last_event_received=c.last_event_received
        e.controllers=persistent
        if fault=='offline':persistent.machine['reachable']=False
        if fault=='missing_press':e.active['starts']=0
        complete(persistent,**({'completion_reason':'manual_or_unknown'} if fault=='manual' else {}))
        await persistent.dispatch()
        if fault=='none':
            await prepare_done(e)
            assert e.mako()['MA_STATUS']==4
        else:
            assert not e.store.recent()
            assert e.mako()['MA_STATUS']!=4
        status=persistent.status()
        assert status['enabled'] and not status['supervised_test']
        assert status['validation']=={'complete':False,'daily_use':True,
            'outstanding_checks':['Wi-Fi recovery','Mid-shot scale loss']}
        e.store.close()
    asyncio.run(run())


def test_incomplete_authorization_cannot_enable_no_button(tmp_path):
    e=Engine(Store(tmp_path/'db'),FakeReader())
    with pytest.raises(ValueError):
        Controllers(e,{'enabled':True,'feedback_mode':'xenia_completion_only',
                       'daily_use':authorization([{},{}])})
    e.store.close()
