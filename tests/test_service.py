from fastapi.testclient import TestClient
import json
from pathlib import Path

from app import app, store


client = TestClient(app)


def setup_function():
    store.clear()


def post_context(scope, context_id, payload, version=1):
    return client.post("/v1/context", json={"scope": scope, "context_id": context_id, "version": version, "payload": payload, "delivered_at": "2026-04-26T10:00:00Z"})


def load_base(customer=False):
    post_context("category", "restaurants", {"slug": "restaurants", "seasonal_beats": [], "digest": []})
    post_context("merchant", "m1", {"merchant_id": "m1", "category_slug": "restaurants", "identity": {"name": "Pizza Works", "owner_first_name": "Suresh"}, "performance": {"delta_7d": {"views_pct": -0.2}}, "offers": [{"title": "BOGO Pizza", "status": "active"}]})
    if customer:
        post_context("customer", "c1", {"customer_id": "c1", "merchant_id": "m1", "identity": {"name": "Asha", "language_pref": "en"}, "relationship": {"services_received": ["dinner"]}, "consent": {"scope": ["promotional_offers"]}, "state": "lapsed_soft"})


def test_health_and_versioned_context():
    assert client.get("/v1/healthz").json()["status"] == "ok"
    assert post_context("category", "restaurants", {"slug": "restaurants"}, 2).json()["accepted"] is True
    stale = post_context("category", "restaurants", {"slug": "restaurants"}, 2).json()
    assert stale["reason"] == "stale_version"


def test_tick_creates_one_grounded_action_and_suppresses_repeat():
    load_base()
    post_context("trigger", "t1", {"id": "t1", "scope": "merchant", "kind": "perf_dip", "merchant_id": "m1", "payload": {"metric": "views", "delta_pct": -0.2}, "suppression_key": "dip:m1"})
    first = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["t1"]}).json()
    assert len(first["actions"]) == 1
    assert "20%" in first["actions"][0]["body"]
    assert client.post("/v1/tick", json={"now": "2026-04-26T10:05:00Z", "available_triggers": ["t1"]}).json() == {"actions": []}


def test_nonconsented_customer_is_suppressed():
    load_base(customer=True)
    post_context("customer", "c1", {"customer_id": "c1", "merchant_id": "m1", "identity": {"name": "Asha"}, "consent": {"scope": []}, "state": "lapsed_soft"}, 2)
    post_context("trigger", "t2", {"id": "t2", "scope": "customer", "kind": "recall_due", "merchant_id": "m1", "customer_id": "c1", "payload": {}, "suppression_key": "recall:c1"})
    assert client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["t2"]}).json() == {"actions": []}


def test_reply_safety_and_intent_transition():
    hostile = client.post("/v1/reply", json={"conversation_id": "h", "from_role": "merchant", "message": "Stop messaging me. This is spam.", "turn_number": 2}).json()
    assert hostile["action"] == "end"
    intent = client.post("/v1/reply", json={"conversation_id": "i", "from_role": "merchant", "message": "Ok lets do it. Whats next?", "turn_number": 2}).json()
    assert intent["action"] == "send"


def test_generated_challenge_data_produces_valid_actions():
    """Exercise the same expanded context shapes that the official harness pushes."""
    expanded = Path(__file__).parents[1] / "expanded"
    if not expanded.exists():
        return
    for path in (expanded / "categories").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        post_context("category", payload["slug"], payload)
    for path in (expanded / "merchants").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        post_context("merchant", payload["merchant_id"], payload)
    for path in (expanded / "customers").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        post_context("customer", payload["customer_id"], payload)
    trigger_ids = []
    for path in (expanded / "triggers").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        trigger_ids.append(payload["id"])
        post_context("trigger", payload["id"], payload)

    result = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": trigger_ids}).json()
    assert 0 < len(result["actions"]) <= 20
    for action in result["actions"]:
        assert action["body"] and action["cta"] and action["suppression_key"]
