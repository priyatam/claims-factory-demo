"""Preview the public upload page locally, with no AWS: http://127.0.0.1:8081

    uv run python scripts/preview_page.py

Runs the real page handler from claims/public.py and answers every valid submit with a sample
claim, so the runtime is never called. The policy code to type is "preview" unless
POLICY_CODE_ADMIN is set.
"""

import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLICY_CODE_ADMIN", "preview")

from claims.public import handler  # noqa: E402

SAMPLE = {
    "claim_id": "a" * 32,
    "status": "ok",
    "vehicle": {"make": "Honda", "model": "Civic", "colour": "blue", "confidence": 0.8},
    "plate": {"value": None, "confidence": None},
    "damage": {"summary": "front bumper dent with scratching", "parts": ["bumper"], "severity": "moderate"},
    "estimate": {"low": 900, "high": 1300, "currency": "USD", "assumptions": ["visual only"], "confidence": 0.8},
    "partner_facts": {"policy": None, "loss_history": None, "estimating": None},
}


class Preview(BaseHTTPRequestHandler):
    def _serve(self, body: str | None = None) -> None:
        event = {"requestContext": {"http": {"method": self.command, "path": self.path}}, "body": body}
        reply = handler(event, None, invoke=lambda _image, _kind: SAMPLE)
        self.send_response(reply["statusCode"])
        for name, value in reply["headers"].items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(reply["body"].encode())

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve(self.rfile.read(int(self.headers.get("content-length", 0))).decode())

    def log_message(self, *_args) -> None:
        pass


if __name__ == "__main__":
    print("Preview at http://127.0.0.1:8081 (policy code: %s)" % os.environ["POLICY_CODE_ADMIN"], flush=True)
    HTTPServer(("127.0.0.1", 8081), Preview).serve_forever()
