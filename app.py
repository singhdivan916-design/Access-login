#!/usr/bin/env python3
"""
Fully transparent forwarder.

  Request  : client method + path + headers + body  ->  upstream verbatim
  Response : upstream status + headers + body      ->  client verbatim

The proxy does not add, remove, or rewrite anything on the response path
except hop-by-hop headers (which the HTTP spec forbids forwarding).
Content-Encoding is preserved, so compressed bodies flow through untouched.
"""
import sys
import warnings
import socketserver
warnings.filterwarnings("ignore")
import requests
requests.packages.urllib3.disable_warnings()
from http.server import HTTPServer, BaseHTTPRequestHandler

C = "\033[1;36m"; G = "\033[1;32m"; R = "\033[1;31m"
Y = "\033[1;33m"; W = "\033[1;37m"; S = "\033[0m"

UPSTREAM    = "https://loginbp.ppmainecoonghj.com"
LISTEN_PORT = 5030

# hop-by-hop headers (RFC 7230 §6.1) — MUST NOT be forwarded either way
HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

# request-only drops: Host must be rewritten so we reach the real server.
# We also let `requests` compute Content-Length from the actual body bytes.
REQ_DROP = HOP | {"host", "content-length"}


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads  = True
    allow_reuse_address = True


class ForwardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):     self._forward()
    def do_POST(self):    self._forward()
    def do_PUT(self):     self._forward()
    def do_DELETE(self):  self._forward()
    def do_HEAD(self):    self._forward()
    def do_OPTIONS(self): self._forward()
    def do_PATCH(self):   self._forward()

    def _forward(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body   = self.rfile.read(length) if length else None

        # Forward everything the client sent, minus hop-by-hop + Host/CL
        fwd_headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in REQ_DROP
        }

        try:
            r = requests.request(
                method          = self.command,
                url             = UPSTREAM + self.path,
                headers         = fwd_headers,
                data            = body,
                verify          = False,
                timeout         = 30,
                allow_redirects = False,
                stream          = True,
            )
            # Preserve raw (possibly gzip/deflate) bytes so Content-Encoding
            # + Content-Length from the server remain untouched.
            r.raw.decode_content = False
            raw = r.raw.read()
        except Exception as e:
            print(R + f"  ✗ upstream error: {e}" + S)
            return self._empty(502)

        print(C + f"  → {self.command} {self.path}  ({len(body) if body else 0} B)"
              + G + f"   ← {r.status_code} ({len(raw)} B)" + S)

        # Write status line only — BaseHTTPRequestHandler would otherwise
        # inject its own Server/Date headers on top of upstream's.
        self.send_response_only(r.status_code, r.reason)

        has_content_length = False
        for k, v in r.headers.items():
            lk = k.lower()
            if lk in HOP:
                continue
            if lk == "content-length":
                has_content_length = True
            self.send_header(k, v)

        # Only add Content-Length if upstream didn't provide one
        # (e.g. upstream replied with chunked encoding).
        if not has_content_length and self.command != "HEAD":
            self.send_header("Content-Length", str(len(raw)))

        self.end_headers()

        if self.command != "HEAD" and raw:
            try:
                self.wfile.write(raw)
            except Exception:
                pass

    def _empty(self, code: int):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def log_message(self, *args):
        pass


def banner():
    import os
    os.system("cls" if os.name == "nt" else "clear")
    print(W + r"""
        ██████╗ ██╗██╗   ██╗ █████╗ ███╗   ██╗
        ██╔══██╗██║██║   ██║██╔══██╗████╗  ██║
        ██║  ██║██║██║   ██║███████║██╔██╗ ██║
        ██║  ██║██║╚██╗ ██╔╝██╔══██║██║╚██╗██║
        ██████╔╝██║ ╚████╔╝ ██║  ██║██║ ╚████║
        ╚═════╝ ╚═╝  ╚═══╝  ╚═╝  ╚═╝╚═╝  ╚═══╝
    """ + S)
    print(C + "        " + "═" * 55 + S)
    print(Y + "        Transparent forwarder — 100% pass-through" + S)
    print(C + "        " + "═" * 55 + S + "\n")


if __name__ == "__main__":
    banner()
    print(C + f"        [*] Listening on http://0.0.0.0:{LISTEN_PORT}  →  {UPSTREAM}" + S)
    print(G + "        [✓] Ready. Press Ctrl+C to stop.\n" + S)
    srv = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), ForwardHandler)
    srv.handle_error = lambda *a: None
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print(R + "\n        [!] Shutting down." + S)
        srv.shutdown()
        sys.exit(0)
