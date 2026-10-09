"""Shared policy code for the public upload page. Fails closed."""

from __future__ import annotations

import hmac
import os

MAX_LEN = 10


def policy_ok(submitted: object) -> bool:
    """True only when the submitted code equals POLICY_CODE_ADMIN. Never logs either value."""
    expected = os.environ.get("POLICY_CODE_ADMIN", "")
    if not expected or len(expected) > MAX_LEN:
        return False
    if not isinstance(submitted, str) or not submitted or len(submitted) > MAX_LEN:
        return False
    return hmac.compare_digest(submitted.encode(), expected.encode())
