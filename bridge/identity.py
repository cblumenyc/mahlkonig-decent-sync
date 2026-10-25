"""Stable installation identity and explicit controller capabilities."""
import json
from pathlib import Path
import uuid

from control import release_id
from grinder_client import BridgeError


def installation_id(store):
    with store.atomic():
        identity = store.get('installation_id')
        if identity is None:
            identity = str(uuid.uuid4())
            store.set('installation_id', identity)
    return identity


def capabilities(engine):
    path = Path(__file__).with_name('product.json')
    if not path.exists():
        path = Path(__file__).resolve().parents[1] / 'release-spec.json'
    product = json.loads(path.read_text())
    return {'schema_version': 1, 'server_id': installation_id(engine.store),
        'product': product['slug'], 'name': product['name'], 'version': release_id(),
        'controller_id': engine.controllers.controller_id if engine.controllers else None,
        'controller_contracts': product['controller_contracts'],
            'telemetry_poll': 1,
        'journal_schema': product['journal_schema'], 'setup_api': 1,
        'transports': ['http', 'websocket'], 'feedback_modes': ['observe', 'xenia_one_press'],
        'starts_extraction': False, 'multiple_controllers': False}


def verify_server(engine, expected):
    if expected != installation_id(engine.store):
        raise BridgeError('Server identity differs; saved events remain bound to their original installation')
