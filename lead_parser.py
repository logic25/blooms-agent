"""
Lead parser — uses Claude to triage an inbound email into one of three kinds:
  - "inquiry": a NEW event/floral inquiry from a prospective customer -> lead
  - "invoice": a vendor/supplier bill or invoice -> recorded for owner review
  - "other":   anything else -> skipped

One Claude call per email. Reuses the same anthropic SDK + key/model as agent.py.
For invoices, any PDF attachments are passed to Claude as document content blocks
so the structured fields (vendor, number, amount, line items) come straight from
the PDF — which is where the real billing data lives.

Output is forced to strict JSON and parsed defensively — any malformed response
is treated as kind "other" so a bad parse never creates a junk lead or invoice.
"""
import base64
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
    "classify it into exactly one of three kinds:\n"
    '  - "inquiry": a NEW event or floral inquiry from a prospective customer '
    "(wedding, corporate event, party/birthday, sympathy/funeral arrangements, etc.).\n"
    '  - "invoice": a vendor/supplier BILL or INVOICE addressed to the shop — e.g. a '
    "flower wholesaler such as Prime Petals or J. Merullo Imports billing the shop for "
    "stems/supplies. These typically have an invoice number, an amount due/total, and "
    "often a PDF attachment with the real billing detail.\n"
    '  - "other": anything else — payment receipts the shop already paid, marketing '
    "emails or newsletters, payroll, internal/automated messages, delivery "
    "notifications, or spam.\n\n"
    "If an email is BOTH a bill and has a PDF, prefer \"invoice\". If a PDF is "
    "attached, treat the PDF as the source of truth for the invoice fields.\n\n"
    "Respond with a single JSON object whose shape depends on the kind.\n\n"
    'For an inquiry:\n'
    '{"kind": "inquiry", "name": string|null, "email": string|null, '
    '"phone": string|null, "event_type": "Wedding"|"Corporate"|"Birthday"|"Sympathy"|"Other", '
    '"event_date": ISO-8601 date string|null, "guest_count": integer|null, '
    '"budget_min": number|null, "budget_max": number|null, '
    '"message": "a 1-2 sentence summary of what they want"}\n\n'
    'For an invoice:\n'
    '{"kind": "invoice", "vendor": string|null, "invoice_number": string|null, '
    '"amount": number|null, "invoice_date": "YYYY-MM-DD"|null, '
    '"line_items": [{"description": string, "qty": number|null, "unit_price": number|null}]}\n\n'
    'For anything else:\n'
    '{"kind": "other"}\n\n'
    "Rules:\n"
    "- Respond with ONLY the JSON object, no prose, no markdown fences.\n"
    "- event_type must be one of the five allowed values; use \"Other\" if unsure.\n"
    "- Use null for any field you cannot determine. Do not invent values.\n"
    "- Dates must be calendar dates in YYYY-MM-DD form, or null.\n"
    "- amount is the invoice total / amount due as a plain number (no currency symbol).\n"
    "- line_items may be an empty array [] if you cannot read individual lines.\n"
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


def _normalize_line_items(value) -> list:
    """Coerce the model's line_items into a clean list of dicts. Drops anything
    malformed rather than raising."""
    if not isinstance(value, list):
        return []
    items = []
    for it in value:
        if not isinstance(it, dict):
            continue
        desc = it.get("description")
        if desc is None:
            continue
        items.append({
            "description": str(desc),
            "qty": _coerce_number(it.get("qty")),
            "unit_price": _coerce_number(it.get("unit_price")),
        })
    return items


def classify_email(
    subject: str, from_header: str, body: str, pdfs: list | None = None
) -> dict:
    """Classify an email into one of three kinds and extract structured fields.

    Returns a dict that always has key "kind" — one of "inquiry", "invoice", or
    "other". For "inquiry" the normalized lead fields are present; for "invoice"
    the normalized invoice fields are present. The legacy "is_inquiry" boolean is
    also included for backward-compat. On any error or malformed model output,
    returns {"kind": "other", "is_inquiry": False}.

    `pdfs` is an optional list of (filename, bytes) tuples; when present, each PDF
    is passed to Claude as a document content block so invoice fields can be read
    straight from the attachment (the source of truth for billing data).
    """
    text_block = (
        f"From: {from_header or '(unknown)'}\n"
        f"Subject: {subject or '(no subject)'}\n\n"
        f"Body:\n{(body or '').strip()[:8000]}"
    )

    # Build the message content. Document (PDF) blocks first, then the text
    # instruction — Claude reads the attachments as the source of truth.
    content: list[dict] = []
    for filename, data in (pdfs or []):
        try:
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(data).decode("ascii"),
                },
            })
        except Exception as e:
            log.warning(f"Skipping unencodable PDF {filename!r}: {e}")
    content.append({"type": "text", "text": text_block})

    try:
        resp = _client.messages.create(
            model=config.MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIError as e:
        log.error(f"Claude classify error: {e}")
        return {"kind": "other", "is_inquiry": False}

    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        log.warning(f"Lead parser: unparseable model output: {text[:200]!r}")
        return {"kind": "other", "is_inquiry": False}

    # Accept either the new "kind" field or the legacy "is_inquiry" boolean.
    kind = parsed.get("kind")
    if kind not in ("inquiry", "invoice", "other"):
        kind = "inquiry" if parsed.get("is_inquiry") else "other"

    if kind == "inquiry":
        event_type = parsed.get("event_type")
        if event_type not in EVENT_TYPES:
            event_type = "Other"
        return {
            "kind": "inquiry",
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

    if kind == "invoice":
        return {
            "kind": "invoice",
            "is_inquiry": False,
            "vendor": (parsed.get("vendor") or None),
            "invoice_number": (
                str(parsed["invoice_number"])
                if parsed.get("invoice_number") not in (None, "")
                else None
            ),
            "amount": _coerce_number(parsed.get("amount")),
            "invoice_date": (parsed.get("invoice_date") or None),
            "line_items": _normalize_line_items(parsed.get("line_items")),
        }

    return {"kind": "other", "is_inquiry": False}
