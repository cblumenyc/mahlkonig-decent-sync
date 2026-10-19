from support import baseline, FakeReader, fixture, payload, prepare_done
import asyncio
import copy
from pathlib import Path
import sys
import time
import uuid

import pytest
from aiohttp import ClientSession, CookieJar
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from engine import Engine, IDENTITY, DEVICE_ID
from grinder_client import BridgeError
from store import Store


def test_success_duplicate_and_restart(tmp_path):
    async def run():
        e,r=await fixture(tmp_path)
        starts=e.active['started'];e.execute({'ID':9});assert e.active['started']==starts
        data=payload(e);t=time.perf_counter();rec=await e.submit(data)
        assert time.perf_counter()-t < .2
        duplicate=await e.submit(data);assert duplicate['id']==rec['id']
        await prepare_done(e)
        assert e.active['phase']=='finishing'
        result=e.mako();assert result['MA_STATUS']==4 and result['PU_SENS_FLOW_METER_VOLUME']==33.1
        r.finish(result);await e.refresh();e.verify()
        saved=e.store.shot(rec['id']);assert saved['state']=='verified' and saved['outcome']=='adjusted'
        assert saved['target_after_um']==124 and saved['physical_after_um']==124.4
        assert (await e.submit(data))['id']==rec['id']
        # Restart with a durable finishing entry must reconcile, never replay status 4.
        e2=Engine(e.store,r);assert e2.mako()['MA_STATUS']==0
        await e2.refresh();await e2.reconcile();e2.ready=True
        assert e2.active is None and e2.mako()['MA_STATUS']==1
        assert len(e.store.recent())==1
        e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('change',[{'valid':False},{'duration_ms':10000},{'duration_ms':80001},
    {'weight_g':float('nan')},{'weight_g':0},{'grind_uuid':'wrong'}, {'timing_basis':'pump_start'},
    {'source':'synthetic-de1'},{'record_only':'yes'}])
def test_invalid_input_never_finishes(tmp_path,change):
    async def run():
        e,r=await fixture(tmp_path)
        with pytest.raises(BridgeError):await e.submit(payload(e,**change))
        assert e.mako()['MA_STATUS']==3
        assert not e.store.recent()
        e.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('mode',['missing_pending','wrong_pending','new_grind','offline'])
def test_validation_failures_never_expose_result(tmp_path,mode):
    async def run():
        e,r=await fixture(tmp_path)
        if mode=='missing_pending':r.state['pending_brew']=None
        if mode=='wrong_pending':r.state['pending_brew']['grinduuid']=str(uuid.uuid4())
        if mode=='new_grind':r.state['grinds'][0]['grind_uuid']=str(uuid.uuid4())
        if mode=='offline':r.fail=True
        rec=await e.submit(payload(e));await prepare_done(e)
        assert e.store.shot(rec['id'])['state']=='rejected'
        assert e.mako()['MA_STATUS']!=4
        e.store.close()
    asyncio.run(run())


def test_record_only_and_unconfirmed_restart(tmp_path):
    async def run():
        e,r=await fixture(tmp_path);r.state['pending_brew']=None
        rec=await e.submit(payload(e,record_only=True));await prepare_done(e)
        result=e.mako();assert result['MA_STATUS']==4
        # No native confirmation at crash: never replay uncertain completion.
        e2=Engine(e.store,r);await e2.refresh();await e2.reconcile();e2.ready=True
        assert e2.mako()['MA_STATUS']==1
        assert e2.store.shot(rec['id'])['state']=='uncertain'
        # A late native record can still confirm exactly once.
        r.finish(result);await e2.refresh();e2.verify()
        assert e2.store.shot(rec['id'])['outcome']=='recorded'
        e.store.close()
    asyncio.run(run())


def test_unrelated_native_brew_does_not_confirm(tmp_path):
    async def run():
        e,r=await fixture(tmp_path);rec=await e.submit(payload(e));await prepare_done(e)
        r.finish(e.mako());r.state['brews'][0]['brew_device_id']='Different scale'
        await e.refresh();e.verify();assert e.store.shot(rec['id'])['state']=='delivering'
        e.store.close()
    asyncio.run(run())


def test_no_press_no_send_and_abort(tmp_path):
    async def run():
        e,r=await fixture(tmp_path);e.active['phase']='armed'
        with pytest.raises(BridgeError):await e.submit(payload(e))
        e.cancel();result=e.mako()
        assert result['MA_STATUS']==4 and result['MA_EXTRACTION_STATUS']==2
        assert result['REAL_EXTRACTION_TIME_MS']==0 and not e.store.recent()
        e.store.close()
    asyncio.run(run())


