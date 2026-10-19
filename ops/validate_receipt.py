"""Verify acceptance against the journal; operator observations remain explicit evidence."""
import json
import sqlite3


def validate(proof, database, mode, *, not_before=0):
    def require(condition, message):
        if not condition:raise ValueError(message)
    require(mode == 'xenia_one_press', 'Unimplemented transport')
    require(proof.get('mode')==mode and proof.get('schema_version')==1, 'Receipt mode/version mismatch')
    cycles=proof.get('cycles',[])
    require(len(cycles)==3 and proof.get('consecutive_real_cycles')==3, 'Exactly three consecutive real cycles required')
    require(proof.get('prompt_clearance') is True and proof.get('native_adjustment_verified') is True,
            'Prompt clearance and native adjustment acceptance required')
    require(proof.get('wake_from_sleep_verified') is True, 'A real wake from Sleep must be verified')
    ids=[c.get('shot_id') for c in cycles]
    require(len(set(ids))==3,'Cycle IDs must be distinct')
    with sqlite3.connect(f'file:{database}?mode=ro',uri=True) as db:
        rows=[json.loads(r[0]) for r in db.execute('SELECT record FROM controller_shots ORDER BY rowid DESC LIMIT 3')]
        require([r['shot_id'] for r in reversed(rows)]==ids,'Receipt must describe the latest three consecutive shots')
        uuids=set()
        for observation,shot in zip(cycles,reversed(rows)):
            require(shot.get('created',0)>=not_before,'Cycle predates the prepared release')
            require(all(observation.get(k) is True for k in ('real_shot','prompt_cleared','next_grind_ready','native_adjustment_reviewed')),
                    'Each cycle needs explicit hardware observations')
            presses=1
            require(type(observation.get('grinder_presses')) is int and observation['grinder_presses']==presses,
                    'Observed press count does not match the selected mode')
            require(shot.get('binding',{}).get('transport')==mode,'Mixed transport receipt')
            require(shot.get('measurements',{}).get('timing_basis')=='after_preinfusion','Wrong timing basis')
            require(shot.get('feedback_status')=='journaled' and shot.get('delivery_id'),'Result not delivered from controller events')
            row=db.execute('SELECT record FROM shots WHERE id=?',(shot['delivery_id'],)).fetchone()
            require(row is not None,'Delivery journal missing')
            delivery=json.loads(row[0])
            require(delivery.get('state')=='verified' and delivery.get('verified_at'),'Native confirmation missing')
            require(delivery.get('source')=='de1' and delivery.get('plan',{}).get('record_only') is False,'Adjustment eligibility missing')
            require(delivery.get('outcome') in ('adjusted','held','unchanged','movement_pending','within_tolerance'),'Native outcome missing')
            require(delivery['grind_uuid']==shot['binding']['grind_uuid'],'Native correlation mismatch')
            uuids.add(delivery['grind_uuid'])
        require(len(uuids)==3,'Each cycle must use a separate grind')
    return True


def validate_daily_use(proof, database):
    """Review earlier verified cycles without representing them as full acceptance."""
    def require(condition, message):
        if not condition:raise ValueError(message)
    require(proof.get('schema_version') == 1 and proof.get('mode') == 'xenia_one_press',
            'Daily use supports only the hardware-verified one-press route')
    require(proof.get('owner_requested') is True and isinstance(proof.get('owner_request'), str)
            and bool(proof['owner_request'].strip()), 'Record the owner request for persistent operation')
    require(proof.get('validation_complete') is False and 'consecutive_real_cycles' not in proof,
            'Daily-use authorization must not claim full acceptance')
    checks = proof.get('outstanding_checks')
    require(isinstance(checks, list) and checks and all(isinstance(x, str) and x.strip() for x in checks),
            'Unresolved checks must remain explicit')
    require(proof.get('wake_from_sleep_verified') is True, 'Verified automatic wake is required')
    cycles = proof.get('cycles', [])
    require(isinstance(cycles, list) and len(cycles) >= 2, 'At least two reviewed native feedback cycles required')
    ids = [c.get('shot_id') for c in cycles]
    require(all(isinstance(x, str) and x for x in ids) and len(set(ids)) == len(ids), 'Distinct shot IDs required')
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as db:
        uuids = set()
        for observation in cycles:
            rows = db.execute('SELECT record FROM controller_shots WHERE shot_id=?', (observation['shot_id'],)).fetchall()
            require(len(rows) == 1, 'Reviewed controller shot missing or ambiguous')
            shot = json.loads(rows[0][0])
            require(all(observation.get(k) is True for k in
                        ('real_shot', 'prompt_cleared', 'next_grind_ready', 'native_adjustment_reviewed')),
                    'Cycle observations must be backed by real hardware evidence')
            require(type(observation.get('grinder_presses')) is int and observation['grinder_presses'] == 1,
                    'One arming press required')
            require(shot.get('binding', {}).get('transport') == 'xenia_one_press', 'Unverified feedback route')
            require(shot.get('measurements', {}).get('timing_basis') == 'after_preinfusion', 'Wrong timing basis')
            require(shot.get('feedback_status') == 'journaled' and shot.get('delivery_id'), 'Result not delivered')
            row = db.execute('SELECT record FROM shots WHERE id=?', (shot['delivery_id'],)).fetchone()
            require(row is not None, 'Delivery journal missing')
            delivery = json.loads(row[0])
            require(delivery.get('state') == 'verified' and delivery.get('verified_at'), 'Native confirmation missing')
            require(delivery.get('source') == 'de1' and delivery.get('plan', {}).get('record_only') is False,
                    'Adjustment eligibility missing')
            require(delivery.get('outcome') in ('adjusted', 'held', 'unchanged', 'movement_pending', 'within_tolerance'),
                    'Native adjustment handling missing')
            require(delivery['grind_uuid'] == shot['binding']['grind_uuid'], 'Native correlation mismatch')
            uuids.add(delivery['grind_uuid'])
        require(len(uuids) == len(cycles), 'Each reviewed shot needs a separate native grind')
    return True
