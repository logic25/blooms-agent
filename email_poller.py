"""
Email -> lead ingestion poller.

A background thread that periodically reads the shop's Gmail inbox over IMAP,
asks Claude whether each message is a NEW prospective event/floral inquiry, and
for each one creates a lead in Blooms via the existing `submit_event_inquiry`
SECURITY DEFINER RPC (called with the public anon key — no service key).

Idempotency: a per-message claim is recorded in Blooms via the `claim_email`
RPC (insert ... on conflict do nothing, returns true only for a fresh claim).
We claim a message ONLY after we've finished deciding what to do with it:
  - non-inquiry        -> claim (so it's never re-examined)
  - inquiry, lead made -> claim (after submit_event_inquiry returns 200)
  - inquiry, no email / RPC failed -> do NOT claim, retry next cycle
Because the claim is atomic in Postgres, running multiple poller threads (e.g.
gunicorn's 2 workers) is safe: only one thread ever claims a given message.

Inbox hygiene: this poller is strictly read-only on the mailbox. It searches by
SINCE date (not UNSEEN) and never sets the \\Seen flag, moves, or deletes
anything — Bileysi's inbox must look untouched. IMAP SELECT is done read-only.
"""
import email
import imaplib
import logging
import threading
import time
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr

import httpx

import config
from lead_parser import classify_email
# Reuse the same Blooms OS connection the agent's tools use. These are the
# public anon key + project URL (hardcoded there, always present), so the poller
# works even when the BLOOMS_SUPABASE_* env vars are unset. All writes go through
# SECURITY DEFINER RPCs (submit_event_inquiry, claim_email) — no service key.
from tools import BLOOMS_SUPABASE_URL, BLOOMS_SUPABASE_KEY

log = logging.getLogger("blooms.email_poller")

# Module-level guard so the poller thread is started at most once per process.
_started = False
_start_lock = threading.Lock()


def _decode(value) -> str:
    """Decode a possibly RFC2047-encoded header into a plain string."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _get_message_id(msg) -> str | None:
    mid = msg.get("Message-ID") or msg.get("Message-Id")
    if mid:
        return mid.strip()
    return None


def _extract_body(msg) -> str:
    """Return the best-effort plain-text body. Walk multipart; fall back to
    stripped HTML if there's no text/plain part."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = (part.get_content_type() or "").lower()
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            payload = _part_text(part)
            if not payload:
                continue
            if ctype == "text/plain":
                plain_parts.append(payload)
            elif ctype == "text/html":
                html_parts.append(payload)
    else:
        ctype = (msg.get_content_type() or "").lower()
        payload = _part_text(msg)
        if payload:
            if ctype == "text/html":
                html_parts.append(payload)
            else:
                plain_parts.append(payload)

    if plain_parts:
        return "\n".join(plain_parts).strip()
    if html_parts:
        return _strip_html("\n".join(html_parts)).strip()
    return ""


# Limits for attachment extraction — keep the Claude payload and memory bounded.
_MAX_PDFS = 3
_MAX_PDF_BYTES = 10 * 1024 * 1024  # ~10 MB per PDF


def _extract_pdfs(msg) -> list:
    """Walk the message and return PDF attachments as (filename, bytes) tuples.

    A part counts as a PDF if its content-type is application/pdf or its filename
    ends in .pdf. Capped at the first _MAX_PDFS PDFs and _MAX_PDF_BYTES each so a
    huge attachment can't blow up memory or the Claude request."""
    pdfs: list = []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if len(pdfs) >= _MAX_PDFS:
            break
        if part.is_multipart():
            continue
        ctype = (part.get_content_type() or "").lower()
        filename = part.get_filename() or ""
        try:
            filename = _decode(filename)
        except Exception:
            pass
        is_pdf = ctype == "application/pdf" or filename.lower().endswith(".pdf")
        if not is_pdf:
            continue
        try:
            data = part.get_payload(decode=True)
        except Exception:
            continue
        if not data:
            continue
        if len(data) > _MAX_PDF_BYTES:
            log.warning(
                f"Skipping oversized PDF {filename or '(unnamed)'!r}: "
                f"{len(data)} bytes > {_MAX_PDF_BYTES}"
            )
            continue
        pdfs.append((filename or "attachment.pdf", data))
    return pdfs


