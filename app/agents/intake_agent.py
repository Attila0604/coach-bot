"""Intake-Agent: Onboarding conversation.

Single-call pattern (wie food_log_agent):
Claude gibt pro Turn immer einen JSON-Block zurück mit:
  - extracted: was aus der letzten User-Nachricht als strukturierter Wert
    herausgelesen wurde (leer wenn nichts Neues).
  - reply: die nächste Nachricht an den Kunden.
  - is_complete: true, wenn nach Merge von `extracted` in `collected`
    alle Pflichtfelder vorhanden sind.

Der Server mergt `extracted` in den persistent gespeicherten `collected`-Dict
und finalisiert das Profil, sobald alle Pflichtfelder da sind.
"""
import json
import re

from app import db
from app.agents import telegram_agent
from app.services import claude_client

REQUIRED_FIELDS = [
    ("age", "Alter in Jahren (int)"),
    ("gender", "Geschlecht (m/w/d)"),
    ("height_cm", "Größe in cm (int)"),
    ("weight_start_kg", "aktuelles Gewicht in kg (float)"),
    ("weight_target_kg", "Zielgewicht in kg (float)"),
    ("goal", "Hauptziel (abnehmen / muskelaufbau / erhalt / ausdauer)"),
    ("experience_level", "Erfahrung (anfaenger / fortgeschritten / profi)"),
    ("equipment", "Equipment (home_none / home_basic / gym)"),
    ("allergies", "Allergien oder Unverträglichkeiten (array, leer wenn keine)"),
    ("food_preferences", "Ernährungsvorlieben (array, leer wenn keine)"),
]
REQUIRED_KEYS = {k for k, _ in REQUIRED_FIELDS}

