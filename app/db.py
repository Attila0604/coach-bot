"""Supabase client (uses service key, so bypasses RLS).

All DB access goes through this module. Functions return plain dicts /
lists so agents don't need to know about supabase-py internals.
"""
from typing import Any, Optional
import secrets
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
from app.config import settings

_client: Optional[Client] = None


def db() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    return _client


# ----- Customers -----
def get_customer_by_chat_id(chat_id: int) -> Optional[dict[str, Any]]:
    resp = (
        db()
        .table("customers")
        .select("*, customer_profiles(*)")
        .eq("telegram_chat_id", chat_id)
        .maybe_single()
        .execute()
    )
    return resp.data if resp else None


def get_customer_by_id(customer_id: str) -> Optional[dict[str, Any]]:
    resp = (
        db()
        .table("customers")
        .select("*, customer_profiles(*)")
        .eq("id", customer_id)
        .maybe_single()
        .execute()
    )
    return resp.data if resp else None


def create_customer(coach_id: str, chat_id: int, username: str | None, first_name: str) -> dict[str, Any]:
    resp = (
        db()
        .table("customers")
        .insert(
            {
                "coach_id": coach_id,
                "telegram_chat_id": chat_id,
                "telegram_username": username,
                "first_name": first_name,
            }
        )
        .execute()
    )
    return resp.data[0]


def update_customer_status(customer_id: str, status: str) -> None:
    db().table("customers").update({"status": status}).eq("id", customer_id).execute()


# ----- Conversation state -----
def get_conversation_state(customer_id: str) -> dict[str, Any]:
    resp = (
        db()
        .table("conversation_states")
        .select("*")
        .eq("customer_id", customer_id)
        .maybe_single()
        .execute()
    )
    return resp.data if resp else {"customer_id": customer_id, "state_data": {}}


def set_conversation_state(
    customer_id: str, current_flow: str | None, current_step: str | None, state_data: dict
) -> None:
    db().table("conversation_states").upsert(
        {
            "customer_id": customer_id,
            "current_flow": current_flow,
            "current_step": current_step,
            "state_data": state_data,
        }
    ).execute()


# ----- Messages -----
def log_message(
    customer_id: str,
    direction: str,
    content: str,
    agent_name: str | None = None,
    model_used: str | None = None,
    tokens_used: int | None = None,
) -> None:
    db().table("messages").insert(
        {
            "customer_id": customer_id,
            "direction": direction,
            "content": content,
            "agent_name": agent_name,
            "model_used": model_used,
            "tokens_used": tokens_used,
        }
    ).execute()


def recent_messages(customer_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent messages (oldest-first) for Claude context."""
    resp = (
        db()
        .table("messages")
        .select("direction, content, created_at")
        .eq("customer_id", customer_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(resp.data))


# ----- Login-Tokens (Ein-Klick-Login-Link) -----

LOGIN_TOKEN_DAYS = 7


def get_or_create_login_token(customer_id: str) -> Optional[str]:
    """Gibt ein gültiges Login-Token zurück (7 Tage gültig, mehrfach nutzbar).

    Wiederverwendet ein bestehendes, noch gültiges Token, sonst wird ein neues
    erzeugt. Bei Fehler -> None (Nachricht wird dann einfach ohne Link gesendet).
    """
    now = datetime.now(timezone.utc)
    try:
        resp = (
            db()
            .table("login_tokens")
            .select("token, expires_at")
            .eq("customer_id", customer_id)
            .gt("expires_at", now.isoformat())
            .order("expires_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows:
            return rows[0]["token"]

        token = secrets.token_urlsafe(32)
        expires = now + timedelta(days=LOGIN_TOKEN_DAYS)
        db().table("login_tokens").insert(
            {
                "token": token,
                "customer_id": customer_id,
                "expires_at": expires.isoformat(),
            }
        ).execute()
        return token
    except Exception:
        return None
