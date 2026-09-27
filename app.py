"""FastAPI service exposed to the magicpin Vera judge."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from context_store import ContextStore, VALID_SCOPES
from engine import compose, reply_action


app = FastAPI(title="Vera Message Engine", version="1.0.0")
store = ContextStore()
STARTED_AT = time.monotonic()


class ContextRequest(BaseModel):
    scope: str
    context_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    payload: dict[str, Any]
    delivered_at: str


class TickRequest(BaseModel):
    now: str
    available_triggers: list[str] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str
    message: str
    received_at: str | None = None
    turn_number: int = Field(default=1, ge=1)


@app.get("/v1/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "uptime_seconds": int(time.monotonic() - STARTED_AT), "contexts_loaded": store.counts()}


@app.get("/v1/metadata")
def metadata() -> dict[str, Any]:
    return {
        "team_name": os.getenv("VERA_TEAM_NAME", "Vera Builder"),
        "team_members": [part.strip() for part in os.getenv("VERA_TEAM_MEMBERS", "").split(",") if part.strip()],
        "model": "deterministic-rules",
        "approach": "deterministic context-grounded decision engine with category rules and suppression",
        "contact_email": os.getenv("VERA_CONTACT_EMAIL", ""),
        "version": "1.0.0",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/v1/context")
def push_context(request: ContextRequest) -> dict[str, Any]:
    if request.scope not in VALID_SCOPES:
        raise HTTPException(status_code=400, detail={"accepted": False, "reason": "invalid_scope", "details": "scope must be category, merchant, customer, or trigger"})
    accepted, current_version = store.put(request.scope, request.context_id, request.version, request.payload)
    if not accepted:
        return {"accepted": False, "reason": "stale_version", "current_version": current_version}
    return {"accepted": True, "ack_id": f"ack_{request.scope}_{request.context_id}_v{request.version}", "stored_at": datetime.now(timezone.utc).isoformat()}


@app.post("/v1/tick")
def tick(request: TickRequest) -> dict[str, list[dict[str, Any]]]:
    actions: list[dict[str, Any]] = []
    for trigger_id in request.available_triggers[:20]:
        trigger = store.get("trigger", trigger_id)
        if not trigger:
            continue
        merchant = store.get("merchant", trigger.get("merchant_id"))
        if not merchant:
            continue
        category = store.get("category", merchant.get("category_slug"))
        if not category:
            continue
        customer = store.get("customer", trigger.get("customer_id")) if trigger.get("customer_id") else None
        decision = compose(category, merchant, trigger, customer)
        if not decision or not store.claim_suppression(decision.suppression_key):
            continue
        safe_trigger = re.sub(r"[^a-zA-Z0-9_-]", "_", decision.trigger_id)
        actions.append(decision.action(f"conv_{safe_trigger}"))
    return {"actions": actions}


@app.post("/v1/reply")
def reply(request: ReplyRequest) -> dict[str, Any]:
    history = store.add_turn(request.conversation_id, request.from_role, request.message)
    normalized = re.sub(r"\s+", " ", request.message.strip().lower())
    repeat_count = store.count_reply(normalized)
    return reply_action(request.message, history, repeat_count)


@app.post("/v1/teardown")
def teardown() -> dict[str, bool]:
    """Optional lifecycle endpoint from the testing brief; removes synthetic data."""
    store.clear()
    return {"cleared": True}
