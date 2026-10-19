"""Small durable journal. No grinder passwords or session tokens are stored here."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if self.db.execute('PRAGMA user_version').fetchone()[0] > 2:
            self.db.close()
            raise ValueError('Journal schema is newer than this bridge supports')
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
          CREATE TABLE IF NOT EXISTS controller_shots (
            controller TEXT NOT NULL, shot_id TEXT NOT NULL, record TEXT NOT NULL,
            PRIMARY KEY(controller,shot_id)
          );
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
        self.db.commit()
        self.last_prune = 0

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
                self.db.execute('DELETE FROM events WHERE created < ?', (now - 90*86400,))
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
