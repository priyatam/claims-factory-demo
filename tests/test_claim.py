import json

from claims.claim import assessment_from_model, json_from_model, redact, run_claim

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


def test_garbage_text_is_unreadable():
    assert run_claim(b"img", "image/jpeg", lambda _image, _kind: "not json", "c1")["status"] == "unreadable"


def test_redact_drops_plate_characters_and_keeps_confidence():
    result = assessment_from_model(OK, "c1")
    hidden = redact(result)
    assert hidden["plate"]["value"] is None
    assert hidden["plate"]["confidence"] == 0.7
    assert result["plate"]["value"] == "ABC123"
