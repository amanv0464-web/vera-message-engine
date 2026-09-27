"""Small, explicit data structures shared by the Vera HTTP service."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class Decision:
    """A fully grounded proactive action before it is converted to API JSON."""

    merchant_id: str
    trigger_id: str
    customer_id: str | None
    body: str
    cta: str
    send_as: str
    suppression_key: str
    rationale: str
    template_name: str
    template_params: list[str]

    def action(self, conversation_id: str) -> dict[str, Any]:
        result = asdict(self)
        result["conversation_id"] = conversation_id
        return result
