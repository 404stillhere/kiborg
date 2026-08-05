"""Тесты Oracle endpoint panel/serve.py."""

import json
import os
import sys
import tempfile
import time
import unittest
from io import BytesIO

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # panel/
sys.path.insert(0, BASE)

import serve  # noqa: E402

from cyborg import config  # noqa: E402


class _FakeSocket:
    def __init__(self, request_bytes):
        self._rfile = BytesIO(request_bytes)
        self._wfile = BytesIO()

    def makefile(self, mode, *args, **kwargs):
        if "r" in mode:
            return self._rfile
        if "w" in mode:
            return self._wfile
        raise ValueError(mode)

    def sendall(self, data):
        self._wfile.write(data)

    def close(self):
        pass


class TestOracleEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="panel_oracle_")
        self._orig_cyborg = serve.CYBORG
        serve.CYBORG = self.tmp
        os.makedirs(os.path.join(self.tmp, "data"), exist_ok=True)

        self._orig_run = dict(serve.RUN)
        self._orig_proc = dict(serve._PROC)

    def tearDown(self):
        serve.CYBORG = self._orig_cyborg
        serve.RUN.clear()
        serve.RUN.update(self._orig_run)
        serve._PROC.clear()
        serve._PROC.update(self._orig_proc)

    def _post(self, path, body):
        raw_body = json.dumps(body, ensure_ascii=False).encode(config.HTTP_CHARSET_UTF8)
        request = (
            f"{config.HTTP_METHOD_POST} {path} HTTP/1.1\r\n"
            f"Host: {config.PANEL_HOST_PORT_TEMPLATE.format(host=config.PANEL_HOST, port=serve.PORT)}\r\n"
            f"Content-Type: {config.HTTP_MEDIA_TYPE_JSON}\r\n"
            f"Content-Length: {len(raw_body)}\r\n"
            f"\r\n".encode(config.HTTP_CHARSET_UTF8) + raw_body
        )
        sock = _FakeSocket(request)
        handler = serve.Handler.__new__(serve.Handler)
        handler.request = sock
        handler.client_address = (config.PANEL_HOST, 55555)
        handler.server = None
        handler.setup()
        handler.handle_one_request()
        sock._wfile.seek(0)
        response = sock._wfile.read()
        head, body = response.split(b"\r\n\r\n", 1)
        code = int(head.split(b" ")[1])
        return code, json.loads(body.decode(config.HTTP_CHARSET_UTF8))

    def test_oracle_endpoint_requires_goal_and_project(self):
        code, resp = self._post("/api/oracle", {"goal": "add auth"})
        self.assertEqual(code, 400)
        self.assertFalse(resp["ok"])
        self.assertIn("путь", resp["msg"])

        code, resp = self._post("/api/oracle", {"project": "M:/projects/demo"})
        self.assertEqual(code, 400)
        self.assertFalse(resp["ok"])
        self.assertIn("цель", resp["msg"])

    def test_oracle_endpoint_accepts_valid_request(self):
        # Подменяем _start_proc, чтобы не запускать реальный subprocess
        calls = []
        orig = serve._start_proc

        def fake_start_proc(goal, args):
            calls.append((goal, args))
            with serve._LOCK:
                serve.RUN.update(running=True, goal=goal, lines=[], rc=None, started=time.time())
            return True

        serve._start_proc = fake_start_proc
        try:
            code, resp = self._post(
                "/api/oracle",
                {"goal": "add auth", "project": "M:/projects/demo"},
            )
            self.assertEqual(code, 200)
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(len(calls), 1)
            self.assertIn("oracle", calls[0][0])
            self.assertIn("--mode", calls[0][1])
            self.assertIn("oracle", calls[0][1])
            self.assertIn("--project", calls[0][1])
            self.assertIn("M:/projects/demo", calls[0][1])
            self.assertIn("--goal", calls[0][1])
        finally:
            serve._start_proc = orig
