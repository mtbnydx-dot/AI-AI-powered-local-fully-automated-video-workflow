import asyncio
import io
import json
import tempfile
import threading
import unittest
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from imagepipe.models import CopyTask, ImageConfig, ImagePlan, ImageTask
from imagepipe.pipeline import load_run, run


class ImagePipelineHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        request = {"path": self.path, "body": body, "content_type": self.headers["Content-Type"]}
        if self.path == "/v1/images/references":
            request["parent_exists"] = self.server.parent_image.is_file()
            message = BytesParser(policy=default).parsebytes(
                f'Content-Type: {request["content_type"]}\r\n\r\n'.encode() + body
            )
            request["images"] = [part.get_payload(decode=True) for part in message.iter_parts()
                                 if part.get_param("name", header="content-disposition") == "image"]
        self.server.requests.append(request)
        self.reply({"id": f"job-{len(self.server.requests)}", "status": "queued"}, 202)

    def do_GET(self):
        if self.path.startswith("/files/"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.server.image)))
            self.end_headers()
            self.wfile.write(self.server.image)
        elif self.path.endswith("/results"):
            job_id = self.path.split("/")[-2]
            self.reply({"data": [{"index": 0, "seed": 42, "url": f"/files/{job_id}.png"}]})
        else:
            self.reply({"id": self.path.split("/")[-1], "status": "completed", "completed": 1})

    def reply(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ImagePipelineTests(unittest.TestCase):
    def test_dependencies_delivery_copy_and_completed_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "assets" / "character.png"
            child = root / "episode" / "first-frame.png"
            copied = root / "episode" / "review" / "character.png"
            run_dir = root / "run"
            env = root / ".env"
            env.write_text("IMAGEPIPE_PIPELINE_TEST_KEY=local-test-key\n")
            data = io.BytesIO()
            Image.new("RGBA", (96, 160), "teal").save(data, format="PNG")
            server = ThreadingHTTPServer(("127.0.0.1", 0), ImagePipelineHandler)
            server.requests = []
            server.image = data.getvalue()
            server.parent_image = parent
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            config = ImageConfig.model_validate({
                "provider": "qwen",
                "api": {"base_url": f"http://127.0.0.1:{server.server_port}",
                        "env_file": str(env), "key_name": "IMAGEPIPE_PIPELINE_TEST_KEY"},
                "generation": {"width": 18, "height": 32},
                "scheduler": {"http_concurrency": 2, "max_active_jobs": 4, "poll_interval_seconds": 0.01},
            })
            plan = ImagePlan(str(root), [
                ImageTask("parent", "text", "A character", [], str(parent), 42),
                ImageTask("child", "reference", "The referenced character in a room", ["parent"], str(child), 43),
            ], [CopyTask("copy-parent", str(parent), str(copied))])
            try:
                first = asyncio.run(run(plan, config, run_dir))
                saved_plan, saved_config = load_run(run_dir)
                resumed = asyncio.run(run(saved_plan, saved_config, run_dir))
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

            self.assertTrue(first["success"])
            self.assertTrue(resumed["success"])
            self.assertEqual(first["counts"], {"completed": 2})
            self.assertEqual(first["copies_completed"], 1)
            self.assertEqual(len(server.requests), 2)
            self.assertEqual([item["path"] for item in server.requests],
                             ["/v1/images/jobs", "/v1/images/references"])
            reference_request = server.requests[1]
            self.assertTrue(reference_request["parent_exists"])
            self.assertEqual(reference_request["images"], [parent.read_bytes()])
            for path in (parent, child, copied):
                self.assertEqual(path.read_bytes(), server.image)
                with Image.open(path) as image:
                    self.assertEqual((image.size, image.mode), ((96, 160), "RGBA"))
            self.assertEqual(copied.read_bytes(), parent.read_bytes())
            self.assertEqual((run_dir / "originals" / "parent.png").read_bytes(), server.image)


if __name__ == "__main__":
    unittest.main()
