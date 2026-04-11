"""
Blooms Agent — Claude-powered agentic loop for Bileysi's flower shop.
Takes a user message, reasons with tools, returns a response.
"""
import json
import logging
import os

import anthropic

import config
from tools import TOOL_DEFINITIONS, TOOL_DISPATCH

log = logging.getLogger("blooms.agent")

# Load soul.md as system prompt
_soul_path = os.path.join(os.path.dirname(__file__), "soul.md")
with open(_soul_path, "r") as f:
    SYSTEM_PROMPT = f.read()

# Initialize Anthropic client
client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

# Simple in-memory conversation store (per-session)
# In production, persist to Supabase
_sessions: dict[str, list] = {}

MAX_TOOL_ROUNDS = 5


async def chat(session_id: str, user_message: str) -> str:
    """Process a user message through the agentic loop. Returns the final text response."""

    # Get or create conversation history
    if session_id not in _sessions:
        _sessions[session_id] = []

    messages = _sessions[session_id]
    messages.append({"role": "user", "content": user_message})

    # Trim conversation history to last 20 turns to manage context
    if len(messages) > 40:
        messages = messages[-40:]
        _sessions[session_id] = messages

    # Agentic loop
    for round_num in range(MAX_TOOL_ROUNDS):
        try:
            response = client.messages.create(
                model=config.MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )
        except anthropic.APIError as e:
            log.error(f"Claude API error: {e}")
            return f"Sorry, I'm having trouble connecting right now. Try again in a minute."

        # Check if Claude is done (no more tool calls)
        if response.stop_reason == "end_turn":
            # Extract text from response
            text_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)

            final_text = "\n".join(text_parts) if text_parts else "I'm not sure how to help with that."

            # Save assistant response to history
            messages.append({"role": "assistant", "content": response.content})
            return final_text

        # Handle tool calls
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_use_blocks:
            # No tool calls and not end_turn — extract any text and return
            text_parts = [b.text for b in response.content if b.type == "text"]
            final_text = "\n".join(text_parts) if text_parts else "I'm not sure how to help with that."
            messages.append({"role": "assistant", "content": response.content})
            return final_text

        # Append assistant's response (with tool_use blocks)
        messages.append({"role": "assistant", "content": response.content})

        # Execute tools and collect results
        tool_results = []
        for tool_block in tool_use_blocks:
            tool_name = tool_block.name
            tool_input = tool_block.input or {}

            log.info(f"Tool call: {tool_name}({json.dumps(tool_input)[:200]})")

            if tool_name in TOOL_DISPATCH:
                try:
                    result = await TOOL_DISPATCH[tool_name](tool_input)
                    result_str = json.dumps(result, default=str)
                except Exception as e:
                    log.error(f"Tool {tool_name} error: {e}")
                    result_str = json.dumps({"error": str(e)})
            else:
                result_str = json.dumps({"error": f"Unknown tool: {tool_name}"})

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": result_str,
            })

        # Append tool results as user message
        messages.append({"role": "user", "content": tool_results})

    # If we exhausted all rounds, return what we have
    return "I ran into a limit processing your request. Could you try asking in a simpler way?"
