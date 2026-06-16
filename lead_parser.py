"""
Lead parser — uses Claude to decide whether an inbound email is a NEW event or
floral inquiry from a prospective customer, and if so, extract structured lead
fields for the Blooms submit_event_inquiry RPC.

One Claude call per email. Reuses the same anthropic SDK + key/model as agent.py.
Output is forced to strict JSON and parsed defensively — any malformed response
is treated as "not an inquiry" so a bad parse never creates a junk lead.
"""
import json
import logging

import anthropic

import config

log = logging.getLogger("blooms.lead_parser")

# Reuse the same client/key/model the agent uses.
_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

EVENT_TYPES = {"Wedding", "Corporate", "Birthday", "Sympathy", "Other"}

_SYSTEM_PROMPT = (
    "You triage the inbox of a flower shop (Blooms in Bunches). For each email you "
    "decide whether it is a NEW event or floral inquiry from a prospective customer "
    "(wedding, corporate event, party/birthday, sympathy/funeral arrangements, etc.).\n\n"
    "If it is NOT a prospective-customer inquiry — e.g. a vendor invoice, a payment "
    "notice/receipt, a marketing email or newsletter, payroll, an internal/automated "
    "message, a delivery notification, or spam — respond with exactly:\n"
    '{"is_inquiry": false}\n\n'
    "If it IS a genuine inquiry, respond with a JSON object of this exact shape:\n"
    '{"is_inquiry": true, "name": string|null, "email": string|null, '
    '"phone": string|null, "event_type": "Wedding"|"Corporate"|"Birthday"|"Sympathy"|"Other", '
    '"event_date": ISO-8601 date string|null, "guest_count": integer|null, '
    '"budget_min": number|null, "budget_max": number|null, '
    '"message": "a 1-2 sentence summary of what they want"}\n\n'
    "Rules:\n"
    "- Respond with ONLY the JSON object, no prose, no markdown fences.\n"
    "- event_type must be one of the five allowed values; use \"Other\" if unsure.\n"
    "- Use null for any field you cannot determine. Do not invent values.\n"
    "- event_date must be a calendar date in YYYY-MM-DD form, or null.\n"
)


def _coerce_int(value):
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_number(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of Claude's reply, tolerating stray text/fences."""
    if not text:
        return None
    text = text.strip()
    # Strip markdown fences if the model added them despite instructions.
    if text.startswith("```"):
        text = text.strip("`")
        # Drop an optional leading "json" language tag.
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    # Find the outermost object braces.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def classify_email(subject: str, from_header: str, body: str) -> dict:
    """Classify an email and, if it's an inquiry, extract lead fields.

    Returns a dict that always has key "is_inquiry" (bool). When True, the other
    normalized lead fields are present. On any error or malformed model output,
    returns {"is_inquiry": False} so the caller treats it as a non-inquiry.
    """
    user_content = (
        f"From: {from_header or '(unknown)'}\n"
        f"Subject: {subject or '(no subject)'}\n\n"
        f"Body:\n{(body or '').strip()[:8000]}"
    )

    try:
        resp = _client.messages.create(
            model=config.MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.APIError as e:
        log.error(f"Claude classify error: {e}")
        return {"is_inquiry": False}

    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        log.warning(f"Lead parser: unparseable model output: {text[:200]!r}")
        return {"is_inquiry": False}

    if not parsed.get("is_inquiry"):
        return {"is_inquiry": False}

    event_type = parsed.get("event_type")
    if event_type not in EVENT_TYPES:
        event_type = "Other"

    return {
        "is_inquiry": True,
        "name": (parsed.get("name") or None),
        "email": (parsed.get("email") or None),
        "phone": (parsed.get("phone") or None),
        "event_type": event_type,
        "event_date": (parsed.get("event_date") or None),
        "guest_count": _coerce_int(parsed.get("guest_count")),
        "budget_min": _coerce_number(parsed.get("budget_min")),
        "budget_max": _coerce_number(parsed.get("budget_max")),
        "message": (parsed.get("message") or None),
    }
