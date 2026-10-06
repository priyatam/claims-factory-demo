"""Score a claim answer against damage labels.

Location and severity come from the dataset class. False ok is a priced
status of ok when the notes say the photo should not be priced. A dollar
comparison is included when a kept amount is present, and marked synthetic
when that amount was invented.
"""

from __future__ import annotations

import re

from claims.claim import json_from_model

_WITHHOLD = {"not_a_vehicle", "unreadable"}
_SEVERITY = {"minor", "moderate", "severe"}
_LOCATION = {"front": re.compile(r"\bfront\b"), "rear": re.compile(r"\brear\b")}


def as_claim(model_output: str | dict | None) -> dict:
    """Read status, damage, and estimate from model text or a stored claim."""
    if model_output is None:
        return {"status": "unreadable", "damage": None, "estimate": None}
    if isinstance(model_output, dict):
        damage = model_output.get("damage")
        estimate = model_output.get("estimate")
        return {
            "status": model_output.get("status"),
            "damage": damage if isinstance(damage, dict) else None,
            "estimate": estimate if isinstance(estimate, dict) else None,
        }
    try:
        return as_claim(json_from_model(model_output))
    except (ValueError, TypeError):
        return {"status": "unreadable", "damage": None, "estimate": None}


def expected_location(label: dict) -> str | None:
    parts = label.get("parts") or []
    for part in parts:
        if isinstance(part, str) and part.strip().lower() in _LOCATION:
            return part.strip().lower()
    summary = str(label.get("damage_summary") or "").lower()
    for name in ("front", "rear"):
        if name in summary:
            return name
    dataset = str(label.get("dataset_label") or "")
    if dataset.startswith("F_"):
        return "front"
    if dataset.startswith("R_"):
        return "rear"
    location = label.get("location")
    if isinstance(location, str) and location.strip().lower() in _LOCATION:
        return location.strip().lower()
    return None


def _damage_text(claim: dict) -> str:
    damage = claim.get("damage") or {}
    parts = damage.get("parts") if isinstance(damage.get("parts"), list) else []
    summary = damage.get("summary") if isinstance(damage.get("summary"), str) else ""
    words = [summary, *[part for part in parts if isinstance(part, str)]]
    return " ".join(words).lower()


def _money(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def priced_ok(claim: dict) -> bool:
    if claim.get("status") != "ok":
        return False
    estimate = claim.get("estimate")
    if not isinstance(estimate, dict):
        return False
    return _money(estimate.get("low")) is not None and _money(estimate.get("high")) is not None


def should_withhold(label: dict) -> bool:
    if label.get("false_ok_case") is True:
        return True
    return label.get("expected_status") in _WITHHOLD


def location_score(model_output: str | dict | None, label: dict) -> dict:
    expected = expected_location(label)
    claim = as_claim(model_output)
    if expected is None:
        return {"score": 0.0, "test_pass": False, "reason": "No front or rear label to score."}
    text = _damage_text(claim)
    matched = _LOCATION[expected].search(text) is not None
    if matched:
        return {
            "score": 1.0,
            "test_pass": True,
            "reason": f"Damage location matches {expected}.",
        }
    return {
        "score": 0.0,
        "test_pass": False,
        "reason": f"Damage location does not match {expected}.",
    }


def severity_score(model_output: str | dict | None, label: dict) -> dict:
    expected = str(label.get("severity") or "").strip().lower()
    claim = as_claim(model_output)
    damage = claim.get("damage") or {}
    actual = str(damage.get("severity") or "").strip().lower()
    if expected not in _SEVERITY:
        return {"score": 0.0, "test_pass": False, "reason": "No severity label to score."}
    if actual == expected:
        return {"score": 1.0, "test_pass": True, "reason": f"Severity matches {expected}."}
    return {
        "score": 0.0,
        "test_pass": False,
        "reason": f"Severity is {actual or 'missing'}; label is {expected}.",
    }


def false_ok_score(model_output: str | dict | None, label: dict) -> dict:
    claim = as_claim(model_output)
    false_ok = should_withhold(label) and priced_ok(claim)
    if false_ok:
        return {
            "score": 0.0,
            "test_pass": False,
            "false_ok": True,
            "reason": "False ok: status ok with an estimate when the photo should not be priced.",
        }
    if should_withhold(label):
        return {
            "score": 1.0,
            "test_pass": True,
            "false_ok": False,
            "reason": "Estimate withheld when the photo should not be priced.",
        }
    return {
        "score": 1.0,
        "test_pass": True,
        "false_ok": False,
        "reason": "Notes allow a price.",
    }


def kept_inside_agent_range(low: int | float, high: int | float, kept: int | float) -> bool:
    """Architecture check: kept dollars fall inside the agent's low–high."""
    return low <= kept <= high


def synthetic_range_score(model_output: str | dict | None, label: dict) -> dict:
    synthetic = bool(label.get("dollars_synthetic"))
    prefix = "Synthetic dollars. " if synthetic else ""
    kept = _money(label.get("kept_dollars"))
    if kept is None:
        return {
            "score": 0.0,
            "test_pass": True,
            "not_applicable": True,
            "synthetic": synthetic,
            "reason": prefix + "No kept amount on the label.",
        }
    estimate = as_claim(model_output).get("estimate") or {}
    low = _money(estimate.get("low"))
    high = _money(estimate.get("high"))
    if low is None or high is None:
        return {
            "score": 0.0,
            "test_pass": False,
            "synthetic": synthetic,
            "reason": prefix + "No agent range to compare with the kept amount.",
        }
    inside = kept_inside_agent_range(low, high, kept)
    if inside:
        reason = prefix + "Kept amount falls inside the agent low–high."
    else:
        reason = prefix + "Kept amount falls outside the agent low–high."
    return {"score": 1.0 if inside else 0.0, "test_pass": inside, "synthetic": synthetic, "reason": reason}


def score_with_adjuster_label(model_output: str | dict, label: dict) -> dict:
    """Score a stored claim once an adjuster label exists. Does not call a model."""
    return {
        "damage_location": location_score(model_output, label),
        "damage_severity": severity_score(model_output, label),
        "false_ok": false_ok_score(model_output, label),
        "synthetic_range": synthetic_range_score(model_output, label),
    }
