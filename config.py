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

# CORS — allowed origins for the chat widget
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
# Support "*" to allow all origins (useful during development)
ALLOWED_ORIGINS = "*" if _raw_origins.strip() == "*" else [o.strip() for o in _raw_origins.split(",")]

PORT = int(os.getenv("PORT", "8080"))
