#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 服务：静态页面 + 真实测速端点 + 诊断接口。

不依赖任何第三方框架，标准库 ThreadingHTTPServer 足够跑家庭/小型办公场景。
测速端点在本机生成数据、本机回收数据，因此测的是「客户端到本服务」这一段，
部署在哪台机器，就测到哪台机器——这也是它比公共测速站更贴近实际的地方。
"""

import json
import mimetypes
import os
import posixpath
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .diagnose import diagnose, ticket_for

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MAX_UPLOAD = 256 * 1024 * 1024      # 单次上传上限 256MB
MAX_DOWNLOAD_MB = 512               # 单次下载上限 512MB
CHUNK = 64 * 1024

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


class NetriageHandler(BaseHTTPRequestHandler):
    server_version = "netriage/" + __version__
    protocol_version = "HTTP/1.1"

    # 默认的访问日志太吵，测速时每秒几十条，关掉
    def log_message(self, fmt, *args):
        return

    # ---------------- 基础工具 ----------------
    def _send(self, code, body=b"", content_type="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, data):
        self._send(code, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")

    def _read_body(self, limit=MAX_UPLOAD):
        length = self.headers.get("Content-Length")
        if length is None:
            return b""
        try:
            remaining = int(length)
        except ValueError:
            return b""
        if remaining > limit:
            return None
        chunks = []
        while remaining > 0:
            data = self.rfile.read(min(CHUNK, remaining))
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        return b"".join(chunks)

    # ---------------- GET ----------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/ping":
            self._send(204, b"", "text/plain")
            return
        if path == "/api/health":
            self._json(200, {"ok": True, "version": __version__})
            return
        if path == "/api/meta":
            self._json(200, {
                "version": __version__,
                "host": self.headers.get("Host", ""),
                "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "max_download_mb": MAX_DOWNLOAD_MB,
                "max_upload_mb": MAX_UPLOAD // (1024 * 1024),
            })
            return
        if path == "/api/download":
            self._download(query)
            return

        if path in ("/", "/index.html"):
            self._static("index.html")
            return
        if path.startswith("/static/"):
            self._static(path[len("/static/"):])
            return
        self._send(404, "404", "text/plain; charset=utf-8")

    def _download(self, query):
        try:
            mb = float(query.get("mb", ["8"])[0])
        except ValueError:
            mb = 8.0
        mb = max(0.05, min(mb, float(MAX_DOWNLOAD_MB)))
        total = int(mb * 1024 * 1024)
        block = os.urandom(CHUNK)

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(total))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Netriage-Bytes", str(total))
        self.end_headers()

        sent = 0
        try:
            while sent < total:
                size = min(CHUNK, total - sent)
                self.wfile.write(block[:size])
                sent += size
        except (BrokenPipeError, ConnectionResetError):
            return

    def _static(self, rel):
        rel = posixpath.normpath(rel.lstrip("/"))
        if rel.startswith("..") or os.path.isabs(rel):
            self._send(403, "403", "text/plain; charset=utf-8")
            return
        full = os.path.join(WEB_DIR, rel)
        if not os.path.isfile(full):
            self._send(404, "404", "text/plain; charset=utf-8")
            return
        suffix = os.path.splitext(full)[1].lower()
        content_type = _MIME.get(suffix) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as handle:
            body = handle.read()
        self._send(200, body, content_type)

    # ---------------- POST ----------------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/upload":
            started = time.time()
            body = self._read_body()
            if body is None:
                self._json(413, {"ok": False, "error": "上传体积超过上限"})
                return
            elapsed = max(time.time() - started, 1e-6)
            mbps = (len(body) * 8 / 1e6) / elapsed
            self._json(200, {"ok": True, "bytes": len(body), "seconds": round(elapsed, 3),
                             "mbps": round(mbps, 2)})
            return

        if path == "/api/diagnose":
            body = self._read_body(limit=8 * 1024 * 1024)
            if not body:
                self._json(400, {"ok": False, "error": "请求体为空"})
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception as exc:
                self._json(400, {"ok": False, "error": "JSON 解析失败：%s" % exc})
                return
            result = diagnose(payload)
            self._json(200, {"ok": True, "result": result})
            return

        if path == "/api/ticket":
            body = self._read_body(limit=8 * 1024 * 1024)
            payload = {}
            if body:
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception:
                    payload = {}
            result = payload.get("result") or diagnose(payload)
            ticket = ticket_for(payload, result)
            self._json(200, {"ok": True, "ticket": ticket})
            return

        self._json(404, {"ok": False, "error": "未知接口"})


def make_server(host="0.0.0.0", port=8080):
    server = ThreadingHTTPServer((host, port), NetriageHandler)
    server.daemon_threads = True
    return server


def serve(host="0.0.0.0", port=8080):
    server = make_server(host, port)
    actual = server.server_address[1]
    print("netriage %s 正在监听 http://%s:%s/" % (__version__, host, actual))
    print("Ctrl-C 停止")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2})
    thread.daemon = True
    thread.start()
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.shutdown()
        server.server_close()
