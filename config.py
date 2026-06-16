import os

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-6"

# Venture Studio Supabase (entity financials, tasks, initiatives)
VS_SUPABASE_URL = os.getenv("VS_SUPABASE_URL", "")
VS_SUPABASE_KEY = os.getenv("VS_SUPABASE_KEY", "")

# Blooms OS Supabase (floral operations — future)
BLOOMS_SUPABASE_URL = os.getenv("BLOOMS_SUPABASE_URL", "")
BLOOMS_SUPABASE_KEY = os.getenv("BLOOMS_SUPABASE_KEY", "")

# --- Email lead poller --------------------------------------------------------
# Reads the shop's Gmail inbox over IMAP, classifies prospective event/floral
# inquiries with Claude, and creates a lead in Blooms via the submit_event_inquiry
# RPC. Read-only on the inbox (never marks read / moves / deletes). If the inbox
# credentials below are unset, the poller logs "not configured" and does nothing,
# so the app still boots fine. Use a Gmail App Password (not the account password).
BLOOMS_INBOX_EMAIL = os.getenv("BLOOMS_INBOX_EMAIL", "")
BLOOMS_INBOX_APP_PASSWORD = os.getenv("BLOOMS_INBOX_APP_PASSWORD", "")
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
EMAIL_POLL_INTERVAL = int(os.getenv("EMAIL_POLL_INTERVAL", "300"))  # seconds
EMAIL_LOOKBACK_DAYS = int(os.getenv("EMAIL_LOOKBACK_DAYS", "7"))

# --- Auth ---------------------------------------------------------------------
# The Blooms OS project that issues the access tokens the frontend sends. Used
# only to validate those tokens (signature + expiry) and read the caller's email.
# The anon key is public by design (it's already shipped in the frontend bundle).
BLOOMS_OS_URL = os.getenv("BLOOMS_OS_URL", "https://pqhatplothwhdanfrcrq.supabase.co")
BLOOMS_OS_ANON_KEY = os.getenv(
    "BLOOMS_OS_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBxaGF0cGxvdGh3aGRhbmZyY3JxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDE0NzM5NDgsImV4cCI6MjA1NzA0OTk0OH0.4nmjVjWu_hylb7WaNKsPk6_JMXAWX5C4n5V1zp_Gr88",
)

# Only these emails may use this (financials-capable) personal assistant.
_raw_emails = os.getenv(
    "BLOOMS_AGENT_ALLOWED_EMAILS",
    "bileysir@gmail.com,erussell25@gmail.com,hello@bloomsinbunches.com",
)
ALLOWED_EMAILS = {e.strip().lower() for e in _raw_emails.split(",") if e.strip()}

# CORS — allowed origins for the chat widget
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
# Support "*" to allow all origins (useful during development)
ALLOWED_ORIGINS = "*" if _raw_origins.strip() == "*" else [o.strip() for o in _raw_origins.split(",")]

PORT = int(os.getenv("PORT", "8080"))
