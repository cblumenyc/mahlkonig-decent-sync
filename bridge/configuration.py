"""Versioned local setup, with credentials outside the journal and exports."""
import argparse
import asyncio
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import ssl
import uuid
from urllib.parse import urlsplit

from adapters import ClassicRest, controller_adapter
from controllers import Controllers
from control import idle_reason
from grinder_client import BridgeError, E64
from network import private_ipv4


def write_private(path, body):
    path = Path(path)
    if path.is_symlink():raise BridgeError('Configuration path must not be a symlink')
    temporary = path.with_name(path.name+'.pending')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)


def save_config(config):
    path = config.get('_config_path')
    if not path:raise BridgeError('Setup changes require a writable installation configuration')
    public = {k:v for k,v in config.items() if not k.startswith('_')}
    try:write_private(path, (json.dumps(public, indent=2)+'\n').encode())
    except PermissionError:
        raise BridgeError('This managed installation requires an administrator to save setup changes') from None


def initial_config(origin, network, data_directory):
    parts = urlsplit(origin)
    if parts.scheme != 'http' or not parts.hostname or parts.path or parts.query or parts.fragment or parts.username:
        raise ValueError('Use the server LAN HTTP origin without a path')
    if parts.port not in (None,80):raise ValueError('The E64 Xenia endpoint requires LAN port 80')
    address = ipaddress.ip_address(parts.hostname)
    subnet = ipaddress.ip_network(network)
    if address.version != 4 or not address.is_private or address not in subnet or subnet.prefixlen == 0:
        raise ValueError('The server address must be a private IPv4 address in the selected LAN')
    return {'schema_version':1, 'listen_host':'0.0.0.0', 'port':80,
        'origins':[f'http://{address}',f'http://{address}:80'], 'allowed_networks':[str(subnet)],
        'grinder_host':'', 'grinder_certificates':{}, 'setup_complete':False,
        'database':str(Path(data_directory)/'bridge.sqlite3'), 'daily_backups':True,
        'automation':{'controller_id':'classic-de1','controller_type':'classic',
            'feedback_mode':'observe','enabled':False,'wake_enabled':False}}


def require_transition(engine, config):
    if engine.active or engine.recovery or engine.native.get('pending_brew'):
        raise BridgeError('Finish the current cycle before changing setup')
    if config.get('setup_complete', bool(config.get('grinder_certificates'))):
        reason = idle_reason(engine)
        if reason:raise BridgeError('Setup requires an idle, connected system: '+reason)


def setup_status(config, engine):
    c = engine.controllers
    return {'schema_version':1, 'complete':config.get('setup_complete', bool(config.get('grinder_certificates'))),
        'controller_type':config.get('automation',{}).get('controller_type','classic'),
        'controller_url':config.get('automation',{}).get('decent_url',''),
        'grinder_host':config.get('grinder_host',''),
        'certificates_enrolled':set(config.get('grinder_certificates',{})) == {'443','9997'},
        'feedback_enabled':bool(c and c.enabled), 'targets_verified':bool(c and c.targets()['confirmed']),
        'measurement_mode':(c.observed_profile or {}).get('mode') if c else None,
        'ready':bool(c and c.sync_warning() is None),
        'required_action':c.summary() if c else 'Select a controller to begin setup'}


async def certificate_fingerprints(host):
    try:return await read_certificates(host)
    except (OSError,TimeoutError):raise BridgeError('Could not read both grinder certificates; check its address and connection') from None


async def read_certificates(host):
    address = sorted(await private_ipv4(host))[0]
    result = {}
    # Enrollment reads public certificates only. No credentials are sent through
    # this untrusted connection; normal device I/O always enforces the saved pins.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    for port in (443,9997):
        async with asyncio.timeout(5):
            reader, writer = await asyncio.open_connection(address, port, ssl=context, server_hostname=host)
            try:
                certificate = writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
                result[str(port)] = hashlib.sha256(certificate).hexdigest()
            finally:
                writer.close()
                await writer.wait_closed()
    return result


def configured_client(config):
    if not config.get('grinder_host') or set(config.get('grinder_certificates',{})) != {'443','9997'}:
        raise BridgeError('Complete grinder setup before connecting')
    path = config.get('grinder_password_file')
    password = Path(path).read_text().strip() if path else None
    return E64(config['grinder_host'], password, config['grinder_certificates'])