def test_open_http_origin_peer_and_end_to_end(tmp_path):
    async def run():
        reader=FakeReader();store=Store(tmp_path/'http.db');e=Engine(store,reader)
        conf={'origins':['http://127.0.0.1'], 'allowed_networks':['127.0.0.0/8'],
          'grinder_host':'127.0.0.1'}
        app=create_app(conf,e);server=TestServer(app);await server.start_server()
        # Host is explicitly pinned independently of the ephemeral test port.
        async with ClientSession(cookie_jar=CookieJar(unsafe=True),headers={'Host':'127.0.0.1'}) as c:
            root=str(server.make_url('')).rstrip('/')
            async with c.get(root+'/api/v1/status') as res:assert res.status==200
            async with c.post(root+'/api/v1/login',json={}) as res:assert res.status==404
            async with c.post(root+'/api/v1/refresh',headers={'Origin':'http://evil.invalid'},json={}) as res:assert res.status==403
            async with c.post(root+'/api/v1/refresh',data='{}',headers={'Content-Type':'text/plain'}) as res:assert res.status==415
            async with c.post(root+'/api/v1/refresh',headers={'Sec-Fetch-Site':'cross-site'},json={}) as res:assert res.status==403
            headers={'Origin':'http://127.0.0.1'}
            async with c.post(root+'/api/v1/refresh',json={}) as res:assert res.status==200
            async with c.get(root+'/api/v2/machine') as res:assert await res.json()==IDENTITY
            await e.refresh();e.ready=True
            async with c.post(root+'/api/v2/brewratio',json={'SYNC_BEVERAGE_WEIGHT':33,'SYNC_FILTER':2}) as res:assert res.status==200
            e.capture_grind()
            async with c.post(root+'/api/v2/scripts/execute',json={'ID':9}) as res:assert res.status==200
            for _ in range(2):
                async with c.get(root+'/api/v2/mako') as res:assert (await res.json())['MA_STATUS']==3
            async with c.post(root+'/api/v1/shots',headers=headers,json=payload(e)) as res:
                assert res.status==202;record=await res.json()
            # Background worker is real; emulate only the grinder's consumption.
            deadline=time.time()+3
            while e.active['phase']!='finishing' and time.time()<deadline:await asyncio.sleep(.02)
            async with c.get(root+'/api/v2/mako') as res:result=await res.json()
            reader.finish(result);await e.refresh();e.verify()
            async with c.get(root+'/api/v1/status') as res:
                assert (await res.json())['shots'][0]['outcome']=='adjusted'
            async with c.get(root+'/api/v1/status',headers={'Host':'evil.invalid'}) as res:assert res.status==403
            # Restrict unauthenticated Xenia endpoints to the configured device.
            conf['grinder_host']='192.168.10.3'
            async with c.get(root+'/api/v2/mako') as res:assert res.status==403
            async with c.get(root+'/api/v1/status') as res:assert res.status==200
            async with c.get(root+'/api/v1/shots/missing') as res:assert res.status==404
            async with c.post(root+'/api/v1/shots',json=payload(e)) as res:assert res.status==202
        await server.close()
    asyncio.run(run())


def test_native_pending_must_be_consumed(tmp_path):
    async def run():
        e,r=await fixture(tmp_path);pending=copy.deepcopy(r.state['pending_brew'])
        rec=await e.submit(payload(e));await prepare_done(e)
        r.finish(e.mako());r.state['pending_brew']=pending
        await e.refresh();e.verify();assert e.store.shot(rec['id'])['state']=='delivering'
        r.state['pending_brew']=None;await e.refresh();e.verify()
        assert e.store.shot(rec['id'])['state']=='verified'
        e.store.close()
    asyncio.run(run())


def test_motion_must_reach_new_target(tmp_path):
    async def run():
        e,r=await fixture(tmp_path);rec=await e.submit(payload(e));await prepare_done(e)
        r.finish(e.mako());r.driver['data']['ddValueDum']=1300
        await e.refresh();e.verify()
        assert e.store.shot(rec['id'])['outcome']=='movement_pending'
        r.driver['data']['ddValueDum']=1244;await e.refresh();e.verify()
        assert e.store.shot(rec['id'])['outcome']=='adjusted'
        e.store.close()
    asyncio.run(run())


def test_large_adjustment_hold_is_distinct_from_tolerance():
    from result_analysis import large_change_hold
    b=baseline();b['pending_brew']['targetGrindSizeDum']=1401
    b['algorithm_states']=[{'recipe_index':1,'brew_device':DEVICE_ID,'group_head':0,
        'data':{'type':'GbS3','lastTargetDdInDeciMicroMeters':2004,
                'lastAdjustmentInDeciMicroMeters':1398,'filteredDdTimesBrew':3355.8}}]
    after=copy.deepcopy(b);after['algorithm_states'][0]['data']['lastTargetDdInDeciMicroMeters']=1401
    assert large_change_hold({'baseline':b},after)
    b['algorithm_states'][0]['data']['lastTargetDdInDeciMicroMeters']=1800
    assert not large_change_hold({'baseline':b},after)


def test_backup_preserves_committed_journal(tmp_path):
    import subprocess
    store=Store(tmp_path/'bridge.sqlite3');store.set('proof',{'value':'durable'});store.close()
    script=Path(__file__).resolve().parents[1]/'backup.py'
    subprocess.run([sys.executable,str(script),str(tmp_path/'bridge.sqlite3')],check=True)
    backups=list((tmp_path/'backups').glob('*.sqlite3'));assert len(backups)==1
    restored=Store(backups[0]);assert restored.get('proof')=={'value':'durable'};restored.close()
