"""Failed extraction must release Xenia without sending invalid/old feedback."""
import asyncio
import copy
from datetime import datetime, timezone
import uuid

import pytest
from controllers import Controllers
from engine import Engine
from support import setup_v2, lifecycle, advance
from support import prepare_done


async def rejected(tmp_path, kind='short'):
    e,c,r,p,claim=await setup_v2(tmp_path)
    r.driver['data'].update(gbsState='Idle',grindState='Idle')
    e.execute({'ID':9});assert e.mako()['MA_STATUS']==3
    rows=lifecycle(p,claim)
    if kind=='short':
        rows[2]['data']['elapsed_ms']=7330;rows[3]['data']['elapsed_ms']=7330
        rows[2]['data']['scale_mass']['value']=99.5
        rows[3]['data']['scale_mass']['value']=100.3
    else:
        rows[2]['data']['completion_reason']='manual_or_unknown'
        rows=[rows[0],rows[2]]  # No preinfusion completion or finalized weight.
    for row in rows:c.accept('classic-de1',row)
    await c.dispatch()
    return e,c,r,p,claim,rows


def new_grind(r):
    g=copy.deepcopy(r.state['grinds'][0]);g.update(grind_id=g['grind_id']+1,
        grind_uuid=str(uuid.uuid4()),grind_date_time=datetime.now(timezone.utc).isoformat())
    r.state['grinds'].insert(0,g)
    r.state['pending_brew'].update(grindId=g['grind_id'],grinduuid=g['grind_uuid'])
    return g


@pytest.mark.parametrize('kind',['short','manual'])
def test_failed_shot_stops_virtual_extraction_without_feedback(tmp_path,kind):
    async def run():
        e,c,r,p,claim,rows=await rejected(tmp_path,kind)
        rec=e.store.controller_shot('classic-de1','shot1')
        assert rec['feedback_status']=='history_only'
        assert e.active['phase']=='aborting'
        assert e.mako()['MA_STATUS']==1  # Do not abort based on pre-stop cached state.
        await e.refresh()
        result=e.mako()
        assert (result['MA_STATUS'],result['MA_EXTRACTION_STATUS'])==(4,2)
        assert result['REAL_EXTRACTION_TIME_MS']==result['PU_SENS_FLOW_METER_VOLUME']==0
        assert not e.store.recent()
        assert e.mako()['MA_STATUS']==1  # A second abort needs another native read.
        r.state['pending_brew']=None
        await e.refresh()
        assert e.active is None and e.mako()['MA_STATUS']==1
        assert e.store.cycle(claim['cycle_id'])['closed_at']
        await e.close();e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('announcement',[False,True])
def test_new_native_grind_retires_old_wait_without_aborting_new_grind(tmp_path,announcement):
    async def run():
        e,c,r,p,claim,rows=await rejected(tmp_path)
        old=copy.deepcopy(e.active)
        e.active['phase']='attention';e.persist()
        if announcement:e.brewratio({'SYNC_BEVERAGE_WEIGHT':45,'SYNC_FILTER':2})
        g=new_grind(r)
        await e.refresh()
        assert not e.active or e.active['cycle_id']!=old['cycle_id']
        assert e.mako()['MA_STATUS']==1
        if not announcement:e.brewratio({'SYNC_BEVERAGE_WEIGHT':45,'SYNC_FILTER':2})
        await e.refresh()
        offer=c.cycles.offer()
        assert offer['grind_uuid']==g['grind_uuid']
        # Retried events from the failed shot cannot clear or bind this new cycle.
        for row in rows:c.accept('classic-de1',row)
        await c.dispatch()
        assert e.active['phase']=='armed' and e.active['grind_uuid']==g['grind_uuid']
        fresh=lifecycle(p,offer)
        for i,row in enumerate(fresh):
            row.update(event_id='new-'+str(i),sequence=20+i,shot_id='shot2')
            c.accept('classic-de1',row)
        e.execute({'ID':9});await advance(e,c);e.mako();e.mako();await prepare_done(e)
        result=e.mako()
        assert result['MA_STATUS']==4 and result['MA_EXTRACTION_STATUS']==0
        assert e.store.recent()[0]['grind_uuid']==g['grind_uuid']
        assert len(e.store.recent())==1
        await e.close();e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('replacement',[False,True])
def test_restart_during_abort_never_resumes_brewing_or_aborts_replacement(tmp_path,replacement):
    async def run():
        e,c,r,p,claim,rows=await rejected(tmp_path)
        store=e.store;await e.close()
        if replacement:new_grind(r)
        e=Engine(store,r);c=e.controllers=Controllers(e,{'feedback_mode':'xenia_one_press','supervised_test':True})
        await e.refresh();await e.reconcile();e.ready=True
        assert not e.recovery
        assert e.mako()['MA_STATUS']==1
        if not replacement:
            await e.refresh();result=e.mako()
            assert result['MA_STATUS']==4 and result['MA_EXTRACTION_STATUS']==2
        else:assert e.active is None
        assert not store.recent()
        await e.close();store.close()
    asyncio.run(run())


@pytest.mark.parametrize('guard',['exposed','delivery','same_pending','busy_driver'])
def test_terminal_cleanup_preserves_unresolved_delivery_or_pending_grind(tmp_path,guard):
    async def run():
        e,c,r,p,claim,rows=await rejected(tmp_path)
        if guard=='exposed':e.active['exposed']=True
        if guard=='delivery':e.active['shot_id']='unresolved-delivery'
        if guard!='same_pending':r.state['pending_brew']=None
        if guard=='busy_driver':r.driver['data']['grindState']='Grinding'
        await e.refresh()
        assert e.active and e.active['cycle_id']==claim['cycle_id']
        await e.close();e.store.close()
    asyncio.run(run())
