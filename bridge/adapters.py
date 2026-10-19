"""Adapter boundaries. Unverified writers and cloud delivery are never advertised."""
from urllib.parse import urlsplit
from urllib.parse import quote
import re
import aiohttp

from grinder_client import BridgeError


class LanRest:
    def __init__(self, url):
        parts = urlsplit(url)
        if parts.scheme != 'http' or not parts.hostname or parts.username or parts.query or parts.fragment:
            raise ValueError('Controller REST requires a configured LAN HTTP URL')
        self.url = url.rstrip('/')

    async def request(self, method, path, *, decode=True, **kwargs):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.request(method, self.url + path, allow_redirects=False, **kwargs) as response:
                if response.status != 200:
                    raise BridgeError('Decent REST request failed')
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > 1048576:
                        raise BridgeError('Decent REST response is too large')
                if decode:
                    import json
                    return json.loads(body)
                return bytes(body)

class ClassicRest(LanRest):
    async def status(self):
        return await self.request('GET', '/api/status/details')

    async def wake(self):
        # Matches the installed advanced_rest_api's own wake control. Never starts a shot.
        await self.request('POST', '/api/status', decode=False, json={'active': 'true'})

    async def history(self):
        names = await self.request('GET', '/api/shot')
        if not isinstance(names, list):
            raise BridgeError('Unsupported Classic history index')
        # Store opaque records for reconciliation; never evaluate Tcl from the tablet.
        valid = sorted({n for n in names if isinstance(n, str)
                        and re.fullmatch(r'[A-Za-z0-9_-]+\.(?:shot|tcl)', n)}, reverse=True)[:5]
        return [{'filename':n, 'raw':(await self.request('GET', '/api/shot/'+quote(n), decode=False)).decode('utf-8')}
                for n in valid]


class DecaidRest(LanRest):
    async def status(self):
        if not getattr(self, 'version_checked', False):
            info = await self.request('GET', '/api/v1/info')
            if info.get('version') != '0.9.0':
                raise BridgeError('This adapter requires Decaid 0.9.0')
            self.version_checked = True
        snapshot = await self.request('GET', '/api/v1/machine/state')
        state = snapshot.get('state', {})
        if not isinstance(state, dict):raise BridgeError('Unsupported Decaid machine state')
        return {'state':{'idle':'Idle','sleeping':'Sleep','espresso':'Espresso'}.get(state.get('state'),'Busy'),
                'substate':'ready' if state.get('state') == 'idle' else state.get('substate','unknown')}

    async def wake(self):
        await self.request('PUT', '/api/v1/machine/state/idle', decode=False)


def controller_adapter(config):
    if not config.get('decent_url'):return None
    kind = config.get('controller_type','classic')
    if kind not in ('classic','decaid'):raise ValueError('Unsupported controller type')
    return (ClassicRest if kind == 'classic' else DecaidRest)(config['decent_url'])
