"""Run the actual JavaScript adapter against the pinned Decaid host contract."""
from pathlib import Path
import shutil
import subprocess
import asyncio
import json
import time

import pytest

from controllers import Controllers
from engine import Engine
from event_fields import timestamp
from store import Store
from support import FakeReader


def test_decaid_javascript_contract():
    node = shutil.which('node')
    assert node, 'Node.js is required for Decaid release validation'
    result = subprocess.run([node,'--test',str(Path(__file__).with_name('decaid.test.cjs'))],
        capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr


def javascript_capture(start, offer=None, fault=None):
    helper=Path(__file__).with_name('decaid_support.cjs')
    code="""
const fs=require('node:fs'),h=require(process.argv[1]),input=JSON.parse(fs.readFileSync(0,'utf8'));
const r=h.runtime({startTime:input.start}),j=h.journal(r),p=r.SyncFields.profile(h.workflow);
if (!input.offer) console.log(JSON.stringify({profile:p,session:j.box.state.session}));
else (async()=>{const s=h.shot(r,{offer:input.offer,scaleLost:input.fault==='scaleLost'});
    r.advance(4000);await s.capture.stored('live-1');s.flush();console.log(JSON.stringify(s.events()));})();
"""
    result=subprocess.run(['node','-e',code,str(helper)],input=json.dumps({'start':start,'offer':offer,'fault':fault}),
        text=True,capture_output=True,check=True,timeout=10)
    return json.loads(result.stdout)


@pytest.mark.parametrize('fault',[None,'scaleLost','wrong_cycle'])
def test_actual_javascript_events_through_python_cycle_and_delivery(tmp_path,monkeypatch,fault):
    async def run():
        # The native grind and offer precede the live extraction.
        start=int(time.time()*1000)+1000
        context=javascript_capture(start)
        reader=FakeReader()
        for owner in ('recipes','grinds'):reader.state[owner][0]['recipe_brew_weight']=36000
        engine=Engine(Store(tmp_path/'decaid.sqlite3'),reader)
        c=engine.controllers=Controllers(engine,{'controller_id':'decaid-de1','controller_type':'decaid',
            'supervised_test':True,'feedback_mode':'xenia_one_press'})
        await engine.refresh();engine.ready=True;c.machine={'reachable':True,'state':'Idle','substate':'ready'}
        p=context['profile']
        from datetime import datetime,timezone
        row={'schema_version':2,'event_id':context['session']+'-0','session_id':context['session'],'sequence':0,
            'occurred_at':datetime.fromtimestamp(start/1000,timezone.utc).isoformat(),'kind':'profile',
            'data':{'profile':p,'measurement_ready':True}}
        c.accept('decaid-de1',row)
        c.commission(p['id'],{'revision':p['revision'],'recipe_index':2,'suitable_for_feedback':True,'target_time_s':24})
        engine.brewratio({'SYNC_BEVERAGE_WEIGHT':36,'SYNC_FILTER':2});await engine.refresh();engine.mako()
        offer=c.cycles.offer();assert offer
        events=javascript_capture(start,offer,fault)
        clock=[start/1000]
        def tick():
            clock[0]+=0.001
            return clock[0]
        monkeypatch.setattr(time,'time',tick)
        for row in events:
            if fault=='wrong_cycle':row['cycle']['grind_uuid']='unrelated-native-grind'
            clock[0]=max(clock[0],timestamp(row['occurred_at'])+0.1)
            engine.mako()  # The independent grinder poll loop remains active.
            ack=c.accept('decaid-de1',row)
            assert ack['durable']
            assert c.accept('decaid-de1',row)['duplicate']
            await engine.refresh()
        await c.dispatch();await engine.refresh();await c.dispatch()
        record=engine.store.controller_shot('decaid-de1','live-1')
        if fault:
            assert record['feedback_status']=='history_only'
            assert not engine.store.recent()
        else:
            assert record['feedback_status']=='awaiting_arm'
            engine.execute({'ID':9});await c.dispatch();await engine.refresh();await c.dispatch()
            engine.mako();engine.mako();await asyncio.gather(*list(engine.tasks))
            result=engine.mako()
            assert result['REAL_EXTRACTION_TIME_MS']==24000
            assert result['PU_SENS_FLOW_METER_VOLUME']==36.2
            assert len(engine.store.recent())==1
        await engine.close();engine.store.close()
    asyncio.run(run())
