"""Online journal snapshots; restore only into a new, empty installation path."""
import argparse
import asyncio
from datetime import date
import logging
import os
from pathlib import Path
import sqlite3
import time
import tempfile


def check(database):
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':raise ValueError('Journal integrity check failed')
        if db.execute('PRAGMA user_version').fetchone()[0] != 2:raise ValueError('Unsupported backup schema')
        if not db.execute("SELECT value FROM kv WHERE key='installation_id'").fetchone():
            raise ValueError('Backup has no installation identity')


def snapshot(database, *, day=None):
    database = Path(database)
    folder = database.parent/'backups'
    folder.mkdir(mode=0o700,exist_ok=True)
    if folder.is_symlink():raise ValueError('Backup folder must not be a symlink')
    output = folder/f'coffee-{day or date.today().isoformat()}.sqlite3'
    fd, name = tempfile.mkstemp(prefix='.snapshot-',suffix='.partial',dir=folder)
    temporary = Path(name)
    os.close(fd)
    try:
        with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as source, sqlite3.connect(temporary) as dest:
            deadline = time.monotonic()+30
            def progress(*args):
                if time.monotonic()>deadline:raise TimeoutError('Journal backup deadline exceeded')
            source.backup(dest,pages=256,progress=progress)
            if dest.execute('PRAGMA quick_check').fetchone()[0] != 'ok':raise ValueError('Backup integrity check failed')
        with temporary.open('rb') as stream:os.fsync(stream.fileno())
        os.replace(temporary,output)
        fd = os.open(folder,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        for old in sorted(folder.glob('coffee-*.sqlite3'),reverse=True)[14:]:old.unlink()
        for old in folder.glob('.snapshot-*.partial'):
            if old.is_file() and not old.is_symlink() and time.time()-old.stat().st_mtime > 86400:old.unlink()
        return output
    finally:
        if temporary.exists():temporary.unlink()


def restore(source, destination):
    """Never replace a live journal as part of a code rollback."""
    source,destination = Path(source),Path(destination)
    check(source)
    if destination.exists() or any(Path(str(destination)+suffix).exists() for suffix in ('-wal','-shm')):
        raise ValueError('Restore requires a new destination; keep the existing journal intact')
    destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd = os.open(destination,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
        check(destination)
        with destination.open('rb') as stream:os.fsync(stream.fileno())
        fd = os.open(destination.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


async def daily(database):
    # A failure is visible in logs and retried; it cannot stall native polling.
    while True:
        output = Path(database).parent/'backups'/f'coffee-{date.today().isoformat()}.sqlite3'
        if not output.exists():
            try:await asyncio.to_thread(snapshot,database)
            except Exception:logging.error('Daily journal backup failed; retrying in one hour')
        await asyncio.sleep(3600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path,help='Journal, or snapshot when --restore-to is used')
    parser.add_argument('--restore-to',type=Path)
    args = parser.parse_args()
    if args.restore_to:restore(args.database,args.restore_to)
    else:snapshot(args.database)


if __name__ == '__main__':main()
