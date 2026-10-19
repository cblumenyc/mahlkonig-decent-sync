"""Unprivileged container entry point; never replaces an existing configuration."""
import json
import os
from pathlib import Path
import socket
import sys

from configuration import initial_config, write_private


def main():
    os.umask(0o077)
    config_path = Path('/data/config.json')
    if not config_path.exists():
        config = initial_config(os.environ['SYNC_ORIGIN'],os.environ['SYNC_LAN_CIDR'],'/data')
        write_private(config_path,(json.dumps(config,indent=2)+'\n').encode())
    else:
        config = json.loads(config_path.read_text())
    with socket.socket() as probe:
        try:probe.bind((config['listen_host'],80))
        except PermissionError:
            raise SystemExit('Port 80 binding capability is missing. Use the supplied runtime entrypoint and Compose capabilities.') from None
        except OSError:
            raise SystemExit('LAN port 80 is unavailable. Use a dedicated host/address and keep the grinder on port 80.') from None
    os.execl(sys.executable,sys.executable,'/app/app.py','--config',str(config_path))


if __name__ == '__main__':main()
