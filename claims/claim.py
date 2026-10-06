import json
import re
from typing import Callable, TypedDict


class Vehicle(TypedDict):
    make: str | None
    model: str | None
    colour: str | None
    confidence: float | None


class Plate(TypedDict):
    value: str | None
    confidence: float | None


class Damage(TypedDict):
    summary: str
    parts: list[str]
    severity: str


class Estimate(TypedDict):
    low: int
    high: int
    currency: str
    assumptions: list[str]
    confidence: float


class ClaimResult(TypedDict):
    claim_id: str
    status: str
    vehicle: Vehicle | None
    plate: Plate
    damage: Damage | None
    estimate: Estimate | None


empty_plate = lambda: {"value": None, "confidence": None}
strings = lambda value: (
    [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, list)
    else []
)


def empty_result(claim_id: str, status: str) -> ClaimResult:
    return {
        "claim_id": claim_id,
        "status": status,
        "vehicle": None,
        "plate": empty_plate(),
        "damage": None,
        "estimate": None,
    }


def json_from_model(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    # Tool-using agents often put a sentence before the JSON; take the first object.
    start = stripped.find("{")
    if start < 0:
        raise ValueError("object required")
    payload, _ = json.JSONDecoder().raw_decode(stripped[start:])
    if not isinstance(payload, dict):
        raise ValueError("object required")
    return payload


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _confidence(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _vehicle(raw: object) -> Vehicle | None:
    if not isinstance(raw, dict):
        return None
    colour = raw.get("colour", raw.get("color"))
    return {
        "make": _text(raw.get("make")),
        "model": _text(raw.get("model")),
        "colour": _text(colour),
        "confidence": _confidence(raw.get("confidence")),
    }


def _plate(raw: object) -> Plate:
    if not isinstance(raw, dict):
        return empty_plate()
    return {"value": _text(raw.get("value")), "confidence": _confidence(raw.get("confidence"))}


def _damage(raw: object) -> Damage | None:
    if not isinstance(raw, dict):
        return None
    summary = _text(raw.get("summary"))
    severity = _text(raw.get("severity"))
    if summary is None or severity is None:
        return None
    return {"summary": summary, "parts": strings(raw.get("parts")), "severity": severity}


def _money(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _estimate(raw: object) -> Estimate | None:
    if not isinstance(raw, dict):
        return None
    low = _money(raw.get("low"))
    high = _money(raw.get("high"))
    currency = _text(raw.get("currency"))
    assumptions = strings(raw.get("assumptions"))
    confidence = _confidence(raw.get("confidence"))
    if None in (low, high, currency, confidence) or not assumptions or low > high:
        return None
    return {
        "low": low,
        "high": high,
        "currency": currency,
        "assumptions": assumptions,
        "confidence": confidence,
    }


def assessment_from_model(payload: dict, claim_id: str) -> ClaimResult:
    plate = _plate(payload.get("plate"))
    vehicle = _vehicle(payload.get("vehicle"))
    if payload.get("status") == "not_a_vehicle":
        return {
            "claim_id": claim_id,
            "status": "not_a_vehicle",
            "vehicle": vehicle,
            "plate": plate,
            "damage": None,
            "estimate": None,
        }
    damage = _damage(payload.get("damage"))
    estimate = _estimate(payload.get("estimate"))
    if payload.get("status") != "ok" or damage is None or estimate is None:
        return empty_result(claim_id, "unreadable")
    return {
        "claim_id": claim_id,
        "status": "ok",
        "vehicle": vehicle,
        "plate": plate,
        "damage": damage,
        "estimate": estimate,
    }


def redact(result: ClaimResult) -> ClaimResult:
    return {**result, "plate": {**result["plate"], "value": None}}


def run_claim(image: bytes, media_type: str, assess: Callable[[bytes, str], str], claim_id: str) -> ClaimResult:
    try:
        payload = json_from_model(assess(image, media_type))
    except (ValueError, TypeError):
        return empty_result(claim_id, "unreadable")
    return assessment_from_model(payload, claim_id)
