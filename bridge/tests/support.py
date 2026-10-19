"""Shared fake devices and contract fixtures; never connect to hardware."""
import asyncio
import copy
from datetime import datetime, timezone
import time
import uuid
from engine import Engine, DEVICE_ID
from controllers import Controllers
from store import Store


def baseline():
    uid = str(uuid.uuid4())
    return {'captured_at': datetime.now(timezone.utc).isoformat(),
      'grinds': [{'grind_id': 21, 'grind_uuid': uid, 'grind_date_time': datetime.now(timezone.utc).isoformat(),
        'recipe_index': 2, 'recipe_data_id': 13, 'grind_ddd': 1320, 'grind_weight': 16500,
        'grind_successfull': 1, 'recipe_gbs_enabled': 1, 'recipe_type_id': 2, 'recipe_brew_time': 24000,
        'recipe_brew_weight': 33000}], 'brews': [],
      'recipes': [{'recipe_index': 2, 'recipe_active': 1, 'recipe_data_id': 13,
        'recipe_gbs_data': {'coursenessDeciMym': 1316, 'gbsMode': 'StandardGbs'},
        'recipe_type_id': 2, 'recipe_brew_time': 24000, 'recipe_brew_weight': 33000, 'recipe_amount': 16500}],
      'pending_brew': {'grindId':21, 'grinduuid':uid, 'gbsMode':'StandardGbs','doseIndex':2,
        'recipeIndex':1,'targetGrindSizeDum':1316,'effectiveGrindSizeDum':1320,'targetBrewTime':24000},
      'algorithm_states': [], 'grind_size_prediction': None, 'motion_alert_events': []}


class FakeReader:
    def __init__(self):
        self.state = baseline()
        self.driver = {'data': {'ddValueDum':1320, 'agsaHallSensorCounts':554, 'ddSensorFailed':False}}
        self.fail = False

    async def read(self):
        if self.fail:
            raise OSError('offline')
        return copy.deepcopy(self.state), copy.deepcopy(self.driver)

    def finish(self, result):
        self.state['brews'].append({'brew_id':1, 'grind_id':21, 'brew_duration':result['REAL_EXTRACTION_TIME_MS'],
          'brew_mass':int(result['PU_SENS_FLOW_METER_VOLUME']*1000), 'brew_validity':1,'brew_device_id':DEVICE_ID})
        self.state['pending_brew'] = None
        self.state['recipes'][0]['recipe_gbs_data']['coursenessDeciMym'] = 1240
        self.driver['data'].update(ddValueDum=1244, agsaHallSensorCounts=234)


async def fixture(tmp_path):
    reader=FakeReader(); engine=Engine(Store(tmp_path/'test.db'),reader)
    await engine.refresh(); engine.ready=True
    engine.brewratio({'SYNC_BEVERAGE_WEIGHT':33,'SYNC_FILTER':2})
    engine.capture_grind(); engine.execute({'ID':9}); engine.mako(); engine.mako()
    return engine,reader


def payload(engine, **changes):
    return {'source':'manual','shot_id':'shot-a','grind_uuid':engine.active['grind_uuid'],
            'weight_g':33.1,'duration_ms':21000,'timing_basis':'first_drip','valid':True,'record_only':False,**changes}


async def prepare_done(engine):
    await asyncio.gather(*list(engine.tasks))


def profile(value=45, revision='rev1', mode='volume'):
    return {'id':'profile1','revision':revision,'title':'Effective override', 'mode':mode,
            'target':{'value':value,'unit':'mL' if mode == 'volume' else 'g'},
            'effective_settings':{'program':'pressure-v1', 'final_desired_shot_volume':value}}


def event(seq, kind, *, p=None, shot='shot1', elapsed=0, **data):
    return {'schema_version':1, 'event_id':f'event-{seq}', 'session_id':'session1', 'sequence':seq,
        'occurred_at':datetime.now(timezone.utc).isoformat(), 'kind':kind,
        **({'shot_id':shot} if kind not in ('status','profile') else {}),
        'data':{'profile':p or profile(),'elapsed_ms':elapsed, **data}}


async def setup(tmp_path, mode='xenia_one_press', p=None):
    r = FakeReader()
    r.state['recipes'][0]['recipe_brew_weight'] = 45000
    r.state['grinds'][0]['recipe_brew_weight'] = 45000
    e = Engine(Store(tmp_path/'journal.db'), r)
    c = e.controllers = Controllers(e, {'feedback_mode':mode, 'supervised_test':True})
    await e.refresh(); e.ready=True
    c.machine = {'reachable':True,'state':'Idle','substate':'ready'}
    c.accept('classic-de1', event(0, 'profile', p=p))
    c.commission('profile1', {'revision':(p or profile())['revision'], 'recipe_index':2,
                            'suitable_for_feedback':True, 'target_time_s':24})
    e.brewratio({'SYNC_BEVERAGE_WEIGHT':45, 'SYNC_FILTER':2});e.capture_grind()
    if mode == 'xenia_one_press':
        e.execute({'ID':9});e.mako();e.mako()
    else:
        e.mako()
    return e,c,r


