"""Online recovery preserves installation and uncertain-delivery evidence."""

import pytest

from backup import snapshot,restore
from identity import installation_id
from store import Store


def test_backup_restore_preserves_identity_payload_and_uncertain_state(tmp_path):
    path = tmp_path/'live'/'bridge.sqlite3'
    store = Store(path)
    identity = installation_id(store)
    store.set('active',{'exposed':True,'cycle_id':'saved-cycle','payload':'exact frozen payload'})
    store.save_controller_shot({'controller_id':'classic-de1','shot_id':'retained','feedback_status':'awaiting_native'})
    first = snapshot(path,day='2026-01-01')
    store.set('after_snapshot','newer event')
    restored = tmp_path/'restored'/'bridge.sqlite3'
    restore(first,restored)
    copy = Store(restored)
    assert installation_id(copy) == identity
    assert copy.get('active') == store.get('active')
    assert copy.get('after_snapshot') is None
    assert copy.controller_shot('classic-de1','retained')['feedback_status'] == 'awaiting_native'
    with pytest.raises(ValueError,match='new destination'):restore(first,path)
    assert store.get('after_snapshot') == 'newer event'
    copy.close();store.close()


def test_retention_and_interrupted_snapshot_do_not_block_recovery(tmp_path):
    path = tmp_path/'bridge.sqlite3'
    store = Store(path);installation_id(store)
    folder = path.parent/'backups';folder.mkdir()
    (folder/'.snapshot-interrupted.partial').write_bytes(b'incomplete')
    for day in range(1,17):snapshot(path,day=f'2026-01-{day:02d}')
    assert len(list(folder.glob('coffee-*.sqlite3'))) == 7
    assert not (folder/'coffee-2026-01-01.sqlite3').exists()
    assert snapshot(path,day='2026-01-16').stat().st_mode & 0o777 == 0o600
    store.close()


def test_telemetry_retention_is_batched_respects_age_and_survives_backup(tmp_path):
    import json
    from store import retain_telemetry
    path = tmp_path/'bridge.sqlite3';store = Store(path);identity=installation_id(store)
    for sequence in range(12):
        payload=json.dumps({'kind':'status','data':{'state':'Idle'},'padding':'x'*2000})
        store.db.execute('INSERT INTO controller_events VALUES (?,?,?,?,?,?,?)',
            ('classic-de1',f'e{sequence}','session',sequence,None,0 if sequence<10 else 7000,payload))
    store.db.commit()
    assert retain_telemetry(path,now=7200,limit=3)==3
    assert store.get('telemetry_retention')['compacted_receipts']==3
    for _ in range(3):retain_telemetry(path,now=7200,limit=3)
    assert retain_telemetry(path,now=7200)==0
    assert store.get('telemetry_retention')['compacted_receipts']==10
    assert store.db.execute("SELECT count(*) FROM controller_events WHERE payload LIKE 'sha256:%'").fetchone()[0]==10
    first=snapshot(path,day='2026-01-01');copy=tmp_path/'copy.sqlite3';restore(first,copy)
    restored=Store(copy)
    assert installation_id(restored)==identity
    assert restored.db.execute('SELECT payload FROM controller_events WHERE event_id=?',('e0',)).fetchone()[0].startswith('sha256:')
    assert restored.db.execute('PRAGMA auto_vacuum').fetchone()[0]==2
    restored.close();store.close()


def test_telemetry_retention_yields_to_an_active_writer(tmp_path):
    import sqlite3
    from store import retain_telemetry
    path=tmp_path/'db';store=Store(path)
    store.db.execute('INSERT INTO controller_events VALUES (?,?,?,?,?,?,?)',
        ('classic-de1','event','session',1,None,0,'{"kind":"status"}'))
    store.db.commit()
    with sqlite3.connect(path) as writer:
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("INSERT INTO kv VALUES ('test','true')")
        # A batch never stalls the owning request connection waiting for a writer.
        assert retain_telemetry(path)==-1
    assert store.get('test') is True
    assert retain_telemetry(path)==1
    store.close()


