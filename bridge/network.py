"""Resolve configured LAN device names without dropping certificate pinning."""
import asyncio
import ipaddress
import re
import socket
from grinder_client import BridgeError


async def private_ipv4(host):
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', host):
        raise BridgeError('Invalid LAN device hostname')
    loop = asyncio.get_running_loop()
    try:
        addresses = {r[4][0] for r in await loop.getaddrinfo(host, None, family=socket.AF_INET,
                                                          type=socket.SOCK_STREAM)}
    except OSError:
        raise BridgeError('Cannot resolve the configured LAN device') from None
    if not addresses or any(not ipaddress.ip_address(a).is_private or ipaddress.ip_address(a).is_loopback
                            or ipaddress.ip_address(a).is_link_local for a in addresses):
        raise BridgeError('Device must resolve only to private LAN IPv4 addresses')
    return addresses
