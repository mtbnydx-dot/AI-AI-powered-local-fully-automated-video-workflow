import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scriptpipe.llm import DEFAULT_MODEL, GLMClient, load_key


class CompletionHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append((self.path, self.headers, json.loads(body)))
        if hasattr(self.server, "request_times"):
            self.server.request_times.append(time.time())
        quota_until = getattr(self.server, "quota_until", 0)
        if time.time() < quota_until:
            reset_at = datetime.fromtimestamp(quota_until, timezone(timedelta(hours=8)))
            body = json.dumps({"error": {
                "code": "AccountQuotaExceeded",
                "message": "You have exceeded the 5-hour usage quota. It will reset at "
                           + reset_at.strftime("%Y-%m-%d %H:%M:%S %z") + " CST.",
                "type": "TooManyRequests",
            }}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        packets = [
            {"id": "local-response", "model": "deepseek-v4-1-flash", "choices": [
                {"delta": {"reasoning_content": "private reasoning"}, "finish_reason": None},
            ]},
            {"choices": [{"delta": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
             "usage": {"completion_tokens": 12}},
        ]
        body = "".join(f"data: {json.dumps(packet)}\n\n" for packet in packets)
        body = (body + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ScriptClientTests(unittest.TestCase):
    def test_streaming_request_uses_deepseek_thinking_and_configured_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), CompletionHandler)
            server.requests = []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            async def exercise():
                client = GLMClient(
                    "local-test-key", root, f"http://127.0.0.1:{server.server_port}/api/coding/v3",
                    max_tokens=72000,
                )
                try:
                    self.assertEqual(await client.complete("configured", "Write JSON", {"text": "故事"}),
                                     {"ok": True})
                    await client.complete("override", "Write JSON", {}, max_tokens=8192)
                finally:
                    await client.close()

            try:
                asyncio.run(exercise())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

            for path, headers, request in server.requests:
                self.assertEqual(path, "/api/coding/v3/chat/completions")
                self.assertEqual(headers["Authorization"], "Bearer local-test-key")
                self.assertEqual(request["model"], DEFAULT_MODEL)
                self.assertEqual(request["thinking"], {"type": "enabled"})
                self.assertEqual(request["reasoning_effort"], "high")
                self.assertTrue(request["stream"])
            self.assertEqual([request["max_tokens"] for _, _, request in server.requests], [72000, 8192])
            response = json.loads(next(root.glob("configured.*.response.json")).read_text())
            self.assertEqual(response["served_model"], "deepseek-v4-1-flash")
            self.assertEqual(response["reasoning_characters"], len("private reasoning"))
            self.assertEqual(response["usage"]["completion_tokens"], 12)
            self.assertNotIn("private reasoning", json.dumps(response))

    def test_account_quota_wait_is_shared_and_survives_client_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), CompletionHandler)
            server.requests = []
            server.request_times = []
            server.quota_until = int(time.time()) + 3
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            endpoint = f"http://127.0.0.1:{server.server_port}/api/coding/v3"

            async def exercise():
                client = GLMClient("local-test-key", root, endpoint)
                resumed = None
                first = asyncio.create_task(client.complete("first", "Write JSON", {}))

                async def wait_for_record():
                    while not (root / "quota_wait.json").exists():
                        await asyncio.sleep(0.01)

                try:
                    await asyncio.wait_for(wait_for_record(), timeout=2)
                    resumed = GLMClient("local-test-key", root, endpoint)
                    self.assertEqual(resumed.quota_reset_at, server.quota_until)
                    results = await asyncio.gather(
                        first,
                        client.complete("queued", "Write JSON", {}),
                        resumed.complete("resumed", "Write JSON", {}),
                    )
                    self.assertEqual(results, [{"ok": True}] * 3)
                finally:
                    await client.close()
                    if resumed is not None:
                        await resumed.close()

            try:
                asyncio.run(exercise())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

            self.assertEqual(len(server.requests), 4)
            self.assertLess(server.request_times[0], server.quota_until)
            self.assertTrue(all(timestamp >= server.quota_until
                                for timestamp in server.request_times[1:]))
            for _, _, request in server.requests:
                self.assertEqual(request["thinking"], {"type": "enabled"})
                self.assertEqual(request["reasoning_effort"], "high")
            events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
            self.assertEqual(len([event for event in events if event["event"] == "quota_wait"]), 1)
            self.assertTrue(all(event["attempt"] == 1 for event in events
                                if event["event"] == "request_started"))
            self.assertFalse(list(root.glob("*.error.json")))

    def test_named_key_reads_dotenv_and_environment_without_shell_execution(self):
        key_name = "SCRIPTPIPE-TEST-KEY"
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text('GLMKEY=other-key\nexport SCRIPTPIPE-TEST-KEY="literal-$(command)"\n')
            previous = os.environ.pop(key_name, None)
            try:
                self.assertEqual(load_key(env_path, key_name), "literal-$(command)")
                os.environ[key_name] = "environment-key"
                self.assertEqual(load_key(env_path, key_name), "environment-key")
            finally:
                os.environ.pop(key_name, None)
                if previous is not None:
                    os.environ[key_name] = previous


if __name__ == "__main__":
    unittest.main()
