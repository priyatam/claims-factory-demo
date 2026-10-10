import base64
import json

from starlette.testclient import TestClient

from claims import localhost
from tests.test_claim import OK

JPEG = b"\xff\xd8\xff" + b"\x00" * 8
CODE = localhost.os.environ["POLICY_CODE_ADMIN"]


def post(client, policy=CODE, image=JPEG):
    body = {"policy": policy, "image_b64": base64.b64encode(image).decode()}
    return client.post("/", content=json.dumps(body))


def test_serves_the_styled_page_with_the_form():
    response = TestClient(localhost.app).get("/")
    assert response.status_code == 200
    assert "<style>" in response.text and 'id="policy"' in response.text


def test_valid_upload_runs_the_claim_in_process(monkeypatch):
    seen = {}

    def run(payload):
        seen.update(payload)
        return {"claim_id": "c1", "status": "ok"}

    monkeypatch.setattr(localhost, "run_payload", run)
    response = post(TestClient(localhost.app))
    assert response.json()["status"] == "ok"
    assert seen["media_type"] == "image/jpeg"
    assert base64.b64decode(seen["image_b64"]) == JPEG


def test_wrong_code_or_non_image_never_reaches_the_harness(monkeypatch):
    monkeypatch.setattr(localhost, "run_payload", lambda _p: (_ for _ in ()).throw(AssertionError("called")))
    client = TestClient(localhost.app)
    assert post(client, policy="wrong").status_code == 400
    assert post(client, image=b"not an image").status_code == 400


def test_agent_routes_are_still_there():
    assert TestClient(localhost.app).get("/ping").status_code == 200
