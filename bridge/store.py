"""Small durable journal. No grinder passwords or session tokens are stored here."""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if self.db.execute('PRAGMA user_version').fetchone()[0] > 2:
            self.db.close()
            raise ValueError('Journal schema is newer than this bridge supports')
        # New journals return freed pages without a blocking full VACUUM.
        new_journal = not self.db.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
        if new_journal:
            self.db.execute('PRAGMA auto_vacuum=INCREMENTAL')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS shots (
            id TEXT PRIMARY KEY, grind_uuid TEXT NOT NULL UNIQUE, grind_id INTEGER NOT NULL,
            source TEXT NOT NULL, external_id TEXT NOT NULL, created REAL NOT NULL,
            state TEXT NOT NULL, payload TEXT NOT NULL, record TEXT NOT NULL,
            UNIQUE(source,external_id)
          );
          CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL
          );
          CREATE INDEX IF NOT EXISTS events_created ON events(created);
          CREATE TABLE IF NOT EXISTS controller_events (
            controller TEXT NOT NULL, event_id TEXT NOT NULL, session_id TEXT NOT NULL,
            sequence INTEGER NOT NULL, shot_id TEXT, received REAL NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(controller,event_id), UNIQUE(controller,session_id,sequence)
          );
          CREATE TABLE IF NOT EXISTS controller_receipt_expiry (
            controller TEXT NOT NULL, session_id TEXT NOT NULL, ranges TEXT NOT NULL,
            PRIMARY KEY(controller,session_id)
          );
          CREATE INDEX IF NOT EXISTS controller_routine_received ON controller_events(received)
            WHERE shot_id IS NULL;
          CREATE TABLE IF NOT EXISTS controller_shots (
            controller TEXT NOT NULL, shot_id TEXT NOT NULL, record TEXT NOT NULL,
            PRIMARY KEY(controller,shot_id)
          );
          CREATE INDEX IF NOT EXISTS controller_telemetry_age ON controller_events(received)
            WHERE shot_id IS NULL AND substr(payload,1,7) != 'sha256:';
          CREATE TABLE IF NOT EXISTS profiles (
            controller TEXT NOT NULL, profile_id TEXT NOT NULL, record TEXT NOT NULL,
            PRIMARY KEY(controller,profile_id)
          );
          CREATE TABLE IF NOT EXISTS controller_archives (
            controller TEXT NOT NULL, filename TEXT NOT NULL, digest TEXT NOT NULL,
            received REAL NOT NULL, raw TEXT NOT NULL,
            PRIMARY KEY(controller,filename,digest)
          );
          CREATE TABLE IF NOT EXISTS cycles (
            cycle_id TEXT PRIMARY KEY, grind_uuid TEXT UNIQUE,
            updated REAL NOT NULL, record TEXT NOT NULL
          );
          PRAGMA user_version=2;
        ''')
        if new_journal:
            self.db.execute("INSERT INTO kv VALUES ('receipt_expiry_api','1')")
        self.db.commit()
        self.last_prune = 0

    @staticmethod
    def payload_matches(saved, encoded):
        if saved.startswith('sha256:'):
            return saved == 'sha256:' + hashlib.sha256(encoded.encode()).hexdigest()
        return saved == encoded

    def receipt_expired(self, controller, event):
        # Check both the claimed sequence and the event ID's original namespace.
        # This prevents a deleted ID being reused with a new session/sequence.
        slots = [(event['session_id'], event['sequence'])]
        session, separator, sequence = event['event_id'].rpartition('-')
        if separator and sequence.isascii() and sequence.isdecimal() and len(sequence) <= 19 and str(int(sequence)) == sequence:
            slots.append((session, int(sequence)))
        for session, sequence in slots:
            row = self.db.execute('SELECT ranges FROM controller_receipt_expiry WHERE controller=? AND session_id=?',
                                  (controller, session)).fetchone()
            if row and any(start <= sequence <= end for start, end in json.loads(row[0])):
                return True
        return False

    @contextmanager
    def atomic(self):
        """Nested journal writes must never commit an enclosing event transaction."""
        nested = self.db.in_transaction
        name = 'nested_' + str(time.monotonic_ns())
        self.db.execute('SAVEPOINT ' + name if nested else 'BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('RELEASE ' + name) if nested else self.db.commit()
        except BaseException:
            if nested:
                self.db.execute('ROLLBACK TO ' + name)
                self.db.execute('RELEASE ' + name)
            else:
                self.db.rollback()
            raise

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.atomic():
            self.db.execute('INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                            (key, json.dumps(value, allow_nan=False)))

    def event(self, kind, **data):
        now = time.time()
        with self.atomic():
            if now - self.last_prune > 86400:
                self.db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events WHERE created < ? LIMIT 500)', (now - 7*86400,))
                self.last_prune = now
            self.db.execute('INSERT INTO events(created,kind,data) VALUES (?,?,?)',
                            (time.time(), kind, json.dumps(data, allow_nan=False)))

    def shot(self, shot_id):
        row = self.db.execute('SELECT record FROM shots WHERE id=?', (shot_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def by_external(self, source, external_id):
        row = self.db.execute('SELECT record FROM shots WHERE source=? AND external_id=?',
                             (source, external_id)).fetchone()
        return json.loads(row[0]) if row else None

    def by_grind(self, grind_uuid):
        row = self.db.execute('SELECT record FROM shots WHERE grind_uuid=?', (grind_uuid,)).fetchone()
        return json.loads(row[0]) if row else None

    def add_shot(self, record):
        with self.atomic():
            self.db.execute('INSERT INTO shots VALUES (?,?,?,?,?,?,?,?,?)',
                (record['id'], record['grind_uuid'], record['grind_id'], record['source'], record['external_id'],
                 record['created'], record['state'], json.dumps(record['input']), json.dumps(record)))
        return record

    def update_shot(self, shot_id, **changes):
        record = self.shot(shot_id)
        if record is None:
            raise KeyError(shot_id)
        record.update(changes)
        with self.atomic():
            self.db.execute('UPDATE shots SET state=?,record=? WHERE id=?',
                            (record['state'], json.dumps(record, allow_nan=False), shot_id))
        return record

    def recent(self, limit=30):
        return [json.loads(r[0]) for r in self.db.execute('SELECT record FROM shots ORDER BY created DESC LIMIT ?', (limit,))]

    def close(self):
        self.db.close()

    def save_cycle(self, record):
        with self.atomic():
            self.db.execute('INSERT INTO cycles VALUES (?,?,?,?) ON CONFLICT(cycle_id) '
                'DO UPDATE SET grind_uuid=excluded.grind_uuid, updated=excluded.updated, record=excluded.record',
                (record['cycle_id'], record.get('grind_uuid'), time.time(), json.dumps(record, allow_nan=False)))

    def cycle(self, cycle_id):
        row = self.db.execute('SELECT record FROM cycles WHERE cycle_id=?', (cycle_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def cycle_for_grind(self, grind_uuid):
        row = self.db.execute('SELECT record FROM cycles WHERE grind_uuid=?', (grind_uuid,)).fetchone()
        return json.loads(row[0]) if row else None

    def controller_shot(self, controller, shot_id):
        row = self.db.execute('SELECT record FROM controller_shots WHERE controller=? AND shot_id=?',
                              (controller, shot_id)).fetchone()
        return json.loads(row[0]) if row else None

    def save_controller_shot(self, record):
        with self.atomic():
            self.db.execute('INSERT INTO controller_shots VALUES (?,?,?) ON CONFLICT(controller,shot_id) '
                            'DO UPDATE SET record=excluded.record',
                            (record['controller_id'], record['shot_id'], json.dumps(record, allow_nan=False)))

    def controller_history(self, limit=100, *, controller_id=None):
        where, params = (' WHERE controller=?', (controller_id,)) if controller_id else ('', ())
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT record FROM controller_shots'+where+' ORDER BY rowid DESC LIMIT ?', (*params,limit))]

    def select_controller(self, controller_id, capture_identity=None):
        """Changing the capture owner preserves history and requires commissioning."""
        with self.atomic():
            previous = self.get('active_controller')
            changed = (previous != controller_id or self.get('controller_capture_identity') != capture_identity)
            if previous is not None and changed:
                for key in ('controller_profile','controller_stream','controller_confirmed_targets',
                            'controller_wake','controller_history_reconciliation','controller_contract'):
                    self.db.execute('DELETE FROM kv WHERE key=?',(key,))
                for row in self.db.execute('SELECT profile_id,record FROM profiles WHERE controller=?',(controller_id,)).fetchall():
                    profile = json.loads(row['record'])
                    profile['commissioned_revision'] = profile['execution_signature'] = None
                    self.save_profile(controller_id,row['profile_id'],profile)
            self.set('active_controller',controller_id)
            self.set('controller_capture_identity',capture_identity)

    def profile(self, controller, profile_id):
        row = self.db.execute('SELECT record FROM profiles WHERE controller=? AND profile_id=?',
                             (controller, profile_id)).fetchone()
        return json.loads(row[0]) if row else None

    def save_profile(self, controller, profile_id, record):
        with self.atomic():
            self.db.execute('INSERT INTO profiles VALUES (?,?,?) ON CONFLICT(controller,profile_id) '
                            'DO UPDATE SET record=excluded.record',
                            (controller, profile_id, json.dumps(record, allow_nan=False)))

    def profiles(self):
        return [{'controller_id':r[0], 'profile_id':r[1], **json.loads(r[2])}
                for r in self.db.execute('SELECT controller,profile_id,record FROM profiles')]


def expire_receipts(db, now, limit):
    # Bound structured heartbeat receipts, retaining exact membership as
    # sequence ranges. Opaque legacy event IDs keep their digest receipts.
    recent = db.execute("""SELECT received,length(payload) FROM controller_events
        WHERE shot_id IS NULL ORDER BY received DESC LIMIT 1001""").fetchall()
    cutoff = now-86400
    used = 0
    for index, (received, size) in enumerate(recent):
        used += size
        if index >= 1000 or used > 512*1024:
            cutoff = max(cutoff, received+0.000001)
            break
    expiry_enabled = db.execute("SELECT value FROM kv WHERE key='receipt_expiry_api'").fetchone()
    expired = db.execute("""SELECT rowid,controller,event_id,session_id,sequence FROM controller_events
        WHERE shot_id IS NULL AND received < ?
        AND event_id=session_id||'-'||CAST(sequence AS TEXT)
        AND (substr(payload,1,7)='sha256:' OR json_extract(payload,'$.kind') IN ('profile','status'))
        ORDER BY received LIMIT ?""", (cutoff, limit)).fetchall() if expiry_enabled and json.loads(expiry_enabled[0]) == 1 else []
    if expired:
        with db:
            groups = {}
            for _, controller, _, session, sequence in expired:
                groups.setdefault((controller,session), []).append([sequence,sequence])
            for (controller,session), additions in groups.items():
                row = db.execute('SELECT ranges FROM controller_receipt_expiry WHERE controller=? AND session_id=?',
                                 (controller,session)).fetchone()
                merged = []
                for start,end in sorted((json.loads(row[0]) if row else []) + additions):
                    if merged and start <= merged[-1][1]+1:
                        merged[-1][1] = max(merged[-1][1],end)
                    else:
                        merged.append([start,end])
                db.execute('INSERT INTO controller_receipt_expiry VALUES (?,?,?) ON CONFLICT(controller,session_id) DO UPDATE SET ranges=excluded.ranges',
                           (controller,session,json.dumps(merged,separators=(',',':'))))
            db.executemany('DELETE FROM controller_events WHERE rowid=?', [(row[0],) for row in expired])
            stats = json.loads(db.execute("SELECT value FROM kv WHERE key='telemetry_retention'").fetchone()[0]) if db.execute("SELECT 1 FROM kv WHERE key='telemetry_retention'").fetchone() else {}
            stats.update(expired_receipts=stats.get('expired_receipts',0)+len(expired),
                         receipt_retention_s=86400, receipt_limit=1000, receipt_payload_budget=512*1024,
                         last_expiry_batch_size=len(expired), checked_at=now)
            db.execute("INSERT INTO kv VALUES ('telemetry_retention',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(stats),))
    else:
        with db:
            row = db.execute("SELECT value FROM kv WHERE key='telemetry_retention'").fetchone()
            if row:
                stats=json.loads(row[0]);stats['last_expiry_batch_size']=0
                db.execute("UPDATE kv SET value=? WHERE key='telemetry_retention'", (json.dumps(stats),))
    return len(expired)


def prune_diagnostics(db, now):
    # Operational diagnostics are not shot history.
    removed=0
    with db:
        removed+=db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events WHERE created < ? LIMIT 500)', (now-7*86400,)).rowcount
        removed+=db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id DESC LIMIT 500 OFFSET 1000)').rowcount
        size = 0
        for event_id, length in db.execute('SELECT id,length(data) FROM events ORDER BY id DESC LIMIT 1001').fetchall():
            size += length
            if size > 2*1024*1024:
                removed+=db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events WHERE id <= ? LIMIT 500)', (event_id,)).rowcount
                break
    return removed


def retain_telemetry(path, *, now=None, limit=500):
    """Bound routine payloads and expire structured telemetry with durable replay guards.

    A separate connection runs short batches off the request loop. Lifecycle
    payloads, commissioned profiles, shot records and deliveries are untouched.
    """
    now = time.time() if now is None else now
    with sqlite3.connect(path, timeout=0.05) as db:
        db.execute('PRAGMA synchronous=FULL')
        deadline = time.monotonic() + 0.1
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            rows = db.execute('''SELECT rowid,payload FROM controller_events
                WHERE shot_id IS NULL AND substr(payload,1,7) != 'sha256:'
                AND received < ? AND json_extract(payload,'$.kind') IN ('profile','status')
                ORDER BY received LIMIT ?''', (now-3600, limit)).fetchall()
            if rows:
                with db:
                    db.executemany('UPDATE controller_events SET payload=? WHERE rowid=?',
                        [('sha256:'+hashlib.sha256(payload.encode()).hexdigest(), rowid)
                         for rowid,payload in rows])
                    row = db.execute("SELECT value FROM kv WHERE key='telemetry_retention'").fetchone()
                    stats = json.loads(row[0]) if row else {'compacted_receipts':0,'released_payload_bytes':0}
                    stats['compacted_receipts'] = stats.get('compacted_receipts',0)+len(rows)
                    stats['released_payload_bytes'] = stats.get('released_payload_bytes',0)+sum(len(payload.encode())-71 for _,payload in rows)
                    stats.update(checked_at=now, full_payload_retention_s=3600)
                    stats['last_batch_size'] = len(rows)
                    db.execute("INSERT INTO kv VALUES ('telemetry_retention',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (json.dumps(stats),))
            else:
                row = db.execute("SELECT value FROM kv WHERE key='telemetry_retention'").fetchone()
                stats = json.loads(row[0]) if row else None
                if stats and stats.get('last_batch_size'):
                    stats.update(last_batch_size=0, checked_at=now)
                    with db:
                        db.execute("UPDATE kv SET value=? WHERE key='telemetry_retention'",(json.dumps(stats),))
            expired = expire_receipts(db, now, limit)
            diagnostics = prune_diagnostics(db, now)
            if db.execute('PRAGMA auto_vacuum').fetchone()[0] == 2:
                db.execute('PRAGMA incremental_vacuum(64)').fetchall()
            return len(rows)+expired+diagnostics
        except sqlite3.OperationalError as exc:
            db.rollback()
            if 'locked' in str(exc) or 'interrupted' in str(exc):
                return -1
            raise


def compact_expired(database):
    """Explicit offline migration, coordinated by the installation authority."""
    store = Store(database)
    try:
        store.set('receipt_expiry_api', 1)
        deadline = time.monotonic()+600
        while True:
            count = retain_telemetry(database)
            if count == 0:break
            if time.monotonic()>deadline:raise TimeoutError('Receipt expiry migration timed out')
        store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
        store.db.execute('VACUUM')
        store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
        if [r[0] for r in store.db.execute('PRAGMA quick_check')] != ['ok']:
            raise ValueError('Compacted journal failed verification')
        return {'database_bytes':Path(database).stat().st_size, 'receipt_expiry_api':1}
    finally:
        store.close()


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Coordinated offline journal maintenance')
    parser.add_argument('--compact-expired', type=Path, required=True)
    print(json.dumps(compact_expired(parser.parse_args().compact_expired)))
