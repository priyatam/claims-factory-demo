"""Partner fact tools. Stubs until Gateway MCP is wired — null stays null."""

from strands import tool


@tool
def fetch_policy(notice_vehicle: str = "") -> dict:
    """Policy facts for the vehicle named on the notice."""
    return {"source": "policy", "notice_vehicle": notice_vehicle or None, "facts": None}


@tool
def fetch_loss_history(plate: str = "", vin: str = "") -> dict:
    """Loss-history facts when a plate or VIN is known."""
    return {"source": "loss_history", "plate": plate or None, "vin": vin or None, "facts": None}


@tool
def fetch_estimating(damage_summary: str = "", region: str = "US") -> dict:
    """Estimating / parts-and-labor facts for a damage summary."""
    return {
        "source": "estimating",
        "damage_summary": damage_summary or None,
        "region": region,
        "facts": None,
    }


PARTNER_TOOLS = [fetch_policy, fetch_loss_history, fetch_estimating]
