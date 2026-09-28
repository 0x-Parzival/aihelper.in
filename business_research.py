"""Fetch one public business page as attributed evidence, never as confirmation."""
import http.client
import ipaddress
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 262144


def public_target(url):
    if not isinstance(url, str) or len(url) > 2000 or any(ord(c) < 33 for c in url):
        raise ValueError("Invalid public URL")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a public HTTP(S) URL without credentials")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port != (443 if parsed.scheme == "https" else 80):
        raise ValueError("Only standard web ports are allowed")
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Host has no addresses")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        transition = isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo)
        if not ip.is_global or ip.is_multicast or transition:
            raise ValueError("URL must resolve only to public addresses")
    return parsed, port, addresses[0][4][0]


def fetch_page(url):
    for _ in range(4):
        parsed, port, address = public_target(url)
        # Pin the validated IP; never resolve the hostname again when connecting.
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=8)
        try:
            connection.sock = socket.create_connection((address, port), timeout=8)
            if parsed.scheme == "https":
                connection.sock = ssl.create_default_context().wrap_socket(connection.sock, server_hostname=parsed.hostname)
            connection.request("GET", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""), headers={"User-Agent": "AIHelper-BusinessResearch/1.0", "Accept": "text/html,text/plain", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Redirect has no destination")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"Source returned HTTP {response.status}")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"text/html", "text/plain"} or response.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("Source must be uncompressed HTML or plain text")
            payload = response.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ValueError("Source exceeds 256 KiB")
            return url, payload.decode("utf-8", errors="replace"), content_type
        finally:
            connection.close()
    raise ValueError("Too many redirects")


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def research_business(url):
    """Return a save_facts-compatible research fact. Page text is untrusted data.

    This is a sourced excerpt, not a verified business identity or a summary.
    Caller must explicitly choose the contact; no customer communication occurs.
    """
    final_url, body, content_type = fetch_page(url)
    if content_type == "text/html":
        parser = PageText()
        parser.feed(body)
        body = " ".join(parser.parts)
    excerpt = " ".join(body.split())[:2000]
    if not excerpt:
        raise ValueError("Source contains no readable text")
    return {"research": {"value": excerpt, "evidence": "Public page excerpt; business identity and claims not independently verified.", "source_url": final_url, "confirmed": False}}