def test_daily_backup_budget_prunes_only_after_verified_replacement_and_keeps_two(tmp_path,monkeypatch):
    import backup
    path=tmp_path/'bridge.sqlite3';store=Store(path);identity=installation_id(store)
    monkeypatch.setattr(backup,'BACKUP_LIMIT_BYTES',1)
    for day in range(1,5):snapshot(path,day=f'2026-01-{day:02d}')
    files=sorted((tmp_path/'backups').glob('coffee-*.sqlite3'))
    assert [p.name for p in files]==['coffee-2026-01-03.sqlite3','coffee-2026-01-04.sqlite3']
    for p in files:
        backup.check(p)
        with backup.sqlite3.connect(p) as db:
            assert db.execute("SELECT value FROM kv WHERE key='installation_id'").fetchone()[0]=='"'+identity+'"'
    def fail_replace(*args):raise OSError('simulated replacement failure')
    monkeypatch.setattr(backup.os,'replace',fail_replace)
    with pytest.raises(OSError,match='replacement failure'):snapshot(path,day='2026-01-05')
    assert sorted((tmp_path/'backups').glob('coffee-*.sqlite3'))==files
    assert not list((tmp_path/'backups').glob('.snapshot-*'))
    store.close()


def test_compact_expired_receipts_preserves_all_coffee_records_and_restores_guards(tmp_path):
    import json,time
    from store import compact_expired
    path=tmp_path/'live.sqlite3';store=Store(path);identity=installation_id(store)
    store.set('receipt_expiry_api',0)
    for seq in range(1,10001):
        store.db.execute('INSERT INTO controller_events VALUES (?,?,?,?,?,?,?)',
            ('classic-de1',f'session-{seq}','session',seq,None,0,'sha256:'+'a'*64))
    store.db.execute('INSERT INTO controller_events VALUES (?,?,?,?,?,?,?)',
        ('classic-de1','opaque-event','opaque-session',7,None,0,'{"kind":"status"}'))
    store.db.commit()
    store.save_controller_shot({'controller_id':'classic-de1','shot_id':'retained','events':{'shot_finalized':{'mass':36}},'feedback_status':'awaiting_native'})
    saved=store.controller_shot('classic-de1','retained');store.close()
    result=compact_expired(path)
    live=Store(path)
    assert result['database_bytes']<1024*1024
    assert live.controller_shot('classic-de1','retained')==saved
    assert live.db.execute('SELECT count(*) FROM controller_events').fetchone()[0]==1
    assert live.db.execute('SELECT ranges FROM controller_receipt_expiry').fetchone()[0]=='[[1,10000]]'
    fresh=snapshot(path);copy=tmp_path/'restored.sqlite3';restore(fresh,copy)
    recovered=Store(copy)
    assert installation_id(recovered)==identity
    assert recovered.receipt_expired('classic-de1',{'event_id':'session-99','session_id':'session','sequence':99})
    recovered.close();live.close()


def test_telemetry_count_and_payload_budgets_expire_recent_receipts_atomically(tmp_path):
    import json
    from store import retain_telemetry
    path=tmp_path/'db';store=Store(path)
    for seq in range(1,12):
        store.db.execute('INSERT INTO controller_events VALUES (?,?,?,?,?,?,?)',
            ('classic-de1',f'session-{seq}','session',seq,None,7100+seq,json.dumps({'kind':'status','padding':'x'*100000})))
    store.db.commit()
    while retain_telemetry(path,now=7200):pass
    count,size=store.db.execute('SELECT count(*),sum(length(payload)) FROM controller_events').fetchone()
    assert count<=5 and size<=512*1024
    assert store.receipt_expired('classic-de1',{'event_id':'session-1','session_id':'session','sequence':1})
    # A normal retry slot still retained is not falsely declared expired.
    assert not store.receipt_expired('classic-de1',{'event_id':'session-11','session_id':'session','sequence':11})
    store.close()
