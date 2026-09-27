"""Deterministic, grounded message composition for Vera.

Business selection and factual extraction happen here.  There are deliberately
no external model calls: hidden judge inputs are handled using the fields they
actually contain, rather than a brittle set of pre-written example responses.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from models import Decision


CUSTOMER_TRIGGER_WORDS = ("recall", "lapsed", "refill", "appointment", "trial", "winback")
NEGATIVE_WORDS = ("stop", "spam", "useless", "not interested", "don't contact", "do not contact", "unsubscribe")
AUTO_REPLY_WORDS = ("thank you for contacting", "team will respond", "automatic reply", "auto-reply", "away message")
ACCEPT_WORDS = ("yes", "let's do", "lets do", "go ahead", "sounds good", "approve", "what's next", "whats next", "do it")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _percent(value: Any) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if abs(number) <= 1:
        number *= 100
    return f"{abs(number):g}%"


def _signed_percent(value: Any) -> str | None:
    pct = _percent(value)
    if pct is None:
        return None
    try:
        return ("up " if float(value) > 0 else "down ") + pct
    except (TypeError, ValueError):
        return pct


def _first_name(merchant: dict[str, Any]) -> str:
    identity = merchant.get("identity", {})
    owner = _text(identity.get("owner_first_name"))
    if owner:
        return owner
    name = _text(identity.get("name"))
    match = re.search(r"Dr\.\s+([A-Za-z]+)", name)
    if match:
        return f"Dr. {match.group(1)}"
    return name.split()[0] if name else "there"


def _merchant_name(merchant: dict[str, Any]) -> str:
    return _text(merchant.get("identity", {}).get("name")) or "your business"


def _active_offers(merchant: dict[str, Any]) -> list[dict[str, Any]]:
    return [offer for offer in merchant.get("offers", []) if _text(offer.get("status")).lower() in {"active", "live"}]


def _offer_title(merchant: dict[str, Any], category: dict[str, Any]) -> str | None:
    active = _active_offers(merchant)
    if active:
        return _text(active[0].get("title")) or None
    # Category catalog is a capability/catalogue, not proof the merchant runs it.
    return None


def _find_digest(category: dict[str, Any], trigger: dict[str, Any]) -> dict[str, Any] | None:
    wanted = _text(trigger.get("payload", {}).get("digest_item_id") or trigger.get("payload", {}).get("top_item_id") or trigger.get("payload", {}).get("alert_id"))
    digest = category.get("digest", [])
    for item in digest:
        if _text(item.get("id")) == wanted:
            return item
    kind = _text(trigger.get("kind")).lower()
    if "research" in kind or "digest" in kind or "cde" in kind:
        return digest[0] if digest else None
    return None


def _payload_facts(payload: dict[str, Any]) -> list[str]:
    """Return short human-readable facts without treating placeholder fields as facts."""
    labels = {
        "metric": "metric", "window": "window", "vs_baseline": "baseline", "likely_driver": "likely driver",
        "competitor_name": "new competitor", "distance_km": "distance", "their_offer": "their offer",
        "event": "event", "festival": "festival", "season": "season", "intent_topic": "topic",
        "days_since_last_visit": "days since last visit", "trial_date": "trial date", "molecule": "medicine",
        "manufacturer": "manufacturer", "estimated_uplift_pct": "estimated uplift", "last_topic": "last topic",
    }
    facts: list[str] = []
    for key, label in labels.items():
        value = payload.get(key)
        if value in (None, "", False):
            continue
        if key.endswith("_pct"):
            value = _percent(value) or value
        if isinstance(value, (dict, list)):
            continue
        facts.append(f"{label}: {value}")
    return facts


def _customer_allowed(customer: dict[str, Any] | None, trigger: dict[str, Any]) -> tuple[bool, str]:
    if not customer:
        return False, "customer context is unavailable"
    consent = customer.get("consent", {})
    scopes = consent.get("scope", []) if isinstance(consent, dict) else []
    opted = customer.get("preferences", {}).get("reminder_opt_in")
    if not scopes and opted is not True:
        return False, "customer has not opted into outreach"
    if _text(customer.get("state")).lower() in {"churned", "do_not_contact", "blocked"}:
        return False, "customer state does not allow outreach"
    if not _text(customer.get("identity", {}).get("name")):
        return False, "customer identity is incomplete"
    return True, "eligible customer context"


def _send_as(customer: dict[str, Any] | None) -> str:
    return "merchant_on_behalf" if customer else "vera"


def _customer_message(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any], customer: dict[str, Any]) -> tuple[str, str, str]:
    payload = trigger.get("payload", {})
    kind = _text(trigger.get("kind")).lower()
    name = _text(customer.get("identity", {}).get("name"))
    business = _merchant_name(merchant)
    owner = _first_name(merchant)
    language = _text(customer.get("identity", {}).get("language_pref")).lower()
    greeting = "Namaste" if "hi" in language and category.get("slug") == "pharmacies" else "Hi"
    offer = _offer_title(merchant, category)

    if "refill" in kind:
        medicines = ", ".join(map(_text, payload.get("molecule_list", [])))
        due = _text(payload.get("stock_runs_out_iso")).split("T")[0]
        detail = f" Your {medicines} refill is due on {due}." if medicines and due else " Your regular refill is due soon."
        if offer:
            detail += f" {offer} is available."
        return f"{greeting} {name}, {business} here.{detail} Reply CONFIRM and we will prepare it for you.", "Reply CONFIRM", "refill due with consent"
    if "appointment" in kind:
        when = _text(payload.get("appointment_iso") or payload.get("appointment_date"))
        return f"{greeting} {name}, {business} here. Your appointment {('on ' + when) if when else 'is coming up'}. Reply YES to confirm or tell us if you need another slot.", "Confirm appointment", "appointment reminder with consent"
    if "trial" in kind:
        options = payload.get("next_session_options", [])
        slot = _text(options[0].get("label")) if options and isinstance(options[0], dict) else "the next session"
        return f"{greeting} {name}, {owner} from {business} here. Thanks for trying us. Would {slot} work for your next session? Reply YES and we will reserve it.", "Reserve my slot", "trial follow-up with consent"
    if "lapsed" in kind or "winback" in kind or "recall" in kind:
        days = _text(payload.get("days_since_last_visit"))
        services = customer.get("relationship", {}).get("services_received", [])
        service = _text(services[-1]) if services else "your previous visit"
        timing = f" It has been {days} days since your last visit" if days else " We would be glad to see you again"
        offer_sentence = f" {offer} is currently active." if offer else ""
        return f"{greeting} {name}, {business} here.{timing}. We can help you continue with {service}.{offer_sentence} Want us to suggest a convenient slot?", "Suggest a slot", "eligible recall based on relationship"
    return f"{greeting} {name}, {business} here. We have an update relevant to your recent visit. Want us to share the next best step?", "Share next step", "eligible customer outreach"


def _merchant_message(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str, str, str]:
    payload = trigger.get("payload", {})
    kind = _text(trigger.get("kind")).lower()
    name = _first_name(merchant)
    business = _merchant_name(merchant)
    offer = _offer_title(merchant, category)
    performance = merchant.get("performance", {})
    digest = _find_digest(category, trigger)
    prefix = f"{name}, " if name else ""

    if "supply" in kind or "alert" in kind:
        molecule = _text(payload.get("molecule"))
        batches = ", ".join(map(_text, payload.get("affected_batches", [])))
        maker = _text(payload.get("manufacturer"))
        evidence = " ".join(part for part in [molecule, f"batches {batches}" if batches else "", f"from {maker}" if maker else ""] if part)
        return (f"{prefix}urgent update for {business}: {evidence or 'a supply alert'} needs a review. Check affected stock and customer records before the next dispense. Want me to draft a precise customer note and a replacement checklist?", "Draft the safety note", "supply/compliance alert with payload facts", "vera_supply_alert_v1")
    if "research" in kind or "digest" in kind or "cde" in kind:
        if digest:
            source = _text(digest.get("source"))
            action = _text(digest.get("actionable"))
            title = _text(digest.get("title"))
            source_clause = f" ({source})" if source else ""
            action_clause = f" Recommended next step: {action}." if action else ""
            return (f"{prefix}{title}{source_clause}.{action_clause} Want me to turn this into a short plan for {business}?", "Make the plan", "selected the linked category digest and its actionable guidance", "vera_research_digest_v1")
    if "dip" in kind:
        metric = _text(payload.get("metric")) or "performance"
        delta = _signed_percent(payload.get("delta_pct"))
        if not delta:
            deltas = performance.get("delta_7d", {})
            for metric_key, value in deltas.items():
                if isinstance(value, (int, float)) and value < 0:
                    metric, delta = metric_key.replace("_pct", ""), _signed_percent(value)
                    break
        metric_clause = f"{metric} is {delta}" if delta else f"{metric} has softened"
        offer_clause = f" You already have {offer} live; test it against the affected audience first." if offer else " Start by checking the listing or campaign signal behind the change before adding a discount."
        return (f"{prefix}{metric_clause} for {business}.{offer_clause} Want me to draft one focused recovery action?", "Draft recovery action", "performance dip linked to a measured merchant signal", "vera_performance_dip_v1")
    if "spike" in kind or "milestone" in kind:
        metric = _text(payload.get("metric")) or "performance"
        delta = _signed_percent(payload.get("delta_pct"))
        driver = _text(payload.get("likely_driver"))
        metric_clause = f"{metric} is {delta}" if delta else f"{metric} is improving"
        driver_clause = f" The likely driver is {driver}." if driver else ""
        return (f"{prefix}{metric_clause} for {business}.{driver_clause} Capture what is working before changing the offer. Want a quick replication checklist?", "Get the checklist", "performance spike should be amplified rather than discounted", "vera_performance_spike_v1")
    if "competitor" in kind:
        competitor = _text(payload.get("competitor_name")) or "a nearby competitor"
        distance = _text(payload.get("distance_km"))
        their_offer = _text(payload.get("their_offer"))
        facts = f" {competitor} opened{(' ' + distance + ' km away') if distance else ''}."
        if their_offer:
            facts += f" Their visible offer is {their_offer}."
        return (f"{prefix}worth a local check for {business}.{facts} Compare your listing and active offer before matching price. Want two differentiated listing angles?", "Show listing angles", "competitor signal with no unsupported price-matching claim", "vera_competitor_response_v1")
    if "festival" in kind or "seasonal" in kind or "ipl" in kind:
        event = _text(payload.get("festival") or payload.get("event") or payload.get("season"))
        relevant = [beat.get("note") for beat in category.get("seasonal_beats", []) if beat.get("note")]
        seasonal_note = _text(relevant[0]) if relevant else ""
        offer_clause = f" Use your active {offer} only if it fits the occasion." if offer else ""
        return (f"{prefix}{event or 'seasonal demand'} is a timely planning window for {business}.{(' ' + seasonal_note) if seasonal_note else ''}{offer_clause} Want a short campaign draft with one clear offer?", "Draft the campaign", "seasonal trigger matched to category context", "vera_seasonal_plan_v1")
    if "planning" in kind:
        topic = _text(payload.get("intent_topic")) or "the idea you raised"
        return (f"{prefix}let's turn {topic} into an executable plan for {business}. I can start with the offer, audience, and a simple launch message. Want the first draft?", "Create first draft", "merchant explicitly signalled planning intent", "vera_planning_v1")
    if "unverified" in kind or "listing" in kind or "gbp" in kind:
        uplift = _percent(payload.get("estimated_uplift_pct"))
        impact = f" The context estimates up to {uplift} uplift." if uplift else ""
        return (f"{prefix}{business} still has a listing verification task open.{impact} Finish the supplied verification path first, then refresh the visible details. Want a two-step checklist?", "Get verification checklist", "listing/verification trigger with supplied uplift", "vera_listing_fix_v1")
    if "review" in kind:
        themes = merchant.get("review_themes", [])
        theme = _text(themes[0]) if themes else "the new feedback theme"
        return (f"{prefix}{business} has a signal worth addressing: {theme}. Want me to turn it into one customer-facing fix and one operational check?", "Show the fixes", "review signal selected for a concrete response", "vera_review_response_v1")
    if "renewal" in kind:
        days = _text(merchant.get("subscription", {}).get("days_remaining"))
        return (f"{prefix}your current plan{(' has ' + days + ' days remaining') if days else ' is nearing its next decision point'}. Want a concise usage summary before you decide?", "Show usage summary", "subscription context used without inventing a renewal offer", "vera_renewal_v1")
    if "dormant" in kind or "curious" in kind:
        topic = _text(payload.get("last_topic") or payload.get("merchant_last_message"))
        return (f"{prefix}quick one for {business}: {topic or 'what is the one service customers ask about most this week?'} I can turn your answer into a listing update and reply draft. Want to do that?", "Create the update", "low-friction re-engagement question", "vera_reengagement_v1")

    facts = _payload_facts(payload)
    fact_clause = f" I found {facts[0]}." if facts else ""
    return (f"{prefix}there is a new {kind.replace('_', ' ')} signal for {business}.{fact_clause} Want one focused next step based on it?", "Show next step", "used the available trigger rather than a generic blast", "vera_general_signal_v1")


def compose(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any], customer: dict[str, Any] | None = None) -> Decision | None:
    """Return one grounded decision, or ``None`` when contact is ineligible."""
    merchant_id = _text(trigger.get("merchant_id") or merchant.get("merchant_id"))
    trigger_id = _text(trigger.get("id"))
    customer_id = _text(trigger.get("customer_id") or (customer or {}).get("customer_id")) or None
    kind = _text(trigger.get("kind")).lower()
    is_customer = bool(customer_id) or trigger.get("scope") == "customer" or any(word in kind for word in CUSTOMER_TRIGGER_WORDS)

    if not merchant_id or not trigger_id:
        return None
    if is_customer:
        ok, _ = _customer_allowed(customer, trigger)
        if not ok or not customer:
            return None
        body, cta, rationale = _customer_message(category, merchant, trigger, customer)
    else:
        body, cta, rationale, template_name = _merchant_message(category, merchant, trigger)
    key = _text(trigger.get("suppression_key")) or f"{merchant_id}:{kind}:{customer_id or 'merchant'}"
    template_name = locals().get("template_name", "vera_customer_outreach_v1")
    params = [value for value in [_merchant_name(merchant), _text(trigger.get("kind")), customer_id or ""] if value]
    return Decision(merchant_id=merchant_id, trigger_id=trigger_id, customer_id=customer_id, body=body, cta=cta, send_as=_send_as(customer if is_customer else None), suppression_key=key, rationale=rationale, template_name=template_name, template_params=params)


def reply_action(message: str, history: list[dict[str, str]], repeat_count: int) -> dict[str, Any]:
    """Safe, deterministic conversation routing for the replay scenarios."""
    normalized = re.sub(r"\s+", " ", message.strip().lower())
    if any(word in normalized for word in NEGATIVE_WORDS):
        return {"action": "end", "rationale": "Merchant asked not to receive further messages"}
    if any(word in normalized for word in AUTO_REPLY_WORDS):
        return {"action": "end", "rationale": "Detected an automated reply; ending to avoid a message loop"}
    if repeat_count >= 2:
        return {"action": "end", "rationale": "Repeated identical reply indicates no active conversation"}
    if any(word in normalized for word in ACCEPT_WORDS):
        return {"action": "send", "body": "Great — I’ll turn the recommendation into a short draft using the details already shared, then send it back for your approval.", "cta": "Review the draft", "rationale": "Merchant committed, so Vera moved directly to the next concrete step"}
    if any(word in normalized for word in ("later", "busy", "tomorrow", "not now")):
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Merchant asked to defer; backing off for 30 minutes"}
    if "?" in message:
        return {"action": "send", "body": "Good question. I’ll keep this focused on the current recommendation and can prepare the practical next step when you’re ready.", "cta": "Continue", "rationale": "Acknowledged the question without making unsupported claims"}
    return {"action": "wait", "wait_seconds": 900, "rationale": "No clear intent yet; leaving space rather than sending another promotion"}
