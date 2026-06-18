"""
Weekly content generator. Once a week (Monday), generate 3 social/Google-post
DRAFTS and file them on the Blooms OS Content page (status pending_review) for
the coordinator to edit and the owner to approve. Nothing posts publicly — these
are drafts a human reviews.

Pulls "what to write about" (seasonal topics + recent real events) from the
get_content_context RPC and files each draft via submit_content_draft (both
SECURITY DEFINER, anon-callable — see migration 071). Mirrors the email poller:
a daily thread that fires at most once per week, guarded by a server-side count
of auto drafts created in the last 6 days.
"""
import json
import logging
import threading
import time
from datetime import datetime

import anthropic
import httpx

import config
from tools import BLOOMS_SUPABASE_URL, BLOOMS_SUPABASE_KEY

log = logging.getLogger("blooms.content_generator")
_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
_started = False
_start_lock = threading.Lock()

BRAND = (
    "Blooms in Bunches — a warm, family-run florist in North Merrick, NY (Nassau "
    "County), 22 years in business. Soft, natural, elegant aesthetic (not loud or "
    "oversaturated). Known for weddings/events, sympathy work, and everyday "
    "arrangements. Friendly, local, not corporate."
)

_SYSTEM = (
    "You write short, on-brand social posts for a local flower shop. Voice: warm, "
    "genuine, local — never salesy or generic. No hashtag spam (2-4 tasteful tags "
    "max for instagram/facebook; none for gbp_post). Keep each post tight.\n\n"
    "Return ONLY a JSON array of exactly 3 objects, no prose, shaped:\n"
    '[{"type":"gbp_post"|"instagram"|"facebook","body":"...","image_suggestion":"a '
    'short description of the photo to pair with it"}]\n'
    "Make the 3 a mix: 1 seasonal/holiday (if a topic is provided), 1 showcasing "
    "recent work (if a recent event is provided), 1 evergreen (a care tip, "
    "behind-the-scenes, or just-because)."
)


def _rpc(name: str, payload: dict | None = None):
    url = f"{BLOOMS_SUPABASE_URL.rstrip('/')}/rest/v1/rpc/{name}"
    headers = {
        "apikey": BLOOMS_SUPABASE_KEY,
        "Authorization": f"Bearer {BLOOMS_SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=20) as c:
        resp = c.post(url, headers=headers, json=payload or {})
    resp.raise_for_status()
    return resp.json()


def _extract_json(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def generate_weekly(force: bool = False) -> int:
    """Generate + file this week's drafts. Returns how many were filed. Skips if
    auto drafts already exist this week (unless force)."""
    try:
        ctx = _rpc("get_content_context")
    except Exception as e:
        log.error(f"content context fetch failed: {e}")
        return 0

    if not force and (ctx.get("recent_auto_drafts") or 0) > 0:
        log.info("Weekly content already generated this week — skipping.")
        return 0

    topics = ctx.get("topics") or []
    events = ctx.get("recent_events") or []
    user = (
        f"Shop: {BRAND}\n\n"
        f"Seasonal topics in play: {json.dumps(topics)}\n"
        f"Recent events to maybe showcase: {json.dumps(events)}\n\n"
        "Write the 3 drafts."
    )

    try:
        resp = _client.messages.create(
            model=config.MODEL, max_tokens=1500, system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIError as e:
        log.error(f"content generation error: {e}")
        return 0

    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    drafts = _extract_json(text)
    if not isinstance(drafts, list):
        log.warning(f"content generator: unparseable output: {text[:200]!r}")
        return 0

    filed = 0
    for d in drafts[:3]:
        if not isinstance(d, dict) or not d.get("body"):
            continue
        try:
            _rpc("submit_content_draft", {
                "_type": d.get("type") or "gbp_post",
                "_body": d.get("body"),
                "_image_suggestion": d.get("image_suggestion"),
            })
            filed += 1
        except Exception as e:
            log.error(f"submit_content_draft failed: {e}")
    log.info(f"Filed {filed} content draft(s).")
    return filed


def _loop():
    log.info("Content generator started (weekly, Mondays).")
    while True:
        try:
            # Fire on Mondays; the server-side 6-day guard prevents duplicates.
            if datetime.utcnow().weekday() == 0:
                generate_weekly()
        except Exception as e:
            log.error(f"content generator cycle failed: {e}", exc_info=True)
        time.sleep(6 * 3600)  # check every 6h


def start_content_generator() -> bool:
    """Start the weekly content thread once. Safe to call from every worker —
    the server-side recent-draft guard makes generation idempotent across them."""
    global _started
    if not config.ANTHROPIC_API_KEY:
        log.info("Content generator: ANTHROPIC_API_KEY unset — skipping.")
        return False
    with _start_lock:
        if _started:
            return False
        _started = True
    threading.Thread(target=_loop, name="content-generator", daemon=True).start()
    return True
