"""Images in a source note, fetched for the reading page when it is opened and never stored.

Only URLs already saved in indexed notes reach here (the route looks them up by block). Each fetch is https on port
443 to a host whose every address is public, dialled at the address that was checked so DNS cannot change in between,
and every redirect is checked again. No cookies or referrer are sent. The whole fetch has one deadline and one size
cap, and only PNG, JPEG, GIF, WebP and AVIF bytes come back, identified from the bytes themselves. Errors name the
host, never the path or query, which can carry signed tokens.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import threading
import time
from typing import Callable
from urllib import parse

MAX_BYTES = 10 * 1024 * 1024
DEADLINE_SECONDS = 10.0
MAX_REDIRECTS = 3
REDIRECTS = {301, 302, 303, 307, 308}
CHUNK = 64 * 1024
HEADERS = {"User-Agent": "knowledge-agent image fetch", "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif"}
# Routes share one thread pool, so slow image hosts must not be able to take all of it.
SLOTS = threading.BoundedSemaphore(8)
SLOT_WAIT_SECONDS = 5.0
# Global unicast is 2000::/3. 6to4 (2002::/16) can wrap any IPv4 address, so it is refused too.
UNICAST_V6 = ipaddress.ip_network("2000::/3")
SIX_TO_FOUR = ipaddress.ip_network("2002::/16")


class ImageError(Exception):
    """The image could not be fetched or is not an allowed image."""


class PinnedConnection(http.client.HTTPSConnection):
    """An https connection to `host` that dials `address`. SNI, the certificate check and `Host` all stay the hostname."""

    def __init__(self, host: str, address: str, timeout: float) -> None:
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        # The plain socket is visible before the handshake, so the deadline can cut a slow handshake too.
        self.sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def resolve(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def sniff(body: bytes) -> str | None:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    if body[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    return None


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip not in UNICAST_V6 or ip in SIX_TO_FOUR:  # NAT64, IPv4-compatible and site-local forms fall here
            return False
    return ip.is_global and not ip.is_multicast


def _parts(url: str) -> tuple[str, str]:
    """The host and request target of an allowed URL."""
    try:
        parts = parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise ImageError("the image link is not a valid URL") from None
    host = (parts.hostname or "").rstrip(".")
    if parts.scheme != "https" or not host or port not in (None, 443) or parts.username or parts.password:
        raise ImageError(f"only plain https images can be shown ({host or 'no host'})")
    return host, (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def fetch(url: str, *, resolver: Callable[[str], list[str]] = resolve,
          connect: Callable[[str, str, float], http.client.HTTPSConnection] = PinnedConnection,
          clock: Callable[[], float] = time.monotonic) -> tuple[str, bytes]:
    """The image at `url` as (content type, bytes)."""
    deadline = clock() + DEADLINE_SECONDS
    for _ in range(MAX_REDIRECTS + 1):
        host, target = _parts(url)
        try:
            addresses = resolver(host)
        except (OSError, ValueError):  # an invalid international name fails as UnicodeError
            raise ImageError(f"{host} could not be found") from None
        if not addresses or not all(_public(address) for address in addresses):
            raise ImageError(f"{host} is not a public address")
        address = min(addresses, key=lambda item: ":" in item)  # IPv4 first: the server may have no IPv6 route
        try:
            connection = connect(host, address, _left(deadline, clock, host))
        except OSError as exc:
            raise ImageError(f"{host} could not be reached ({type(exc).__name__})") from None
        # A socket timeout only bounds one wait, so a host sending a byte at a time could hold the thread for
        # hours. The watchdog shuts the socket when the whole fetch's time is up, wherever the fetch is waiting.
        expired = threading.Event()
        watchdog = threading.Timer(_left(deadline, clock, host), _cut, (connection, expired))
        watchdog.daemon = True
        watchdog.start()
        try:
            connection.request("GET", target, headers={"Host": host, **HEADERS})
            response = connection.getresponse()
            if response.status in REDIRECTS:
                location = response.getheader("Location")
                if not location:
                    raise ImageError(f"{host} redirected without a location")
                url = parse.urljoin(url, location)
                continue
            if response.status != 200:
                raise ImageError(f"{host} answered {response.status}")
            body = _read(response, host)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            if expired.is_set():
                raise ImageError(f"{host} took too long") from None
            raise ImageError(f"{host} could not be reached ({type(exc).__name__})") from None
        finally:
            watchdog.cancel()
            connection.close()
        if expired.is_set():
            raise ImageError(f"{host} took too long")
        kind = sniff(body)
        if kind is None:
            raise ImageError(f"{host} did not send a PNG, JPEG, GIF, WebP or AVIF image")
        return kind, body
    raise ImageError("the image link redirected too many times")


def _left(deadline: float, clock: Callable[[], float], host: str) -> float:
    left = deadline - clock()
    if left <= 0:
        raise ImageError(f"{host} took too long")
    return left


def _cut(connection, expired: threading.Event) -> None:
    expired.set()
    sock = getattr(connection, "sock", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def _read(response, host: str) -> bytes:
    length = response.getheader("Content-Length")
    expected = int(length) if length and length.isdigit() else None
    if expected is not None and expected > MAX_BYTES:
        raise ImageError(f"the image from {host} is too large")
    body = bytearray()
    while chunk := response.read1(CHUNK):
        body += chunk
        if len(body) > MAX_BYTES:
            raise ImageError(f"the image from {host} is too large")
    # A connection closed early ends the read without an error; a partial image must not be cached as whole.
    if expected is not None and len(body) != expected:
        raise ImageError(f"the image from {host} was cut short")
    return bytes(body)
