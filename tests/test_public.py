import base64
import json

import pytest

from claims.public import ERROR, MAX_IMAGE, handler

JPEG = b"\xff\xd8\xff" + b"\x00" * 16
CODE = "test-code"


@pytest.fixture(autouse=True)
def policy_code(monkeypatch):
    monkeypatch.setenv("POLICY_CODE_ADMIN", CODE)


def post(code: object, image: bytes = JPEG, **extra) -> dict:
    body = {"policy": code, "image_b64": base64.b64encode(image).decode("ascii"), **extra}
    return {"requestContext": {"http": {"method": "POST", "path": "/"}}, "body": json.dumps(body)}


def never(_image, _kind):
    raise AssertionError("the runtime must not be called")


def test_page_has_a_ten_character_code_box_and_security_headers():
    response = handler({"requestContext": {"http": {"method": "GET", "path": "/"}}}, None)
    assert response["statusCode"] == 200
    assert 'maxlength="10"' in response["body"]
    assert "frame-ancestors 'none'" in response["headers"]["content-security-policy"]
    assert response["headers"]["x-content-type-options"] == "nosniff"
    assert response["headers"]["cache-control"] == "no-store"


@pytest.mark.parametrize("code", ["wrong", "", None, 12345, "x" * 11, "tést-code"])
def test_wrong_code_does_not_call_the_runtime(code):
    response = handler(post(code), None, invoke=never)
    assert response["statusCode"] == 400
    assert response["body"] == ERROR
    assert CODE not in response["body"]


def test_unset_code_rejects_everything(monkeypatch):
    monkeypatch.delenv("POLICY_CODE_ADMIN")
    assert handler(post(CODE), None, invoke=never)["body"] == ERROR


def test_code_longer_than_ten_characters_in_the_environment_rejects(monkeypatch):
    monkeypatch.setenv("POLICY_CODE_ADMIN", "x" * 11)
    assert handler(post("x" * 11), None, invoke=never)["statusCode"] == 400


def test_correct_code_calls_the_runtime_with_the_photo():
    seen = {}

    def invoke(image, kind):
        seen.update(image=image, kind=kind)
        return {"status": "ok", "claim_id": "c1"}

    response = handler(post(CODE), None, invoke=invoke)
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["status"] == "ok"
    assert seen == {"image": JPEG, "kind": "image/jpeg"}


def test_photo_over_three_megabytes_is_rejected():
    big = b"\xff\xd8\xff" + b"\x00" * MAX_IMAGE
    assert handler(post(CODE, big), None, invoke=never)["body"] == ERROR


def test_non_image_is_rejected():
    assert handler(post(CODE, b"hello"), None, invoke=never)["body"] == ERROR


@pytest.mark.parametrize("body", ["not json", "[]", json.dumps({"policy": CODE}), json.dumps({"policy": CODE, "image_b64": "%%%"})])
def test_malformed_requests_are_rejected(body):
    event = {"requestContext": {"http": {"method": "POST", "path": "/"}}, "body": body}
    assert handler(event, None, invoke=never)["body"] == ERROR


def test_runtime_failure_returns_the_same_message_without_detail():
    def boom(_image, _kind):
        raise RuntimeError("secret detail")

    response = handler(post(CODE), None, invoke=boom)
    assert response["statusCode"] == 500
    assert response["body"] == ERROR


def test_other_paths_and_methods_are_rejected():
    assert handler({"requestContext": {"http": {"method": "GET", "path": "/admin"}}}, None)["body"] == ERROR
    assert handler({"requestContext": {"http": {"method": "PUT", "path": "/"}}}, None)["body"] == ERROR