def _part_text(part) -> str:
    try:
        raw = part.get_payload(decode=True)
        if raw is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    """Very small HTML -> text fallback (no external deps)."""
    import re
    # Drop script/style blocks entirely.
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    # Turn block-ish tags into newlines for readability.
    html = re.sub(r"(?i)<(br|/p|/div|/tr|/li)\s*/?>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    # Collapse whitespace and unescape a few common entities.
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text


def _claim_email(message_id: str) -> bool:
    """Atomically claim a message id in Blooms. Returns True only if this call
    was the one that claimed it (i.e. it was not already seen). On any error,
    returns False so we don't treat an unclaimed message as claimed."""
    url = f"{BLOOMS_SUPABASE_URL.rstrip('/')}/rest/v1/rpc/claim_email"
    headers = {
        "apikey": BLOOMS_SUPABASE_KEY,
        "Authorization": f"Bearer {BLOOMS_SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, headers=headers, json={"_message_id": message_id})
        if resp.status_code != 200:
            log.error(f"claim_email HTTP {resp.status_code}: {resp.text[:200]}")
            return False
        # RPC returns a bare boolean.
        return bool(resp.json())
    except Exception as e:
        log.error(f"claim_email error: {e}")
        return False


def _submit_lead(lead: dict) -> bool:
    """POST an extracted lead to the submit_event_inquiry RPC. Returns True on
    HTTP 200, False otherwise."""
    url = f"{BLOOMS_SUPABASE_URL.rstrip('/')}/rest/v1/rpc/submit_event_inquiry"
    headers = {
        "apikey": BLOOMS_SUPABASE_KEY,
        "Authorization": f"Bearer {BLOOMS_SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "_name": lead.get("name"),
        "_email": lead.get("email"),
        "_phone": lead.get("phone"),
        "_event_type": lead.get("event_type"),
        "_event_date": lead.get("event_date"),
        "_guest_count": lead.get("guest_count"),
        "_budget_min": lead.get("budget_min"),
        "_budget_max": lead.get("budget_max"),
        "_message": lead.get("message"),
    }
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(url, headers=headers, json=body)
        if resp.status_code == 200:
            return True
        log.error(f"submit_event_inquiry HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        log.error(f"submit_event_inquiry error: {e}")
        return False


def _submit_invoice(invoice: dict, subject: str, from_email: str) -> bool:
    """POST an extracted vendor invoice to the submit_vendor_invoice RPC. v1 just
    records the invoice for owner review (no cost posting). Returns True on HTTP
    200, False otherwise."""
    url = f"{BLOOMS_SUPABASE_URL.rstrip('/')}/rest/v1/rpc/submit_vendor_invoice"
    headers = {
        "apikey": BLOOMS_SUPABASE_KEY,
        "Authorization": f"Bearer {BLOOMS_SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "_vendor": invoice.get("vendor"),
        "_invoice_number": invoice.get("invoice_number"),
        "_amount": invoice.get("amount"),
        "_invoice_date": invoice.get("invoice_date"),
        "_line_items": invoice.get("line_items") or [],
        "_email_subject": subject or None,
        "_from_email": from_email or None,
    }
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(url, headers=headers, json=body)
        if resp.status_code == 200:
            return True
        log.error(f"submit_vendor_invoice HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        log.error(f"submit_vendor_invoice error: {e}")
        return False


def _process_message(msg) -> None:
    """Classify a single message and, if it's an inquiry, create a lead. Claims
    the message id when it should not be looked at again. Any exception here is
    caught by the caller so one bad email never kills the cycle."""
    message_id = _get_message_id(msg)
    if not message_id:
        # Without a stable id we can't dedup safely — skip rather than risk dupes.
        log.warning("Skipping message with no Message-ID header")
        return

    # Claim FIRST — this is the cross-worker lock. gunicorn runs 2 workers, each
    # with its own poller thread; without an up-front claim both would classify
    # AND create a lead for the same email (duplicate leads). claim_email is an
    # atomic insert-on-conflict, so exactly one worker gets True and proceeds;
    # the other gets False and skips. (Trade-off: a claimed message whose lead
    # submit later fails is not retried — logged loudly below for manual recovery.)
    if not _claim_email(message_id):
        return

    from_header = _decode(msg.get("From"))
    subject = _decode(msg.get("Subject"))
    body = _extract_body(msg)
    pdfs = _extract_pdfs(msg)

    result = classify_email(subject, from_header, body, pdfs=pdfs)
    kind = result.get("kind", "other")

    from_name, from_email = parseaddr(from_header)

    if kind == "invoice":
        # Require enough to be a real, identifiable invoice — at least a vendor
        # OR an invoice number, plus a non-null amount. Otherwise skip (already
        # claimed, which is fine; v1 only records for owner review).
        vendor = result.get("vendor")
        invoice_number = result.get("invoice_number")
        amount = result.get("amount")
        if not (vendor or invoice_number) or amount is None:
            log.info(
                f"Invoice missing required fields, skipping: "
                f"vendor={vendor!r} num={invoice_number!r} amount={amount!r} "
                f"— {subject[:60]!r}"
            )
            return
        if _submit_invoice(result, subject, from_email):
            log.info(
                f"Invoice captured: {vendor or '(unknown vendor)'} "
                f"#{invoice_number or '(no number)'} ${amount}"
            )
        else:
            log.error(
                f"Invoice submit FAILED after claim (won't retry — recover "
                f"manually): {subject[:60]!r} from {from_header[:60]!r}"
            )
        return

    if kind != "inquiry":
        log.info(f"Non-invoice/non-inquiry: {subject[:60]!r} from {from_header[:60]!r}")
        return

    # It's an inquiry. Fill gaps from the From header.
    if not result.get("email"):
        result["email"] = from_email or None
    if not result.get("name"):
        result["name"] = (_decode(from_name).strip() or None) if from_name else None

    if not result.get("email"):
        log.info(f"Inquiry with no email, skipping: {subject[:60]!r}")
        return
    if not result.get("name"):
        result["name"] = result["email"]

    if _submit_lead(result):
        log.info(
            f"Lead created from email: {result['name']} <{result['email']}> "
            f"({result['event_type']}) — {subject[:60]!r}"
        )
    else:
        log.error(
            f"Lead submit FAILED after claim (won't retry — recover manually): "
            f"{subject[:60]!r} from {from_header[:60]!r}"
        )


def _poll_once() -> None:
    """Run a single poll cycle: connect, search by SINCE date, process each
    message. Strictly read-only on the mailbox."""
    since = (datetime.utcnow() - timedelta(days=config.EMAIL_LOOKBACK_DAYS))
    since_str = since.strftime("%d-%b-%Y")  # IMAP date format, e.g. 08-Jun-2026

    imap = imaplib.IMAP4_SSL(config.IMAP_HOST)
    try:
        imap.login(config.BLOOMS_INBOX_EMAIL, config.BLOOMS_INBOX_APP_PASSWORD)
        # readonly=True so the server never sets \Seen on our fetches.
        imap.select("INBOX", readonly=True)

        status, data = imap.search(None, "SINCE", since_str)
        if status != "OK":
            log.error(f"IMAP search failed: {status}")
            return

        ids = data[0].split() if data and data[0] else []
        log.info(f"Email poll: {len(ids)} message(s) since {since_str}")

        for num in ids:
            try:
                # BODY.PEEK[] fetches the full message WITHOUT setting \Seen.
                status, fetched = imap.fetch(num, "(BODY.PEEK[])")
                if status != "OK" or not fetched or not fetched[0]:
                    log.warning(f"Fetch failed for message {num!r}")
                    continue
                raw = fetched[0][1]
                msg = email.message_from_bytes(raw)
                _process_message(msg)
            except Exception as e:
                # One bad email must never abort the whole cycle.
                log.error(f"Error processing message {num!r}: {e}", exc_info=True)
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _poll_loop() -> None:
    log.info(
        f"Email poller started (host={config.IMAP_HOST}, "
        f"interval={config.EMAIL_POLL_INTERVAL}s, lookback={config.EMAIL_LOOKBACK_DAYS}d)"
    )
    while True:
        try:
            _poll_once()
        except Exception as e:
            # Connection/login errors etc. — log and keep the loop alive.
            log.error(f"Email poll cycle failed: {e}", exc_info=True)
        time.sleep(config.EMAIL_POLL_INTERVAL)


def start_email_poller() -> bool:
    """Start the background poll loop once. Returns True if a thread was started,
    False if the poller is not configured or was already started.

    Safe to call from every gunicorn worker: claim_email makes per-message
    processing idempotent across processes."""
    global _started

    if not (config.BLOOMS_INBOX_EMAIL and config.BLOOMS_INBOX_APP_PASSWORD):
        log.info("Email poller not configured (BLOOMS_INBOX_EMAIL / "
                 "BLOOMS_INBOX_APP_PASSWORD unset) — skipping.")
        return False

    if not config.ANTHROPIC_API_KEY:
        log.warning("Email poller: ANTHROPIC_API_KEY unset — cannot classify, skipping.")
        return False

    with _start_lock:
        if _started:
            return False
        _started = True

    thread = threading.Thread(target=_poll_loop, name="email-poller", daemon=True)
    thread.start()
    return True