SYSTEM_PROMPT = """Du bist der Onboarding-Assistent eines Ernährungs- und Fitness-Coaches.
Du sprichst auf Deutsch, per Du, locker und menschlich — kein Fragebogen-Stil.

AUFGABE
- Sammle die Pflichtfelder im Gespräch. EINE Frage pro Nachricht, nie mehrere.
- Wiederhole NIEMALS eine Frage zu einem Feld, das unter "Bereits erfasst" steht.
- Wenn die letzte User-Nachricht neue Infos enthält, lies sie heraus und fülle sie in "extracted".

Bereits erfasst (NICHT nochmal fragen):
{collected_summary}

Noch zu erfassen:
{missing_list}

ANTWORT-FORMAT
Antworte IMMER ausschließlich mit EINEM JSON-Block, nichts davor, nichts danach:

```json
{{
  "extracted": {{
    // Nur Keys ausfüllen, die du aus der LETZTEN User-Nachricht sicher
    // herausliest. Leeres Objekt wenn nichts Neues. Erlaubte Keys:
    // age (int), gender ("m"|"w"|"d"), height_cm (int),
    // weight_start_kg (float), weight_target_kg (float),
    // goal ("abnehmen"|"muskelaufbau"|"erhalt"|"ausdauer"),
    // experience_level ("anfaenger"|"fortgeschritten"|"profi"),
    // equipment ("home_none"|"home_basic"|"gym"),
    // allergies (array of strings), food_preferences (array of strings)
  }},
  "reply": "Deine nächste Nachricht an den Kunden — 1 bis 2 Sätze, eine Frage.",
  "is_complete": false
}}
```

is_complete-REGEL
- Setze "is_complete": true NUR dann, wenn NACH dem Merge von "extracted" in "Bereits erfasst"
  ALLE 10 Pflichtfelder vorhanden sind.
- Wenn "is_complete": true → "reply" ist ein kurzer, motivierender Abschluss-Satz.
  KEINE weitere Frage mehr stellen.
- Sonst: "is_complete": false und "reply" ist die nächste logische Frage.

Keine Markdown-Formatierung außerhalb des JSON-Blocks. Kein Text außerhalb des JSON-Blocks.
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
    """Single Claude call: extract + reply + completeness check."""
    collected = (state or {}).get("state_data", {}).get("collected", {}) or {}

    collected_summary = (
        "\n".join(f"- {k}: {_fmt(v)}" for k, v in collected.items())
        if collected
        else "(noch nichts)"
    )
    missing = [
        f"- {key}: {desc}"
        for key, desc in REQUIRED_FIELDS
        if key not in collected
    ]
    missing_list = "\n".join(missing) if missing else "(alle Felder erfasst — setze is_complete=true)"

    system = SYSTEM_PROMPT.format(
        collected_summary=collected_summary,
        missing_list=missing_list,
    )

    history = db.recent_messages(customer["id"], limit=20)
    # Drop the just-logged user message from history (we'll append it fresh)
    history = [h for h in history if h["content"] != user_text or h["direction"] != "in"]

    messages = claude_client.build_messages_from_history(history, user_text)
    raw, tokens, model = claude_client.ask(system, messages, max_tokens=500)

    data = _parse_json(raw)
    if data is None:
        # Claude hat kein valides JSON geliefert — freundliche Retry-Aufforderung.
        fallback = "Hm, da ist mir gerade was durcheinander geraten — kannst du das nochmal schreiben?"
        await telegram_agent.send_message(customer["telegram_chat_id"], fallback)
        db.log_message(
            customer["id"], "out", fallback,
            agent_name="intake", model_used=model, tokens_used=tokens,
        )
        return

    # 1) Merge extracted in collected (nur Non-Empty-Werte)
    extracted = data.get("extracted") or {}
    for k, v in extracted.items():
        if k not in REQUIRED_KEYS:
            continue  # ignoriere unbekannte Keys
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        # Leere Arrays sind bei allergies / food_preferences ein GÜLTIGER Wert
        # ("keine Allergien") — also nicht filtern.
        collected[k] = v

    reply_text = (data.get("reply") or "").strip()
    claude_complete = bool(data.get("is_complete"))

    # 2) Server-seitige Wahrheit: ist wirklich alles da?
    all_present = REQUIRED_KEYS.issubset(collected.keys())
    is_complete = claude_complete and all_present

    # 3) Wenn Claude fälschlich is_complete=true sagte, aber noch Felder fehlen,
    #    fragen wir hier direkt weiter — robuste Absicherung.
    if claude_complete and not all_present:
        still_missing = [desc for key, desc in REQUIRED_FIELDS if key not in collected]
        reply_text = (
            f"Fast geschafft! Eine Sache brauche ich noch: {still_missing[0]}?"
        )

    # 4) Fortschritts-Anzeige wenn nicht fertig
    if not is_complete:
        done = len(REQUIRED_KEYS & set(collected.keys()))
        total = len(REQUIRED_KEYS)
        if done > 0 and reply_text:
            reply_text = f"({done}/{total}) {reply_text}"

    # 5) State persistieren (immer, auch bei Fertigstellung — falls DB-Write fehlschlägt)
    db.set_conversation_state(
        customer_id=customer["id"],
        current_flow="intake" if not is_complete else None,
        current_step="ask_next" if not is_complete else None,
        state_data={"collected": collected} if not is_complete else {},
    )

    # 6) Antwort senden
    if reply_text:
        await telegram_agent.send_message(customer["telegram_chat_id"], reply_text)
        db.log_message(
            customer["id"], "out", reply_text,
            agent_name="intake", model_used=model, tokens_used=tokens,
        )

    # 7) Finalisieren wenn wirklich komplett
    if is_complete:
        _finalize_intake(customer, collected)
        await telegram_agent.send_message(
            customer["telegram_chat_id"],
            "✅ Dein Profil ist angelegt. Der Coach setzt deine Makro-Ziele und meldet "
            "sich dann bei dir. Du kannst ab sofort jederzeit dein Essen als Text loggen.",
        )


def _parse_json(raw: str) -> dict | None:
    """Parse JSON aus Claude-Antwort. Akzeptiert mit/ohne ```json fences."""
    if not raw:
        return None
    # Zuerst: ```json ... ``` block
    m = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Fallback: erster {...}-Block
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _fmt(v) -> str:
    """Hübschere Darstellung für den System-Prompt."""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "(keine)"
    return str(v)


def _finalize_intake(customer: dict, collected: dict) -> None:
    """Write profile to DB and mark customer as active."""
    profile_row = {
        "customer_id": customer["id"],
        "age": collected.get("age"),
        "gender": collected.get("gender"),
        "height_cm": collected.get("height_cm"),
        "weight_start_kg": collected.get("weight_start_kg"),
        "weight_target_kg": collected.get("weight_target_kg"),
        "goal": collected.get("goal"),
        "experience_level": collected.get("experience_level"),
        "equipment": collected.get("equipment"),
        "allergies": collected.get("allergies", []),
        "food_preferences": collected.get("food_preferences", []),
    }
    db.db().table("customer_profiles").upsert(profile_row).execute()
    db.update_customer_status(customer["id"], "active")
    db.db().table("customers").update({"onboarded_at": "now()"}).eq("id", customer["id"]).execute()
    db.set_conversation_state(customer["id"], current_flow=None, current_step=None, state_data={})
