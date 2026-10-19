"""Native exports are bounded and cannot monopolize the poll event loop."""
import asyncio
import threading

import pytest

import grinder_client as native
from engine import Engine
from store import Store
from support import FakeReader


class Response:
    def __init__(self, chunks, length=None):
        self.chunks, self.content_length, self.status = chunks, length, 200
        self.content = self

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk

    async def __aenter__(self):return self
    async def __aexit__(self, *args):pass


@pytest.mark.parametrize('length', [None, 9])
def test_export_limit_enforced_with_or_without_content_length(length):
    async def run():
        with pytest.raises(native.BridgeError):
            await native.bounded_body(Response([b'1234', b'56789'], length), 8)
        assert await native.bounded_body(Response([b'1234', b'5678']), 8) == b'12345678'
    asyncio.run(run())


def test_native_processing_does_not_block_cached_xenia_poll(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def parse(data):
        entered.set()
        assert release.wait(3)
        return {'parsed': True}
    monkeypatch.setattr(native, 'snapshot', parse)
    class HTTP:
        def get(self, *args, **kwargs):
            assert kwargs['allow_redirects'] is False
            return Response([b'export'])
    async def run():
        client = native.E64('grinder.local', fingerprints={'443': 'a'*64})
        client.http = HTTP()
        engine = Engine(Store(tmp_path/'db'), FakeReader())
        task = asyncio.create_task(client.read())
        try:
            async with asyncio.timeout(1):
                while not entered.is_set():await asyncio.sleep(.001)
            # The parser is still blocked, but a scheduled poll runs immediately.
            assert engine.mako()['MA_STATUS'] == 0
        finally:
            release.set()
            assert await task == {'parsed': True}
            engine.store.close()
    asyncio.run(run())
