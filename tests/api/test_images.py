"""The image fetcher's refusals, redirects, limits and byte checks, against a fake resolver and connection."""

from __future__ import annotations

import unittest

from app.api import images
from app.api.images import ImageError, fetch

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PUBLIC = "93.184.216.34"


class Response:
    def __init__(self, status=200, headers=None, chunks=(PNG,), delay=0.0, clock=None):
        self.status, self.headers, self.chunks = status, headers or {}, list(chunks)
        self.delay, self.clock = delay, clock

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, size):
        if self.clock:
            self.clock.now += self.delay
        return self.chunks.pop(0) if self.chunks else b""


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Site:
    """Answers requests by (host, target) and records every connection it was asked to open."""

    def __init__(self, answers, addresses=None):
        self.answers, self.addresses = answers, addresses or {}
        self.connections: list = []

    def resolve(self, host):
        return self.addresses.get(host, [PUBLIC])

    def connect(self, host, address, timeout):
        site = self

        class Connection:
            def request(self, method, target, headers):
                self.sent = (method, target, headers)

            def getresponse(self):
                return site.answers[(host, self.sent[1])]

            def close(self):
                pass

        connection = Connection()
        self.connections.append((host, address, timeout, connection))
        return connection

    def fetch(self, url, clock=None):
        return fetch(url, resolver=self.resolve, connect=self.connect, clock=clock or Clock())


class FetchTests(unittest.TestCase):
    def test_a_png_comes_back_with_its_sniffed_type(self) -> None:
        site = Site({("x.test", "/a.png?v=1"): Response(headers={"Content-Type": "text/html"})})
        self.assertEqual(site.fetch("https://x.test/a.png?v=1#frag"), ("image/png", PNG))

    def test_the_connection_goes_to_the_vetted_address_under_the_hostname(self) -> None:
        site = Site({("x.test", "/a.png"): Response()})
        site.fetch("https://x.test/a.png")
        host, address, timeout, connection = site.connections[0]
        method, target, headers = connection.sent
        self.assertEqual((host, address, method, target), ("x.test", PUBLIC, "GET", "/a.png"))
        self.assertEqual(headers["Host"], "x.test")
        self.assertFalse({"Cookie", "Referer"} & set(headers))
        self.assertLessEqual(timeout, images.DEADLINE_SECONDS)

    def test_the_pinned_connection_dials_the_address_but_names_the_host(self) -> None:
        connection = images.PinnedConnection("x.test", PUBLIC, 5)
        self.assertEqual((connection.host, connection.port, connection.address), ("x.test", 443, PUBLIC))

    def test_other_schemes_ports_and_credentials_are_refused(self) -> None:
        site = Site({})
        for url in ("http://x.test/a.png", "https://x.test:8443/a.png", "https://a@x.test/a.png", "ftp://x.test/a",
                    "https:///a.png", "data:image/png;base64,AA=="):
            with self.subTest(url=url), self.assertRaises(ImageError):
                site.fetch(url)
        self.assertEqual(site.connections, [])

    def test_hosts_that_resolve_to_private_addresses_are_refused(self) -> None:
        for addresses in (["127.0.0.1"], ["10.0.0.5"], ["169.254.169.254"], ["::1"], ["fd12::1"], ["::ffff:127.0.0.1"],
                          ["192.168.1.1"], ["0.0.0.0"], [PUBLIC, "10.0.0.5"], []):
            site = Site({("x.test", "/a.png"): Response()}, {"x.test": addresses})
            with self.subTest(addresses=addresses), self.assertRaises(ImageError):
                site.fetch("https://x.test/a.png")
            self.assertEqual(site.connections, [])

    def test_a_redirect_is_checked_again_and_relative_locations_resolve(self) -> None:
        site = Site({("x.test", "/a.png"): Response(302, {"Location": "/b/c.png"}),
                     ("x.test", "/b/c.png"): Response(301, {"Location": "https://cdn.test/c.png"}),
                     ("cdn.test", "/c.png"): Response()})
        self.assertEqual(site.fetch("https://x.test/a.png")[0], "image/png")
        private = Site({("x.test", "/a.png"): Response(302, {"Location": "https://inside.test/a.png"})},
                       {"inside.test": ["10.1.2.3"]})
        with self.assertRaises(ImageError):
            private.fetch("https://x.test/a.png")
        self.assertEqual([host for host, *_ in private.connections], ["x.test"])

    def test_more_than_three_redirects_are_refused(self) -> None:
        site = Site({("x.test", f"/{n}"): Response(302, {"Location": f"/{n + 1}"}) for n in range(5)})
        with self.assertRaises(ImageError):
            site.fetch("https://x.test/0")
        self.assertEqual(len(site.connections), 4)

    def test_a_body_over_the_cap_is_refused_with_or_without_a_length(self) -> None:
        big = images.MAX_BYTES + 1
        declared = Site({("x.test", "/a.png"): Response(headers={"Content-Length": str(big)}, chunks=())})
        with self.assertRaises(ImageError):
            declared.fetch("https://x.test/a.png")
        streamed = Site({("x.test", "/a.png"): Response(chunks=[PNG] + [b"\x00" * 1_048_576] * 11)})
        with self.assertRaises(ImageError):
            streamed.fetch("https://x.test/a.png")

    def test_a_slow_host_is_cut_off_at_the_total_deadline(self) -> None:
        clock = Clock()
        site = Site({("x.test", "/a.png"): Response(chunks=[PNG] + [b"\x00"] * 100, delay=1.0, clock=clock)})
        with self.assertRaises(ImageError):
            site.fetch("https://x.test/a.png", clock=clock)
        self.assertLessEqual(clock.now, images.DEADLINE_SECONDS + 1)

    def test_only_raster_image_bytes_are_returned(self) -> None:
        for body in (b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"<!doctype html><p>hi", b""):
            site = Site({("x.test", "/a.png"): Response(headers={"Content-Type": "image/png"}, chunks=[body])})
            with self.subTest(body=body[:10]), self.assertRaises(ImageError):
                site.fetch("https://x.test/a.png")
        for body, kind in ((b"\xff\xd8\xff\xe0rest", "image/jpeg"), (b"GIF89a....", "image/gif"),
                           (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"), (b"\x00\x00\x00\x1cftypavif....", "image/avif")):
            site = Site({("x.test", "/a"): Response(chunks=[body])})
            self.assertEqual(site.fetch("https://x.test/a")[0], kind)

    def test_errors_name_the_host_but_never_the_path_or_query(self) -> None:
        def broken(host, address, timeout):
            raise OSError("connection reset")
        cases = [lambda url: fetch(url, resolver=lambda host: [PUBLIC], connect=broken, clock=Clock()),
                 Site({("x.test", "/secret/a.png?token=abc"): Response(404)}).fetch]
        for case in cases:
            with self.assertRaises(ImageError) as caught:
                case("https://x.test/secret/a.png?token=abc")
            self.assertIn("x.test", str(caught.exception))
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
