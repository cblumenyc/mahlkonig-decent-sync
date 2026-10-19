"""Local-only deployment control. No administrative operation is exposed on HTTP."""
import asyncio
import json
from pathlib import Path
import re
import socket
import struct
import time

from grinder_client import BridgeError


def release_id():
    path = Path(__file__).parent / 'release.json'
    return json.loads(path.read_text()).get('version', 'development') if path.exists() else 'development'


def idle_reason(engine):
    c = engine.controllers
    if engine.active or engine.recovery:
        return 'active_cycle_or_reconciliation'
    if engine.tasks and any(not t.done() and t.get_name().startswith('delivery-') for t in engine.tasks):
        return 'delivery_in_progress'
    if not engine.ready or engine.error or time.time()-engine.last_native > 35:
        return 'grinder_state_unavailable'
    if engine.native.get('pending_brew'):
        return 'native_pending_grind'
    driver = engine.driver.get('data', {})
    if driver.get('grindState') != 'Idle' or driver.get('gbsState') != 'Idle':
        return 'grinder_not_idle'
    if not c or not c.machine.get('reachable') or c.machine.get('state') not in ('Idle', 'Sleep'):
        return 'tablet_not_idle_or_unreachable'
    if time.time()-c.last_event_received > 15:
        return 'tablet_events_unavailable'
    for record in engine.store.controller_history():
        if record.get('feedback_status') in ('collecting','awaiting_events','awaiting_final_mass',
                'awaiting_arm','awaiting_native','queued','dispatching','awaiting_native_validation'):
            return 'unfinished_controller_record'
    return None


def command(engine, request):
    op = request.get('command')
    if op == 'status':
        engine.store.db.execute('SELECT 1 FROM kv LIMIT 1').fetchone()
        c = engine.controllers
        return {'service':'coffee-bridge', 'version':release_id(), 'journal_schema':2,
            'healthy':True, 'idle_reason':idle_reason(engine), 'maintenance':engine.maintenance,
            'enabled':bool(c and c.enabled), 'feedback_mode':c.mode if c else 'manual',
            'grinder_connected':engine.ready and not engine.error,
            'tablet_connected':bool(c and c.machine.get('reachable'))}
    operation = request.get('operation')
    if not isinstance(operation, str) or not re.fullmatch('[0-9a-f]{64}', operation):
        raise BridgeError('Invalid deployment operation')
    if op == 'release':
        if engine.maintenance and engine.maintenance['operation'] == operation:
            engine.maintenance = None
        return {'released':True}
    if op != 'enter':
        raise BridgeError('Unknown deployment command')
    reason = idle_reason(engine)
    if reason:
        raise BridgeError(reason)
    # No awaits: the state-machine check and maintenance transition are atomic
    # relative to incoming grind/shot handlers in this event loop.
    if engine.maintenance and engine.maintenance['operation'] != operation:
        raise BridgeError('another_deployment_in_progress')
    engine.maintenance = {'operation':operation, 'entered_at':time.time()}
    engine.store.set('deployment_maintenance', engine.maintenance)
    return {'entered':True, 'operation':operation}


async def serve(engine, path):
    path = Path(path)
    if path.exists():
        if not path.is_socket():
            raise RuntimeError('Deployment control path is not a socket')
        path.unlink()

    async def client(reader, writer):
        try:
            peer = writer.get_extra_info('socket')
            _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != 0:
                raise BridgeError('Administrator connection required')
            line = await asyncio.wait_for(reader.readline(), timeout=3)
            if len(line) > 4096:
                raise BridgeError('Request too large')
            request = json.loads(line)
            if not isinstance(request, dict):
                raise BridgeError('Invalid request')
            result = command(engine, request)
        except Exception as exc:
            result = {'error':str(exc) if isinstance(exc, BridgeError) else 'Invalid control request'}
        writer.write(json.dumps(result).encode()+b'\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(client, path=str(path), limit=4096)
    path.chmod(0o600)
    return server
