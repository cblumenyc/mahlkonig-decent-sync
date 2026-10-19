"""HTTP and WebSocket share durable event identity and validation."""
import asyncio

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from app import create_app
from engine import Engine
from store import Store
from support import FakeReader, event


def test_websocket_http_retry_identity_and_origin_guards(tmp_path):
    async def run():
        engine = Engine(Store(tmp_path/'db'), FakeReader())
        config = {'origins':['http://127.0.0.1'], 'allowed_networks':['127.0.0.0/8'],
            'grinder_host':'127.0.0.1', 'automation':{'feedback_mode':'observe'}}
        app = create_app(config, engine)
        async with TestServer(app) as server, ClientSession() as client:
            headers = {'Host':'127.0.0.1'}
            url = str(server.make_url('/api/v1/controllers/classic-de1'))
            async with client.get(server.make_url('/api/v1/identity'), headers=headers) as response:
                identity = await response.json()
            expected = identity['server_id']
            assert identity['starts_extraction'] is False
            async with client.ws_connect(url+'/ws', headers=headers) as socket:
                assert (await socket.receive_json())['server_id'] == expected
                row = event(1,'profile');row['schema_version'] = 2
                await socket.send_json({'type':'event','server_id':'unrelated','event':row})
                assert (await socket.receive_json())['type'] == 'error'
                assert engine.store.db.execute('SELECT count(*) FROM controller_events').fetchone()[0] == 0
                await socket.send_json({'type':'event','server_id':expected,'event':row})
                ack = await socket.receive_json()
                assert ack['type'] == 'ack' and ack['durable'] and not ack['duplicate']
                async with client.post(url+'/events', headers={**headers,'X-Sync-Server-ID':expected}, json=row) as response:
                    assert response.status == 202
                    assert (await response.json())['duplicate']
                async with client.post(url+'/events', headers={**headers,'X-Sync-Server-ID':'wrong'}, json=row) as response:
                    assert response.status == 409
                row['data']['changed'] = True
                await socket.send_json({'type':'event','server_id':expected,'event':row})
                assert (await socket.receive_json())['type'] == 'error'
                assert engine.store.db.execute('SELECT count(*) FROM controller_events').fetchone()[0] == 1
            async with client.get(url+'/ws',headers={**headers,'Origin':'http://unrelated.example'}) as response:
                assert response.status == 403
        store = Store(tmp_path/'db')
        assert store.get('installation_id') == expected
        assert store.db.execute('SELECT count(*) FROM controller_events').fetchone()[0] == 1
        store.close()
    asyncio.run(run())
