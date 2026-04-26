"""Thin wrapper around Anthropic SDK with cost tracking."""
from typing import Optional
from anthropic import Anthropic
from app.config import settings

_client: Optional[Anthropic] = None

# Vision model — Haiku 4.5 has vision and is cheap. Switch to Sonnet if estimates feel off.
VISION_MODEL_DEFAULT = "claude-haiku-4-5-20251001"


def client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def ask(
    system_prompt: str,
    messages: list[dict],
    model: str | None = None,
    max_tokens: int = 1024,
) -> tuple[str, int, str]:
    """Send a conversation to Claude. Returns (reply_text, total_tokens, model_used)."""
    model_used = model or settings.CLAUDE_MODEL_DEFAULT
    resp = client().messages.create(
        model=model_used,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=messages,
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    total_tokens = resp.usage.input_tokens + resp.usage.output_tokens
    return text, total_tokens, model_used


def ask_with_image(
    system_prompt: str,
    image_b64: str,
    media_type: str,
    user_text: str,
    model: str | None = None,
    max_tokens: int = 800,
) -> tuple[str, int, str]:
    """Send a single-image + text prompt to Claude Vision.

    Returns (reply_text, total_tokens, model_used).
    """
    model_used = model or VISION_MODEL_DEFAULT
    resp = client().messages.create(
        model=model_used,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": user_text},
                ],
            }
        ],
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    total_tokens = resp.usage.input_tokens + resp.usage.output_tokens
    return text, total_tokens, model_used


def build_messages_from_history(history: list[dict], new_user_msg: str) -> list[dict]:
    """Convert DB message rows into Anthropic-format messages + append new user msg."""
    msgs = []
    for row in history:
        role = "user" if row["direction"] == "in" else "assistant"
        msgs.append({"role": role, "content": row["content"]})
    msgs.append({"role": "user", "content": new_user_msg})
    # Anthropic requires alternating roles starting with user; merge consecutives
    merged = []
    for m in msgs:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += "\n" + m["content"]
        else:
            merged.append(m)
    # Must start with user
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged
