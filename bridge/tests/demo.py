"""Isolated UI test fixture. No connection to any real grinder. Never deploy this."""
import asyncio
import sys
from pathlib import Path
from aiohttp import web
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from engine import Engine
from store import Store
from support import FakeReader

reader=FakeReader()
engine=Engine(Store(':memory:'), reader)
config={'origins':['http://127.0.0.1:8680'],'allowed_networks':['127.0.0.0/8'],
        'grinder_host':'127.0.0.1'}
app=create_app(config,engine)
async def simulation(app):
    async def poll():
        while not engine.ready:await asyncio.sleep(.1)
        engine.brewratio({'SYNC_BEVERAGE_WEIGHT':33,'SYNC_FILTER':2})
        engine.capture_grind();engine.execute({'ID':9})
        delivered=False
        while True:
            result=engine.mako()
            if result['MA_STATUS']==4 and result['MA_EXTRACTION_STATUS']==0 and not delivered:
                reader.finish(result);engine.refresh_requested=True;delivered=True
            await asyncio.sleep(2)
    task=asyncio.create_task(poll())
    yield
    task.cancel()
    await asyncio.gather(task,return_exceptions=True)
app.cleanup_ctx.append(simulation)
web.run_app(app,host='127.0.0.1',port=8680,access_log=None)