def complete(c, p=None, **changes):
    events = [event(1,'shot_started',p=p), event(2,'preinfusion_complete',p=p,elapsed=4000),
              event(3,'shot_finished',p=p,elapsed=28000, completion_reason='target_volume',
                    quality_flags=[], pour_volume={'value':45.5,'unit':'mL','source':'de1_pour_volume'},
                    scale_mass=None)]
    events[-1]['data'].update(changes)
    for item in events:
        c.accept('classic-de1',item)
    return events


class Tablet:
    def __init__(self):self.state='Sleep';self.wakes=0;self.fail=False
    async def status(self):
        if self.fail:raise OSError('unreachable')
        return {'state':self.state,'substate':'ready'}
    async def wake(self):self.wakes+=1;self.state='Idle'


async def setup_v2(tmp_path):
    r = FakeReader()
    r.state['recipes'][0]['recipe_brew_weight'] = 45000
    r.state['grinds'][0]['recipe_brew_weight'] = 45000
    e = Engine(Store(tmp_path/'cycles.db'), r)
    c = e.controllers = Controllers(e, {'feedback_mode':'xenia_one_press','supervised_test':True})
    await e.refresh(); e.ready = True
    c.machine = {'reachable':True,'state':'Idle','substate':'ready'}
    p = dict(profile(mode='scale'), adapter_version='classic-0.5')
    pe = event(0,'profile',p=p); pe['schema_version']=2
    c.accept('classic-de1', pe)
    c.commission(p['id'], {'revision':p['revision'],'recipe_index':2,'suitable_for_feedback':True})
    e.brewratio({'SYNC_BEVERAGE_WEIGHT':45,'SYNC_FILTER':2})
    assert not c.cycles.offer()  # Cached native state cannot identify a new announcement.
    await e.refresh()
    claim = c.cycles.offer()
    assert claim
    e.mako()
    return e,c,r,p,claim


def lifecycle(p, claim):
    rows = [event(1,'shot_started',p=p,measurement_source='scale'),
        event(2,'preinfusion_complete',p=p,elapsed=7000),
        event(3,'shot_finished',p=p,elapsed=32763,measurement_source='scale',
            completion_reason='target_weight',quality_flags=[],
            pour_volume={'value':44.4,'unit':'mL','source':'de1_pour_volume'},
            scale_mass={'value':34.8,'unit':'g','source':'scale'}),
        event(4,'shot_finalized',p=p,elapsed=32763,quality_flags=[],
            scale_mass_basis='de1_final_espresso_weight',scale_mass={'value':35.9,'unit':'g','source':'scale'})]
    for row in rows: row.update(schema_version=2,cycle=copy.deepcopy(claim))
    return rows


async def advance(e,c):
    await c.dispatch(); await e.refresh(); await c.dispatch()


def accepted(tmp_path):
    # Assemble three independently verified simulated cycles to test receipt verification itself.
    # These fixtures are never exported as hardware acceptance receipts.
    db=tmp_path/'receipt.db';s=Store(db);observations=[]
    for i in range(3):
        shot_id=f'shot-{i}';uuid=f'grind-{i}';delivery=f'delivery-{i}'
        s.save_controller_shot({'controller_id':'classic-de1','shot_id':shot_id,'created':time.time(),
            'binding':{'transport':'xenia_one_press','grind_uuid':uuid},
            'measurements':{'timing_basis':'after_preinfusion'},'feedback_status':'journaled','delivery_id':delivery})
        s.add_shot({'id':delivery,'grind_uuid':uuid,'grind_id':i,'source':'de1','external_id':shot_id,
            'created':time.time(),'state':'verified','verified_at':time.time(),'input':{},
            'plan':{'record_only':False},'outcome':'held'})
        observations.append({'shot_id':shot_id,'grinder_presses':1,'real_shot':True,'prompt_cleared':True,
            'next_grind_ready':True,'native_adjustment_reviewed':True})
    s.close()
    return db,{'schema_version':1,'mode':'xenia_one_press','consecutive_real_cycles':3,
        'prompt_clearance':True,'native_adjustment_verified':True,'wake_from_sleep_verified':True,'cycles':observations}
