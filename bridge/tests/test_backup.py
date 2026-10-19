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
    assert len(list(folder.glob('coffee-*.sqlite3'))) == 14
    assert not (folder/'coffee-2026-01-01.sqlite3').exists()
    assert snapshot(path,day='2026-01-16').stat().st_mode & 0o777 == 0o600
    store.close()
