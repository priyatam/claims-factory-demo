import json
import logging

from fastapi.testclient import TestClient

from claims.web import create_app
from tests.test_claim import OK

JPEG = b"\xff\xd8\xff" + b"\x00" * 8


def assess(_image, _kind):
    return json.dumps(OK)


def test_health_and_form():
    client = TestClient(create_app(assess=assess))
    assert client.get("/health").json() == {"status": "ok"}
    page = client.get("/")
    assert page.status_code == 200
    assert "Upload" in page.text
    assert "url" in page.text.lower()


def test_upload_returns_json_assessment():
    client = TestClient(create_app(assess=assess))
    response = client.post("/claims", files={"file": ("car.jpg", JPEG, "image/jpeg")})
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["claim_id"]
    assert body["plate"]["value"] == "ABC123"
    assert body["estimate"]["low"] < body["estimate"]["high"]


def test_logs_omit_the_plate(caplog):
    client = TestClient(create_app(assess=assess))
    with caplog.at_level(logging.INFO, logger="claims"):
        client.post("/claims", files={"file": ("car.jpg", JPEG, "image/jpeg")})
    assert "ABC123" not in caplog.text


def test_form_shows_detection_without_the_plate_characters():
    client = TestClient(create_app(assess=assess))
    response = client.post("/", files={"file": ("car.jpg", JPEG, "image/jpeg")})
    assert response.status_code == 200
    assert "ABC123" not in response.text
    assert "detected" in response.text.lower()
    assert "not a settlement" in response.text.lower()
    assert "left rear bumper dent" in response.text


def test_missing_input_is_400():
    client = TestClient(create_app(assess=assess))
    response = client.post("/claims")
    assert response.status_code == 400


def test_bad_url_is_fetch_failed():
    client = TestClient(create_app(assess=assess, fetch=lambda _url: None))
    response = client.post("/claims", data={"url": "https://example.com/missing.jpg"})
    assert response.status_code == 200
    assert response.json()["status"] == "fetch_failed"
    assert response.json()["estimate"] is None


def test_non_image_is_unreadable():
    client = TestClient(create_app(assess=assess))
    response = client.post("/claims", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.json()["status"] == "unreadable"


def test_vision_failure_is_502():
    def boom(_image, _kind):
        raise RuntimeError("claude 400")

    client = TestClient(create_app(assess=boom))
    response = client.post("/claims", files={"file": ("car.jpg", JPEG, "image/jpeg")})
    assert response.status_code == 502
    assert "400" not in response.text
