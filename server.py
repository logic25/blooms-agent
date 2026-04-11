"""
Blooms Agent Server — Flask app with CORS for the Blooms OS chat widget.
"""
import asyncio
import logging
import uuid

from flask import Flask, request, jsonify
from flask_cors import CORS

import config
from agent import chat

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("blooms.server")

app = Flask(__name__)
CORS(app, origins=config.ALLOWED_ORIGINS, supports_credentials=False)

# Shared event loop for async tool calls
_loop = asyncio.new_event_loop()


def run_async(coro):
    """Run an async coroutine from sync Flask context."""
    return _loop.run_until_complete(coro)


@app.route("/health", methods=["GET"])
def health():
    """Health check for Railway."""
    return jsonify({
        "status": "ok",
        "service": "blooms-agent",
        "model": config.MODEL,
        "vs_supabase_configured": bool(config.VS_SUPABASE_URL and config.VS_SUPABASE_KEY),
        "blooms_os_configured": bool(config.BLOOMS_SUPABASE_URL and config.BLOOMS_SUPABASE_KEY),
        "anthropic_configured": bool(config.ANTHROPIC_API_KEY),
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Chat endpoint for the Blooms OS widget.

    Request body:
        {
            "message": "What should I order today?",
            "session_id": "optional-session-id"
        }

    Response:
        {
            "response": "Based on today being Tuesday...",
            "session_id": "the-session-id"
        }
    """
    data = request.get_json()
    if not data or not data.get("message"):
        return jsonify({"error": "message is required"}), 400

    user_message = data["message"].strip()
    if not user_message:
        return jsonify({"error": "message cannot be empty"}), 400

    # Get or create session ID
    session_id = data.get("session_id") or str(uuid.uuid4())

    log.info(f"Chat [{session_id[:8]}]: {user_message[:100]}")

    try:
        response_text = run_async(chat(session_id, user_message))
        log.info(f"Response [{session_id[:8]}]: {response_text[:100]}")
        return jsonify({
            "response": response_text,
            "session_id": session_id,
        })
    except Exception as e:
        log.error(f"Chat error [{session_id[:8]}]: {e}", exc_info=True)
        return jsonify({
            "error": "Something went wrong. Please try again.",
            "session_id": session_id,
        }), 500


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def clear_session(session_id):
    """Clear a chat session."""
    from agent import _sessions
    if session_id in _sessions:
        del _sessions[session_id]
    return jsonify({"status": "cleared"})


if __name__ == "__main__":
    log.info(f"Starting Blooms Agent on port {config.PORT}")
    log.info(f"VS Supabase: {'configured' if config.VS_SUPABASE_URL else 'NOT configured'}")
    log.info(f"Anthropic: {'configured' if config.ANTHROPIC_API_KEY else 'NOT configured'}")
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
