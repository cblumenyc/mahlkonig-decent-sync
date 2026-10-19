"""Read-only, credential-free export of recent feedback correlation evidence."""
import json
import argparse
import sqlite3
import time
from pathlib import Path


def collect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        shots = [json.loads(r[0]) for r in db.execute(
            'SELECT record FROM controller_shots ORDER BY rowid DESC LIMIT 10')]
        receipts = []
        for shot in shots:
            receipts.extend(dict(r) for r in db.execute(
                'SELECT event_id, sequence, received, payload FROM controller_events '
                'WHERE controller=? AND shot_id=? ORDER BY sequence',
                (shot['controller_id'], shot['shot_id'])))
        events = [dict(r) for r in db.execute(
            'SELECT created,kind,data FROM events WHERE created>? ORDER BY id DESC LIMIT 500',
            (time.time() - 3*86400,))]
        return {'captured_at': time.time(), 'shots': shots, 'receipts': receipts,
                'events': events, 'integrity': db.execute('PRAGMA quick_check').fetchone()[0]}
    finally:
        db.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path)
    print(json.dumps(collect(parser.parse_args().database), indent=2))
