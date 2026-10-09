import ipaddress
import json
import re
import socket
from typing import Callable, TypedDict
from urllib.parse import urlparse


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
    confidence: float | None


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
    # A missing confidence stays null; it must not turn a complete estimate into an unreadable claim.
    if None in (low, high, currency) or not assumptions or low > high:
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


# --- Photo intake: image type, public-URL check, and fetch ---

MAX_BYTES = 5 * 1024 * 1024

_BLOCKED = {"localhost", "metadata.google.internal"}


def media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def is_public_ip(raw: str) -> bool:
    ip = ipaddress.ip_address(raw)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def public_url(url: str, resolve: Callable = socket.getaddrinfo) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED or host.endswith(".local"):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            infos = resolve(host, port)
        except OSError:
            return False
        addresses = [info[4][0] for info in infos]
        return bool(addresses) and all(map(is_public_ip, addresses))
    return is_public_ip(host)


def fetch_image(
    url: str,
    *,
    get: Callable | None = None,
    resolve: Callable = socket.getaddrinfo,
    limit: int = MAX_BYTES,
) -> dict | None:
    if not public_url(url, resolve=resolve):
        return None
    getter = get or _get
    try:
        response = getter(url, timeout=10.0, follow_redirects=False)
    except Exception:
        return None
    final = str(getattr(response, "url", url))
    if final != url and not public_url(final, resolve=resolve):
        return None
    if getattr(response, "status_code", 0) != 200:
        return None
    data = getattr(response, "content", b"")
    if not isinstance(data, (bytes, bytearray)) or len(data) > limit:
        return None
    kind = media_type(bytes(data))
    if kind is None:
        return None
    return {"data": bytes(data), "media_type": kind}


def _get(url: str, **kwargs):
    import httpx  # imported here so the Lambda that bundles this file needs only the standard library

    return httpx.get(url, **kwargs)
