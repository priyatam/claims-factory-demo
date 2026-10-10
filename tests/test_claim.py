import json
from types import SimpleNamespace

from claims.claim import (
    assessment_from_model,
    fetch_image,
    json_from_model,
    media_type,
    public_url,
)

OK = {
    "status": "ok",
    "vehicle": {"make": "Honda", "model": "Civic", "colour": "blue", "confidence": 0.8},
    "plate": {"value": "ABC123", "confidence": 0.7},
    "damage": {
        "summary": "left rear bumper dent with scratching",
        "parts": ["bumper"],
        "severity": "moderate",
    },
    "estimate": {
        "low": 400,
        "high": 900,
        "currency": "USD",
        "assumptions": ["paint blend unknown", "labor region unknown"],
        "confidence": 0.4,
    },
}


def test_parses_an_ok_assessment():
    result = assessment_from_model(OK, "c1")
    assert result["claim_id"] == "c1"
    assert result["status"] == "ok"
    assert result["vehicle"]["make"] == "Honda"
    assert result["vehicle"]["colour"] == "blue"
    assert result["damage"]["parts"] == ["bumper"]
    assert result["estimate"]["low"] == 400
    assert result["estimate"]["high"] == 900


def test_accepts_american_color_spelling():
    payload = {**OK, "vehicle": {"make": "Honda", "model": "Civic", "color": "blue", "confidence": 0.5}}
    assert assessment_from_model(payload, "c1")["vehicle"]["colour"] == "blue"


def test_not_a_vehicle_has_no_estimate():
    result = assessment_from_model({**OK, "status": "not_a_vehicle"}, "c1")
    assert result["status"] == "not_a_vehicle"
    assert result["damage"] is None
    assert result["estimate"] is None


def test_inverted_cost_range_is_unreadable():
    estimate = {**OK["estimate"], "low": 900, "high": 400}
    result = assessment_from_model({**OK, "estimate": estimate}, "c1")
    assert result["status"] == "unreadable"
    assert result["estimate"] is None


def test_ok_without_assumptions_is_unreadable():
    estimate = {**OK["estimate"], "assumptions": []}
    assert assessment_from_model({**OK, "estimate": estimate}, "c1")["status"] == "unreadable"


def test_missing_plate_is_null():
    result = assessment_from_model({**OK, "plate": None}, "c1")
    assert result["plate"]["value"] is None
    assert result["plate"]["confidence"] is None


def test_json_after_prose():
    text = "Partner tools returned null facts.\n\n" + json.dumps(OK) + "\n"
    assert json_from_model(text) == OK


def test_fenced_json():
    text = "```json\n" + json.dumps({"status": "not_a_vehicle"}) + "\n```"
    result = assessment_from_model(json_from_model(text), "c1")
    assert result["status"] == "not_a_vehicle"


# --- Photo intake ---

JPEG = b"\xff\xd8\xff" + b"\x00" * 8
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def resolve(host, port):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def test_media_type_sniffs_magic_bytes():
    assert media_type(JPEG) == "image/jpeg"
    assert media_type(PNG) == "image/png"
    assert media_type(b"GIF89a") is None
    assert media_type(b"") is None


def test_public_url_rejects_private_and_non_http():
    assert public_url("https://example.com/car.jpg", resolve=resolve)
    assert not public_url("https://example.com/car.jpg", resolve=lambda _h, port: [(2, 1, 6, "", ("10.0.0.5", port))])
    assert not public_url("http://127.0.0.1/a.jpg", resolve=resolve)
    assert not public_url("http://169.254.169.254/latest", resolve=lambda _h, port: [(2, 1, 6, "", ("169.254.169.254", port))])
    assert not public_url("file:///etc/passwd", resolve=resolve)


def test_fetch_returns_bytes_for_a_public_image():
    def get(url, **_kwargs):
        return SimpleNamespace(status_code=200, content=JPEG, url=url)

    loaded = fetch_image("https://example.com/car.jpg", get=get, resolve=resolve)
    assert loaded["media_type"] == "image/jpeg"
    assert loaded["data"].startswith(b"\xff\xd8\xff")


def test_fetch_rejects_oversized_and_non_images():
    def get(url, **_kwargs):
        return SimpleNamespace(status_code=200, content=JPEG, url=url)

    assert fetch_image("https://example.com/car.jpg", get=get, resolve=resolve, limit=4) is None

    def text(url, **_kwargs):
        return SimpleNamespace(status_code=200, content=b"hello", url=url)

    assert fetch_image("https://example.com/a.txt", get=text, resolve=resolve) is None


def test_estimate_without_a_confidence_is_still_a_priced_claim():
    estimate = {k: v for k, v in OK["estimate"].items() if k != "confidence"}
    result = assessment_from_model({**OK, "estimate": estimate}, "c1")
    assert result["status"] == "ok"
    assert result["estimate"]["confidence"] is None
    assert result["estimate"]["low"] == OK["estimate"]["low"]


def test_estimate_still_needs_a_range_currency_and_assumptions():
    for missing in ("low", "high", "currency", "assumptions"):
        estimate = {k: v for k, v in OK["estimate"].items() if k != missing}
        assert assessment_from_model({**OK, "estimate": estimate}, "c1")["status"] == "unreadable"
