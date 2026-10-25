"""Mahlkönig - Decent sync: local dashboard, controller API and cached Xenia peer."""
import argparse
import asyncio
import ipaddress
import json
import logging
from pathlib import Path
import time

from aiohttp import web, WSMsgType
from engine import Engine, Reader, IDENTITY
from grinder_client import BridgeError
from store import Store
from controllers import Controllers, TelemetryExpired
from adapters import controller_adapter
from identity import capabilities, installation_id, verify_server

STATIC = Path(__file__).parent / 'static'
def create_app(config, engine):
    origins = set(config['origins'])
    hosts = {origin.split('://', 1)[1] for origin in origins}
    grinder_addresses = set(config.get('grinder_addresses', []))
    try:
        grinder_addresses.add(str(ipaddress.ip_address(config['grinder_host'])))
    except ValueError:
        pass
    if config.get('automation') is not None:
        auto = config['automation']
        adapter = controller_adapter(auto)
        engine.controllers = Controllers(engine, auto, adapter)
    networks = [ipaddress.ip_network(v) for v in config['allowed_networks']]
    server_id = installation_id(engine.store)

    @web.middleware
    async def guard(request, handler):
        try:
            peer = ipaddress.ip_address(request.remote or '0.0.0.0')
            if not (peer.is_loopback and request.path == '/healthz') and not any(peer in network for network in networks):
                raise web.HTTPForbidden(text='Local network access only')
            if request.host not in hosts:
                raise web.HTTPForbidden(text='Unrecognized host')
            if request.path.startswith('/api/v2/'):
                try:
                    permitted_grinders = {str(ipaddress.ip_address(config['grinder_host']))}
                except ValueError:
                    permitted_grinders = set(config.get('_grinder_addresses',grinder_addresses))
                if request.remote not in permitted_grinders:
                    raise web.HTTPForbidden(text='Grinder access only')
            else:
                origin = request.headers.get('Origin')
                if origin and origin not in origins:
                    raise web.HTTPForbidden(text='Unrecognized origin')
                if request.method not in ('GET', 'HEAD'):
                    # Browser cross-site forms cannot use this JSON-only API. Native LAN
                    # controllers need no dashboard credentials.
                    if request.content_type != 'application/json':
                        raise web.HTTPUnsupportedMediaType(text='Use application/json')
                    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                        raise web.HTTPForbidden(text='Cross-site requests are not accepted')
            response = await handler(request)
        except TelemetryExpired as exc:
            response = web.json_response({'error':str(exc), 'server_id':server_id,
                'event_id':exc.event_id, 'durable':False, 'expired_telemetry':True}, status=410)
        except BridgeError as exc:
            response = web.json_response({'error': str(exc)}, status=409)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            response = web.json_response({'error': 'Invalid request data'}, status=400)
        except web.HTTPException as exc:
            response = web.json_response({'error': exc.text}, status=exc.status)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        if request.cookies.get('coffee_session'):
            response.del_cookie('coffee_session')
        return response

    app = web.Application(middlewares=[guard], client_max_size=131072)
    setup_lock = asyncio.Lock()

    async def status(request):
        from control import idle_reason
        return web.json_response({**engine.status(),'update_idle_reason':idle_reason(engine)})

    async def classic_updates(request):
        from updates import classic_catalogue
        return web.json_response(classic_catalogue(engine))

    async def classic_update_file(request):
        from updates import classic_file
        return classic_file(engine,request.match_info['name'])

    async def shot(request):
        data = await request.json()
        if not isinstance(data, dict):
            raise BridgeError('Shot must be an object')
        return web.json_response(await engine.submit(data), status=202)

    async def get_shot(request):
        from engine import public_shot
        record = engine.store.shot(request.match_info['id'])
        if not record:
            raise web.HTTPNotFound(text='Unknown shot')
        return web.json_response(public_shot(record))

    async def cancel(request):
        engine.cancel()
        return web.json_response({'ok': True})

    async def refresh(request):
        engine.refresh_requested = True
        return web.json_response({'ok': True})

    async def identity(request):
        engine.last_identity = time.time()
        return web.json_response(IDENTITY)

    async def mako(request):
        return web.json_response(engine.mako())

    async def ratio(request):
        engine.brewratio(await request.json())
        return web.json_response({'success': True})

    async def execute(request):
        engine.execute(await request.json())
        return web.json_response({'success': True})

    async def index(request):
        return web.FileResponse(STATIC / 'index.html')

    async def logo(request):
        path = STATIC/'mahlkonig.svg'
        if not path.exists():path = STATIC.parent.parent/'assets/mahlkonig.svg'
        return web.FileResponse(path)

    async def notices(request):
        root = STATIC.parent
        if not (root/'ASSET-NOTICES.md').exists():root = root.parent
        return web.Response(text=(root/'ASSET-NOTICES.md').read_text()+'\n\n'+(root/'LICENSE').read_text(),content_type='text/plain')

    async def health(request):
        from control import release_id
        engine.store.db.execute('SELECT 1 FROM kv LIMIT 1').fetchone()
        return web.json_response({'service': 'coffee-bridge', 'status': 'ok', 'version': 2,
            'release':release_id(), 'server_id':server_id})

    async def server_identity(request):
        return web.json_response(capabilities(engine))

    async def setup_get(request):
        from configuration import setup_status
        return web.json_response(setup_status(config,engine))

    async def setup_devices(request):
        from configuration import configure_devices
        data = await request.json()
        async with setup_lock:
            return web.json_response(await configure_devices(config,engine,data))

    async def setup_feedback(request):
        from configuration import configure_feedback
        data = await request.json()
        async with setup_lock:
            return web.json_response(configure_feedback(config,engine,data))

    async def setup_certificates(request):
        from configuration import certificate_fingerprints, require_transition
        require_transition(engine,config)
        data = await request.json()
        if data.get('schema_version') != 1 or set(data) != {'schema_version','host'}:
            raise BridgeError('Invalid certificate enrollment request')
        return web.json_response({'schema_version':1, 'host':data['host'],
            'certificates':await certificate_fingerprints(data['host'])})

    def controller(request):
        if not engine.controllers or request.match_info['controller'] != engine.controllers.controller_id:
            raise web.HTTPNotFound(text='Controller is not configured')
        return engine.controllers

    async def controller_event(request):
        # Older supported HTTP clients have no identity header. New clients bind
        # before sending and supply it on every retry, without changing event bytes.
        if 'X-Sync-Server-ID' in request.headers:
            verify_server(engine, request.headers['X-Sync-Server-ID'])
        return web.json_response(accept_event(request, await request.json()), status=202)

    def accept_event(request, event):
        return {**controller(request).accept(request.match_info['controller'], event), 'server_id':server_id}

    async def controller_socket(request):
        controller(request)
        socket = web.WebSocketResponse(heartbeat=15, max_msg_size=131072, compress=False)
        await socket.prepare(request)
        await socket.send_json({'type':'hello', **capabilities(engine)})
        async for message in socket:
            if message.type != WSMsgType.TEXT:
                if message.type == WSMsgType.ERROR:break
                continue
            try:
                data = json.loads(message.data)
                if not isinstance(data, dict):raise BridgeError('Expected an object')
                verify_server(engine, data.get('server_id'))
                if data.get('type') == 'event':
                    if data.get('event', {}).get('schema_version') != 2:
                        raise BridgeError('WebSocket events require controller contract 2')
                    await socket.send_json({'type':'ack', **accept_event(request, data['event'])})
                elif data.get('type') == 'status':
                    c = controller(request)
                    await socket.send_json({'type':'status', 'server_id':server_id,
                        'poll':c.poll_status(data.get('session_id'), data.get('measurement_ready')),
                        **c.status(), 'display':c.display(c.session)})
                else:
                    raise BridgeError('Unknown controller message')
            except TelemetryExpired as exc:
                await socket.send_json({'type':'error', 'server_id':server_id, 'event_id':exc.event_id,
                    'durable':False, 'expired_telemetry':True, 'error':str(exc)})
            except (BridgeError, web.HTTPException, ValueError, KeyError, TypeError, AttributeError) as exc:
                await socket.send_json({'type':'error', 'server_id':server_id,
                    'error':str(exc) if isinstance(exc, BridgeError) else 'Invalid controller message'})
        return socket

    async def controller_status(request):
        c = controller(request)
        if 'X-Sync-Session-ID' not in request.headers:
            return web.json_response(c.status())
        verify_server(engine, request.headers.get('X-Sync-Server-ID'))
        session = request.headers['X-Sync-Session-ID']
        poll = c.poll_status(session)
        return web.json_response({'server_id':server_id, 'poll':poll,
            'summary':c.summary(), 'cycle':c.cycles.offer(session), 'display':c.display(session)})

    async def commission(request):
        return web.json_response(controller(request).commission(request.match_info['profile'], await request.json()))

    app.router.add_get('/', index)
    app.router.add_get('/healthz', health)
    app.router.add_get('/api/v1/identity', server_identity)
    app.router.add_get('/api/v1/notices', notices)
    app.router.add_get('/static/mahlkonig.svg', logo)
    app.router.add_get('/api/v1/updates/classic', classic_updates)
    app.router.add_get('/api/v1/updates/classic/files/{name}', classic_update_file)
    app.router.add_get('/api/v1/setup', setup_get)
    app.router.add_post('/api/v1/setup/devices', setup_devices)
    app.router.add_post('/api/v1/setup/feedback', setup_feedback)
    app.router.add_post('/api/v1/setup/certificates', setup_certificates)
    app.router.add_static('/static/', STATIC, show_index=False, follow_symlinks=False)
    app.router.add_get('/api/v1/status', status)
    app.router.add_post('/api/v1/shots', shot)
    app.router.add_get('/api/v1/shots/{id}', get_shot)
    app.router.add_post('/api/v1/cancel', cancel)
    app.router.add_post('/api/v1/refresh', refresh)
    app.router.add_post('/api/v1/controllers/{controller}/events', controller_event)
    app.router.add_get('/api/v1/controllers/{controller}/ws', controller_socket)
    app.router.add_get('/api/v1/controllers/{controller}/status', controller_status)
    app.router.add_post('/api/v1/controllers/{controller}/profiles/{profile}/commission', commission)
    app.router.add_get('/api/v2/machine', identity)
    app.router.add_get('/api/v2/mako', mako)
    app.router.add_post('/api/v2/brewratio', ratio)
    app.router.add_post('/api/v2/scripts/execute', execute)

    async def lifecycle(app):
        if config.get('grinder_host') and not grinder_addresses:
            from network import private_ipv4
            grinder_addresses.update(await private_ipv4(config['grinder_host']))
        control = None
        if config.get('database') and __import__('sys').platform == 'linux':
            from control import serve
            control = await serve(engine, Path(config['database']).parent / 'deployment.sock')
        await engine.start()
        backups = None
        if config.get('daily_backups') and config.get('database'):
            from backup import daily
            backups = asyncio.create_task(daily(config['database']),name='daily-backup')
        yield
        if backups:
            backups.cancel()
            await asyncio.gather(backups,return_exceptions=True)
        if control:
            control.close()
            await control.wait_closed()
        await engine.close()
        engine.store.close()
    app.cleanup_ctx.append(lifecycle)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    from configuration import configured_client
    config['_config_path'] = str(Path(args.config).resolve())
    reader = Reader(lambda: configured_client(config))
    store = Store(config['database'])
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    web.run_app(create_app(config, Engine(store, reader)), host=config['listen_host'],
                port=config.get('port', 80), access_log=None, shutdown_timeout=15)
