"""SSRF guard for web.fetch (SPEC 15.2): public addresses only, re-checked on every redirect hop."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

MAX_REDIRECTS = 3


class BlockedURL(ValueError):
    pass


def check_ip(ip: str) -> None:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if not addr.is_global or addr.is_multicast:
        raise BlockedURL(f"address {addr} is not public")


def pinned(url: str, ip: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """(url to connect to, headers, httpx extensions): the request goes to the checked IP while
    the Host header and TLS (SNI and certificate verification) keep the real host name. This
    closes the DNS-rebinding window between the check and the connect."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    literal = f"[{ip}]" if ":" in ip else ip
    netloc = f"{literal}:{parts.port}" if parts.port else literal
    target = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    headers = {"Host": parts.netloc.rsplit("@", 1)[-1]}
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    return target, headers, extensions


async def check_url(url: str) -> str:
    """Allow http(s) URLs whose host resolves only to public addresses; return one to use."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedURL(f"scheme {parts.scheme!r} is not allowed")
    if not parts.hostname:
        raise BlockedURL("URL has no host")
    if parts.username or parts.password:
        raise BlockedURL("credentials in URLs are not allowed")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise BlockedURL(f"cannot resolve {parts.hostname}") from exc
    if not infos:
        raise BlockedURL(f"cannot resolve {parts.hostname}")
    addresses = [str(sockaddr[0]) for *_, sockaddr in infos]
    for ip in addresses:  # every answer must be public, not just the one we use
        check_ip(ip)
    return addresses[0]