async def configure_devices(config, engine, data):
    require_transition(engine, config)
    if data.get('schema_version') != 1:raise BridgeError('Unsupported setup version')
    if set(data) - {'schema_version','controller_type','controller_url','grinder_host','certificates','owner_password'}:
        raise BridgeError('Unknown setup field')
    kind, url, host = data['controller_type'], data['controller_url'], data['grinder_host']
    if kind not in ('classic','decaid'):raise BridgeError('Select Classic or Decaid')
    ClassicRest(url)  # Validates the URL before any network request.
    parts = urlsplit(url)
    await private_ipv4(parts.hostname)
    addresses = await private_ipv4(host)
    if config.get('setup_complete', bool(config.get('grinder_certificates'))) and host != config['grinder_host']:
        raise BridgeError('A different grinder requires a separate installation and journal')
    pins = data['certificates']
    if pins != await certificate_fingerprints(host):
        raise BridgeError('Certificate changed during enrollment; review the new fingerprints')
    require_transition(engine, config)  # Native work may have started during enrollment.
    updated = copy.deepcopy(config)
    old = config.get('automation',{})
    changed = old.get('controller_type','classic') != kind or old.get('decent_url') != url
    auto = dict(old) if not changed else {}
    auto.update(controller_type=kind, controller_id='classic-de1' if kind == 'classic' else 'decaid-de1', decent_url=url)
    if changed or not config.get('setup_complete', bool(config.get('grinder_certificates'))):
        auto.update(enabled=False, wake_enabled=False, feedback_mode='observe')
        auto['capture_identity'] = str(uuid.uuid4())
    updated.update(automation=auto, grinder_host=host, grinder_certificates=pins,
        setup_complete=True, _grinder_addresses=sorted(addresses))
    if 'owner_password' in data:
        password = data['owner_password']
        if not isinstance(password,str) or len(password) > 1024:raise BridgeError('Invalid Owner password')
        path = Path(config['_config_path']).parent/'grinder-password'
        # Do not overwrite an existing credential during an ordinary setup edit.
        if path.exists() and path.read_text() != password:
            raise BridgeError('Change an enrolled Owner credential through the installation administrator')
        write_private(path,password.encode())
        updated['grinder_password_file'] = str(path)
    adapter = controller_adapter(auto)
    # Validate construction before changing durable configuration.
    with engine.store.atomic():
        candidate = Controllers(engine, auto, adapter)
        save_config(updated)
    config.clear();config.update(updated)
    engine.controllers = candidate
    engine.configuration_epoch += 1
    engine.native, engine.driver, engine.last_native, engine.ready = {}, {}, 0, False
    engine.refresh_requested = True
    return setup_status(config, engine)


def configure_feedback(config, engine, data):
    if (data.get('schema_version') != 1 or type(data.get('enabled')) is not bool
            or set(data) - {'schema_version','enabled','acknowledge_beta'}):
        raise BridgeError('Invalid feedback configuration')
    require_transition(engine, config)
    c = engine.controllers
    if not c or not config.get('setup_complete', bool(config.get('grinder_certificates'))):
        raise BridgeError('Complete device setup first')
    updated = copy.deepcopy(config)
    auto = updated['automation']
    if data['enabled']:
        if not c.targets()['confirmed']:
            raise BridgeError('Commission the profile and verify native grinder targets first')
        if not c.validation_complete and not auto.get('daily_use'):
            if data.get('acknowledge_beta') is not True:
                raise BridgeError('Acknowledge that physical acceptance of this beta is incomplete')
            auto['community_beta'] = {'schema_version':1,'owner_requested':True,'validation_complete':False}
    auto.update(enabled=data['enabled'], supervised_test=False,
        feedback_mode='xenia_one_press' if data['enabled'] else 'observe')
    save_config(updated)
    config.clear(); config.update(updated)
    c.config, c.enabled, c.mode, c.test_mode = auto, auto['enabled'], auto['feedback_mode'], False
    return setup_status(config,engine)


def main():
    parser = argparse.ArgumentParser(description='Create a disabled local installation configuration')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--origin',required=True)
    parser.add_argument('--network',required=True)
    parser.add_argument('--data-directory',required=True)
    args = parser.parse_args()
    if args.output.exists():parser.error('Configuration exists; existing settings were retained')
    args.output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    write_private(args.output,(json.dumps(initial_config(args.origin,args.network,args.data_directory),indent=2)+'\n').encode())
    print('Configuration created. Start the server and open its LAN address to complete setup.')


if __name__ == '__main__':
    main()
