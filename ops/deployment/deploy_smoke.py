"""Portable isolated release check; runs as coffee-sync-check, never root."""
import asyncio
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import uuid

sys.path.insert(0,sys.argv[1])
from controllers import Controllers
from engine import Engine
from store import Store
from result_analysis import small_change_within_tolerance


class Reader:
    def __init__(self):
        uid=str(uuid.uuid4());stamp=datetime.now(timezone.utc).isoformat()
        self.state={'grinds':[{'grind_uuid':uid,'grind_id':1,'grind_date_time':stamp,'grind_weight':18000,
            'recipe_index':3,'recipe_data_id':7,'recipe_type_id':2,'recipe_gbs_enabled':1,'grind_successfull':1,
            'recipe_brew_weight':36000,'recipe_brew_time':24000}], 'brews':[],
            'recipes':[{'recipe_index':3,'recipe_active':1,'recipe_data_id':7,'recipe_type_id':2,
                'recipe_brew_weight':36000,'recipe_brew_time':24000,'recipe_amount':18000,
                'recipe_gbs_data':{'coursenessDeciMym':1300,'gbsMode':'StandardGbs'}}],
            'pending_brew':{'grindId':1,'grinduuid':uid,'gbsMode':'StandardGbs','doseIndex':2},
            'algorithm_states':[],'motion_alert_events':[],'grind_size_prediction':None}
    async def read(self):return copy.deepcopy(self.state),{'data':{'gbsState':'Idle','grindState':'Idle'}}


async def check(directory, short=False):
    from app import create_app
    from control import command
    e=Engine(Store(Path(directory)/'check.sqlite3'),Reader())
    c=e.controllers=Controllers(e,{'supervised_test':True,'feedback_mode':'xenia_one_press'})
    await e.refresh();e.ready=True;c.machine={'reachable':True,'state':'Idle'}
    p={'id':'test','revision':'revision1','adapter_version':'classic-0.5','mode':'volume',
       'target':{'value':36,'unit':'mL'},'effective_settings':{'program':'pressure'}}
    def event(n,kind,**data):
        return {'schema_version':2,'event_id':'e'+str(n),'session_id':'check-session','sequence':n,
            'kind':kind,'occurred_at':datetime.now(timezone.utc).isoformat(),
            **({'shot_id':'shot1','cycle':claim} if kind!='profile' else {}),
            'data':{'profile':p,'elapsed_ms':0,**data}}
    c.accept('classic-de1',event(0,'profile'))
    c.commission('test',{'revision':'revision1','recipe_index':3,'suitable_for_feedback':True})
    e.brewratio({'SYNC_BEVERAGE_WEIGHT':36,'SYNC_FILTER':2});await e.refresh();e.mako()
    claim=c.cycles.offer();assert claim
    c.accept('classic-de1',event(1,'shot_started',measurement_source='volume_estimate'))
    c.accept('classic-de1',event(2,'preinfusion_complete',elapsed_ms=5000))
    done=event(3,'shot_finished',elapsed_ms=29000,completion_reason='target_volume',quality_flags=[],
        pour_volume={'value':36.2,'unit':'mL','source':'de1_pour_volume'},scale_mass=None)
    if short:
        e.execute({'ID':9});e.mako()
        done['data']['elapsed_ms']=5330
    c.accept('classic-de1',done);await c.dispatch();await e.refresh();await c.dispatch()
    if short:
        assert c.display(c.session)['grinder_connected'] and not c.display(c.session)['sync_ready']
        result=e.mako()
        assert result['MA_STATUS']==4 and result['MA_EXTRACTION_STATUS']==2
        assert result['PU_SENS_FLOW_METER_VOLUME']==0 and not e.store.recent()
        e.reader.state['pending_brew']=None;await e.refresh()
        assert e.active is None
        await e.close();e.store.close();return
    assert e.store.controller_shot('classic-de1','shot1')['feedback_status']=='awaiting_arm'
    assert not e.store.recent()
    try:command(e,{'command':'enter','operation':'a'*64})
    except Exception:pass
    else:raise AssertionError('Maintenance interrupted a late-arming wait')
    # Restart while waiting, then press after extraction; original result must survive.
    e=Engine(e.store,e.reader);c=e.controllers=Controllers(e,{'supervised_test':True,'feedback_mode':'xenia_one_press'})
    c.machine={'reachable':True,'state':'Idle'}
    import time
    c.last_event_received=time.time()
    await e.refresh();await e.reconcile();e.ready=True;e.mako()
    e.execute({'ID':9});await c.dispatch();await e.refresh();await c.dispatch();e.mako();e.mako()
    await asyncio.gather(*list(e.tasks))
    result=e.mako();assert result['MA_STATUS']==4 and result['REAL_EXTRACTION_TIME_MS']==24000
    assert result['PU_SENS_FLOW_METER_VOLUME']==36.2
    assert c.accept('classic-de1',done)['duplicate'] and len(e.store.recent())==1
    await e.close()
    e=Engine(e.store,e.reader);c=e.controllers=Controllers(e,{'supervised_test':True,'feedback_mode':'xenia_one_press'})
    await e.refresh();await e.reconcile();e.ready=True
    assert e.mako()['PU_SENS_FLOW_METER_VOLUME']==0
    assert e.store.recent()[0]['state']=='uncertain'
    assert e.store.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    e.store.close()


sensor={'data':{'ddValueDum':999,'agsaHallSensorCounts':100,'ddSensorFailed':False,
    'agsaStatus':'Normal','grindState':'Idle','gbsState':'Idle'}}
native={'pending_brew':None,'motion_alert_events':[]}
assert small_change_within_tolerance(1003,997,sensor,sensor,native)
assert not small_change_within_tolerance(1010,997,sensor,sensor,native)
fault=copy.deepcopy(sensor);fault['data']['ddSensorFailed']=True
assert not small_change_within_tolerance(1003,997,sensor,fault,native)

with tempfile.TemporaryDirectory(prefix='coffee-sync-check-') as directory:
    asyncio.run(check(directory))
    rejected=Path(directory)/'rejected';rejected.mkdir();asyncio.run(check(rejected,short=True))
print(json.dumps({'passed':True,'checks':['imports','late-arming','maintenance-gate','unexposed-restart','exposed-no-replay','durable-duplicate','journal-integrity','failed-shot-cleanup','sync-warning','tiny-target-tolerance']}))
