import asyncio
import json
import sqlite3
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest
from app import create_app
from adapters import ClassicRest
from engine import Engine
from store import Store
from support import FakeReader
from support import event


def test_controller_http_hostname_and_durable_ack(tmp_path):
    async def run():
        e=Engine(Store(tmp_path/'http.db'),FakeReader())
        app=create_app({'origins':['http://sync.local','http://127.0.0.1'],
            'allowed_networks':['127.0.0.0/8'],'grinder_host':'127.0.0.1','automation':{}},e)
        e.controllers.adapter=None
        server=TestServer(app);await server.start_server()
        async with ClientSession(headers={'Host':'sync.local'}) as client:
            base=str(server.make_url('')).rstrip('/')
            payload=event(0,'profile')
            path=base+'/api/v1/controllers/classic-de1/events'
            for duplicate in (False,True):
                async with client.post(path,json=payload) as res:
                    data=await res.json()
                    assert res.status==202 and data['durable'] and data['duplicate']==duplicate
            row=e.store.db.execute('SELECT payload FROM controller_events').fetchone()
            assert json.loads(row[0])==payload
            async with client.post(path,json={**payload,'sequence':9}) as res:assert res.status==409
            async with client.post(base+'/api/v1/controllers/unknown/events',json=payload) as res:assert res.status==404
            async with client.get(base+'/api/v1/controllers/classic-de1/status') as res:
                status=await res.json();assert status['profile']['target']['value']==45
            async with client.get(base+'/api/v1/status',headers={'Host':'127.0.0.1'}) as res:assert res.status==200
        await server.close()
    asyncio.run(run())


def test_classic_wake_accepts_plain_text_then_verifies_status():
    async def run():
        app=web.Application();calls=[]
        async def status(req):return web.json_response({'state':'Idle','substate':'ready'})
        async def wake(req):calls.append(await req.json());return web.Response(text='ok')
        app.router.add_post('/api/status',wake);app.router.add_get('/api/status/details',status)
        server=TestServer(app);await server.start_server()
        adapter=ClassicRest(str(server.make_url('')).rstrip('/'))
        await adapter.wake();assert (await adapter.status())['state']=='Idle'
        assert calls==[{'active':'true'}]
        await server.close()
    asyncio.run(run())


def test_additive_migration_keeps_old_history_and_rejects_future_schema(tmp_path):
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE sessions (id TEXT); INSERT INTO sessions VALUES ("keep");'
                        'CREATE TABLE kv (key TEXT PRIMARY KEY,value TEXT NOT NULL);'
                        'INSERT INTO kv VALUES ("legacy","42");')
    s=Store(path)
    assert s.get('legacy')==42
    assert s.db.execute('SELECT id FROM sessions').fetchone()[0]=='keep'
    assert s.db.execute('PRAGMA user_version').fetchone()[0]==2
    s.close()
    with sqlite3.connect(path) as db:db.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError):Store(path)


def test_historical_reconciliation_is_idempotent_opaque_and_never_feedback(tmp_path):
    class Archive:
        async def history(self):return [{'filename':'20260927_100000.shot','raw':'[exec must-never-run] $secret'}]
    async def run():
        e=Engine(Store(tmp_path/'archive.db'),FakeReader())
        from controllers import Controllers
        c=e.controllers=Controllers(e,adapter=Archive())
        await c.reconcile_history();await c.reconcile_history()
        assert c.history_reconciliation['new_versions']==0
        assert e.store.db.execute('SELECT count(*) FROM controller_archives').fetchone()[0]==1
        assert not e.store.recent() and not e.store.controller_history()
        e.store.close()
    asyncio.run(run())
