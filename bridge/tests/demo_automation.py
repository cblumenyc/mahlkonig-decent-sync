"""Loopback-only dashboard fixture. Uses FakeReader and cannot contact real devices."""
import asyncio
from pathlib import Path
import sys
from aiohttp import web
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from engine import Engine
from store import Store
from support import FakeReader
from support import profile,event,Tablet
reader=FakeReader();reader.state['recipes'][0]['recipe_brew_weight']=45000
reader.state['grinds'][0]['recipe_brew_weight']=45000
engine=Engine(Store(':memory:'),reader)
config={'origins':['http://127.0.0.1:8681'],'allowed_networks':['127.0.0.0/8'],
        'grinder_host':'127.0.0.1','automation':{'feedback_mode':'observe'}}
app=create_app(config,engine);engine.controllers.adapter=Tablet()
async def simulation(app):
    async def feed():
        n=0
        while True:
            engine.mako()
            engine.controllers.accept('classic-de1',event(n,'profile',p=profile()))
            n+=1
            await asyncio.sleep(2)
    task=asyncio.create_task(feed())
    yield
    task.cancel();await asyncio.gather(task,return_exceptions=True)
app.cleanup_ctx.append(simulation)
web.run_app(app,host='127.0.0.1',port=8681,access_log=None)
