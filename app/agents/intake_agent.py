"""Intake-Agent: Onboarding conversation.

Conversational flow (not a rigid form):
Claude leads a natural chat, extracting structured data step by step.
The steps array defines what information must be collected before
marking the customer as 'active'.
"""
from app import db
from app.agents import telegram_agent
from app.services import claude_client

REQUIRED_FIELDS = [
    ("age", "Alter in Jahren"),
    ("gender", "Geschlecht (m/w/d)"),
    ("height_cm", "Größe in cm"),
    ("weight_start_kg", "aktuelles Gewicht in kg"),
    ("weight_target_kg", "Zielgewicht in kg"),
    ("goal", "Hauptziel (abnehmen / muskelaufbau / erhalt / ausdauer)"),
    ("experience_level", "Erfahrung (anfaenger / fortgeschritten / profi)"),
    ("equipment", "Equipment (home_none / home_basic / gym)"),
    ("allergies", "Allergien oder Unverträglichkeiten"),
    ("food_preferences", "Ernährungsvorlieben (vegetarisch, keine Fische, etc.)"),
]

SYSTEM_PROMPT = """Du bist ein freundlicher Onboarding-Assistent eines Ernährungs- und Fitness-Coaches.
Deine Aufgabe: sammle in einem natürlichen, lockeren Gespräch die folgenden Informationen vom Kunden.
Stelle EINE Frage pro Nachricht, nicht mehrere auf einmal. Bleib kurz und menschlich, kein Fragebogen-Stil.

Pflichtfelder (noch zu erfassen werden dir mitgeteilt):
{missing_list}

Wenn alles erfasst ist, fasse die Antworten in EINEM JSON-Block zusammen, EXAKT so:
```json
{{
  "age": 30,
  "gender": "m",
  "height_cm": 180,
  "weight_start_kg": 85.0,
  "weight_target_kg": 78.0,
  "goal": "abnehmen",
  "experience_level": "fortgeschritten",
  "equipment": "gym",
  "allergies": ["Laktose"],
  "food_preferences": ["vegetarisch"]
}}
```
Vor dem JSON schreibst du einen kurzen, motivierenden Abschluss-Satz auf Deutsch.
"""


async def start(customer: dict) -> None:
    """Send the very first intake message."""
    first_name = customer["first_name"]
    greeting = (
        f"Hi {first_name}! 👋 Schön, dass du da bist. "
        f"Bevor wir richtig loslegen, brauche ich ein paar Infos von dir – "
        f"nichts aufwendiges, einfach ein kurzer Chat. "
        f"Ganz ehrlich: wie alt bist du?"
    )
    await telegram_agent.send_message(customer["telegram_chat_id"], greeting)
    db.log_message(customer["id"], "out", greeting, agent_name="intake")
    db.set_conversation_state(
        customer_id=customer["id"],
        current_flow="intake",
        current_step="ask_age",
        state_data={"collected": {}},
    )


async def handle_step(customer: dict, state: dict, user_text: str) -> None:
    """Claude drives the conversation; we just pass context and missing fields."""
    collected = (state or {}).get("state_data", {}).get("collected", {})

    missing = [
        f"- {key}: {desc}"
        for key, desc in REQUIRED_FIELDS
        if key not in collected
    ]
    system = SYSTEM_PROMPT.format(missing_list="\n".join(missing) if missing else "(alle Felder erfasst)")

    history = db.recent_messages(customer["id"], limit=20)
    # Drop the just-logged user message from history (we'll append it fresh)
    history = [h for h in history if h["content"] != user_text or h["direction"] != "in"]

    messages = claude_client.build_messages_from_history(history, user_text)
    reply, tokens, model = claude_client.ask(system, messages, max_tokens=500)

    # If JSON block present, parse and finalize
    if "```json" in reply:
        import json
        import re
        match = re.search(r"```json\s*(\{.*?\})\s*```", reply, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                _finalize_intake(customer, parsed)
                # Send only the prose part (before the JSON block)
                prose = reply.split("```json")[0].strip()
                if prose:
                    await telegram_agent.send_message(customer["telegram_chat_id"], prose)
                    db.log_message(
                        customer["id"], "out", prose,
                        agent_name="intake", model_used=model, tokens_used=tokens,
                    )
                await telegram_agent.send_message(
                    customer["telegram_chat_id"],
                    "✅ Dein Profil ist angelegt. Der Coach setzt deine Makro-Ziele und meldet sich dann bei dir. Du kannst ab sofort jederzeit dein Essen als Text loggen.",
                )
                return
            except json.JSONDecodeError:
                pass  # fall through to normal reply

    await telegram_agent.send_message(customer["telegram_chat_id"], reply)
    db.log_message(
        customer["id"], "out", reply,
        agent_name="intake", model_used=model, tokens_used=tokens,
    )


def _finalize_intake(customer: dict, parsed: dict) -> None:
    """Write profile to DB and mark customer as active."""
    profile_row = {
        "customer_id": customer["id"],
        "age": parsed.get("age"),
        "gender": parsed.get("gender"),
        "height_cm": parsed.get("height_cm"),
        "weight_start_kg": parsed.get("weight_start_kg"),
        "weight_target_kg": parsed.get("weight_target_kg"),
        "goal": parsed.get("goal"),
        "experience_level": parsed.get("experience_level"),
        "equipment": parsed.get("equipment"),
        "allergies": parsed.get("allergies", []),
        "food_preferences": parsed.get("food_preferences", []),
    }
    db.db().table("customer_profiles").upsert(profile_row).execute()
    db.update_customer_status(customer["id"], "active")
    db.db().table("customers").update({"onboarded_at": "now()"}).eq("id", customer["id"]).execute()
    db.set_conversation_state(customer["id"], current_flow=None, current_step=None, state_data={})
