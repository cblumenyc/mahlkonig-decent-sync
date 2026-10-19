"""Health is process/journal availability; sync readiness remains a separate fact."""
import json
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlsplit

try:
    config = json.loads(Path('/data/config.json').read_text())
    request = urllib.request.Request('http://127.0.0.1/healthz',
        headers={'Host':urlsplit(config['origins'][0]).netloc})
    with urllib.request.urlopen(request,timeout=3) as response:
        status = json.load(response)
    if status.get('status') != 'ok' or status.get('service') != 'coffee-bridge':sys.exit(1)
except Exception:
    sys.exit(1)
