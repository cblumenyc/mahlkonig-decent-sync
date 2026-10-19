"""Strict scalar validation shared by controller events and measurements."""
from datetime import datetime
import json
import math

from grinder_client import BridgeError


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def number(value, name, low=0, high=1000000):
    if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
        raise BridgeError(f'Invalid {name}')
    return value


def timestamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
        return stamp.timestamp()
    except (AttributeError, TypeError, ValueError):
        raise BridgeError('Use a timestamp with an explicit timezone') from None


def quantity(value, unit, source):
    if not isinstance(value, dict) or value.get('unit') != unit or value.get('source') != source:
        raise BridgeError(f'Expected {unit} from {source}')
    return number(value.get('value'), source, 0, 1000)
