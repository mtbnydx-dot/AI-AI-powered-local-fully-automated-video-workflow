import asyncio
import json
import tempfile
import threading
import unittest
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from imagepipe.client import ImageClient
from imagepipe.models import ImageConfig, ImageTask


class ImageJobHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append((self.path, self.headers, body))
        self.reply({"id": "job-1", "status": "queued"}, 202)

    def do_GET(self):
        if self.path.endswith("/results"):
            self.reply({"data": [{"index": 0, "seed": 7, "url": "/files/image.png"}]})
        elif self.path.startswith("/files/"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(self.server.image)
        else:
            self.reply({"id": "job-1", "status": "completed", "completed": 1})

    def reply(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ImageClientTests(unittest.TestCase):
    def test_real_http_json_multipart_and_download_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            references = [root / "first.png", root / "second.png"]
            for path, color in zip(references, ["red", "blue"]):
                Image.new("RGB", (9, 16), color).save(path)
            env_file = root / ".env"
            env_file.write_text('IMAGEPIPE_TEST_KEY="local-test-key"\n')
            server = ThreadingHTTPServer(("127.0.0.1", 0), ImageJobHandler)
            server.requests = []
            server.image = references[0].read_bytes()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            config = ImageConfig.model_validate({"provider": "qwen", "api": {
                "base_url": f"http://127.0.0.1:{server.server_port}",
                "env_file": str(env_file), "key_name": "IMAGEPIPE_TEST_KEY",
            }})

            async def exercise():
                async with ImageClient(config) as client:
                    for mode, images in [("text", []), ("edit", references[:1]), ("reference", references)]:
                        task = ImageTask("asset", mode, "Exact input prompt", [], str(root / "target.png"), 7)
                        submission = await client.submit(task, images)
                        self.assertEqual(submission["id"], "job-1")
                    self.assertEqual((await client.status("job-1"))["status"], "completed")
                    result = await client.results("job-1")
                    await client.download(result["data"][0]["url"], root / "download.png")

            try:
                asyncio.run(exercise())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
            self.assertEqual((root / "download.png").read_bytes(), server.image)
            text_path, headers, body = server.requests[0]
            self.assertEqual(text_path, "/v1/images/jobs")
            self.assertEqual(headers["Authorization"], "Bearer local-test-key")
            self.assertEqual(json.loads(body), {
                "model": "qwen-image-2.1", "prompt": "Exact input prompt", "size": "736x1280",
                "steps": 20, "n": 1, "seed": 7,
            })
            for (path, headers, body), expected_path, filenames in zip(
                server.requests[1:], ["/v1/images/edits", "/v1/images/references"],
                [["first.png"], ["first.png", "second.png"]],
            ):
                self.assertEqual(path, expected_path)
                message = BytesParser(policy=default).parsebytes(
                    f'Content-Type: {headers["Content-Type"]}\r\n\r\n'.encode() + body
                )
                images = [part for part in message.iter_parts()
                          if part.get_param("name", header="content-disposition") == "image"]
                self.assertEqual([part.get_filename() for part in images], filenames)
                self.assertEqual(images[0].get_payload(decode=True), references[0].read_bytes())


if __name__ == "__main__":
    unittest.main()
