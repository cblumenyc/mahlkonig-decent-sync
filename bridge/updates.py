"""Expose the complete Classic package bundled with the verified server release."""
import hashlib
import json
from pathlib import Path

from aiohttp import web

from identity import installation_id

ROOT = Path(__file__).parent/'updates'


def classic_catalogue(engine):
    path = ROOT/'classic.json'
    if not path.is_file():raise web.HTTPNotFound(text='Install a packaged server release to enable updates')
    manifest = json.loads(path.read_text())
    return {**manifest,'server_id':installation_id(engine.store)}


def classic_file(engine, name):
    manifest = classic_catalogue(engine)
    if name not in manifest['files'] or Path(name).name != name:
        raise web.HTTPNotFound(text='Unknown package file')
    path = ROOT/'classic'/name
    if path.is_symlink() or not path.is_file():raise web.HTTPConflict(text='Package file unavailable')
    body = path.read_bytes()
    spec = manifest['files'][name]
    if len(body) != spec['bytes'] or hashlib.sha256(body).hexdigest() != spec['sha256']:
        raise web.HTTPConflict(text='Package file verification failed')
    return web.Response(body=body,content_type='application/octet-stream')
