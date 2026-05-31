"""Food-Log-Agent: parses free-text meal descriptions and food photos into structured entries.

Also handles general chat in 'active' state — one Claude call classifies the
intent and returns either a food_log JSON or a chat reply.
"""
import base64
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from app import db
from app.agents import telegram_agent
from app.config import settings
from app.services import claude_client

TZ = ZoneInfo(settings.TZ)

SYSTEM_PROMPT = """Du bist der persönliche Assistent eines Ernährungs- und Fitness-Coaches.
Du sprichst mit dem Kunden direkt auf Deutsch, locker und motivierend, per Du.

AUFGABE: Analysiere die Nachricht des Kunden und entscheide, um was es geht.

Antworte IMMER ausschließlich mit einem JSON-Block, nichts davor, nichts danach:

Fall A — der Kunde beschreibt etwas, das er gegessen oder getrunken hat:
```json
{
  "type": "food_log",
  "meal_type": "fruehstueck" | "mittag" | "abend" | "snack",
  "items": [
    {"item": "Ei", "qty": 2, "unit": "Stk", "kcal": 156, "protein_g": 13.0, "carbs_g": 1.0, "fat_g": 11.0},
    {"item": "Vollkorntoast", "qty": 1, "unit": "Scheibe", "kcal": 80, "protein_g": 3.0, "carbs_g": 14.0, "fat_g": 1.0}
  ],
  "reply": "Kurze, freundliche Bestätigung auf Deutsch, 1-2 Sätze."
}
```

Regeln für food_log:
- meal_type aus Kontext (Uhrzeit, Worte wie "Frühstück", "Mittag", "Abend") ableiten, sonst "snack"
- Kalorien und Makros realistisch schätzen, deutsche/österreichische Portionsgrößen
- Bei unklarer Menge vernünftige Durchschnitts-Annahme treffen
- "reply" ist die Antwort, die der Kunde sieht — noch OHNE Tages-Summen (die hängt das System an)

Fall B — Frage, Plauderei, Stimmung, sonstiges:
```json
{
  "type": "chat",
  "reply": "Antwort auf Deutsch."
}
```

Fall C — der Kunde liefert eindeutig Check-in-Daten (Gewicht, Maße, Schlaf):
```json
{
  "type": "checkin_hint",
  "reply": "Antwort mit Hinweis, dass Check-ins über den wöchentlichen Dialog laufen."
}
```

WICHTIG: Antworte ausschließlich mit gültigem JSON. Keine Erklärungen davor/dahinter.
"""


PHOTO_SYSTEM_PROMPT = """Du bist der persönliche Assistent eines Ernährungs- und Fitness-Coaches.
Du sprichst mit dem Kunden auf Deutsch, locker und motivierend, per Du.

AUFGABE: Analysiere das Foto, das der Kunde geschickt hat.

DATENSCHUTZ — STRIKT:
- Du analysierst AUSSCHLIESSLICH Fotos von Essen und Getränken.
- Bei Personen, Selfies, Körperfotos, Screenshots, Memes, Landschaften, Tieren oder anderen Privatfotos: höflich ablehnen, KEINE Beschreibung der Person/des Inhalts geben.
- Im Zweifel lieber ablehnen als raten.

Antworte IMMER ausschließlich mit einem JSON-Block, nichts davor, nichts danach:

Fall A — das Foto zeigt eindeutig Essen oder Getränke:
```json
{
  "type": "food_log",
  "meal_type": "fruehstueck" | "mittag" | "abend" | "snack",
  "items": [
    {"item": "Spiegelei", "qty": 2, "unit": "Stk", "kcal": 156, "protein_g": 13.0, "carbs_g": 1.0, "fat_g": 11.0},
    {"item": "Vollkornbrot", "qty": 1, "unit": "Scheibe", "kcal": 80, "protein_g": 3.0, "carbs_g": 14.0, "fat_g": 1.0}
  ],
  "reply": "Kurze freundliche Bestätigung, was du erkannt hast, 1-2 Sätze auf Deutsch."
}
```

Regeln für food_log:
- Identifiziere alle sichtbaren Speisen und Getränke
- Schätze Mengen aus Tellergröße/Vergleichsobjekten realistisch
- Wenn der Kunde eine Caption mitgeschickt hat (z.B. "ca. 200g Reis"), nutze sie als Hilfe
- meal_type aus Kontext (Caption, sichtbare Speisenart)

Fall B — das Foto zeigt KEIN Essen (Person, Selfie, Körper, Landschaft, Screenshot, Meme, etc.):
```json
{
  "type": "rejected",
  "reply": "Hey, ich kann nur Fotos von Essen oder Getränken analysieren. Schick mir gern ein Foto von deiner nächsten Mahlzeit! 🍽️"
}
```

Fall C — Foto zu unklar / dunkel / verschwommen:
```json
{
  "type": "unclear",
  "reply": "Ich erkenne auf dem Foto leider nicht eindeutig, was drauf ist — kannst du es nochmal bei besserem Licht aufnehmen oder mir kurz schreiben, was es war?"
}
```

WICHTIG: Antworte ausschließlich mit gültigem JSON. Keine Erklärungen davor/dahinter.
"""


async def handle(customer: dict, text: str) -> None:
    """Main entry: analyze message, route to food_log or chat."""
    history = db.recent_messages(customer["id"], limit=10)
    # Strip the just-logged user message from history
    history = [h for h in history if not (h["direction"] == "in" and h["content"] == text)]

    messages = claude_client.build_messages_from_history(history, text)
    reply_raw, tokens, model = await claude_client.ask(
        SYSTEM_PROMPT, messages, max_tokens=800
    )

    parsed = _parse_json(reply_raw)
    if parsed is None:
        # Fallback: send raw reply as chat (Claude-generiert → escapen)
        await _send_and_log(
            customer, telegram_agent.escape_html(reply_raw), "food_log", model, tokens
        )
        return

    if parsed.get("type") == "food_log":
        await _handle_food_log(customer, parsed, model, tokens)
    else:
        # chat or checkin_hint — just relay the reply (Claude-generiert → escapen)
        await _send_and_log(
            customer,
            telegram_agent.escape_html(parsed.get("reply", "…")),
            "food_log",
            model,
            tokens,
        )


async def handle_photo(
    customer: dict,
    photo_bytes: bytes,
    media_type: str,
    caption: str | None = None,
) -> None:
    """Analyze a food photo with Claude Vision and log it like a text food entry."""
    image_b64 = base64.b64encode(photo_bytes).decode("ascii")

    user_text = "Hier ist mein Essen — bitte analysieren."
    if caption:
        user_text = f"Hier ist mein Essen. Hinweis vom Kunden: {caption}"

    reply_raw, tokens, model = await claude_client.ask_with_image(
        PHOTO_SYSTEM_PROMPT,
        image_b64,
        media_type,
        user_text,
        max_tokens=900,
    )

    parsed = _parse_json(reply_raw)
    if parsed is None:
        await _send_and_log(
            customer,
            "Ich konnte dein Foto leider nicht analysieren — schick es nochmal oder beschreib es kurz in Worten.",
            "food_log_photo",
            model,
            tokens,
        )
        return

    ptype = parsed.get("type")
    if ptype == "food_log":
        await _handle_food_log(customer, parsed, model, tokens, agent_name="food_log_photo")
    else:
        # rejected / unclear / anything else → just relay reply (Claude-generiert → escapen)
        await _send_and_log(
            customer,
            telegram_agent.escape_html(parsed.get("reply", "…")),
            "food_log_photo",
            model,
            tokens,
        )


# ------------------------------------------------------------------
# Internals
# ------------------------------------------------------------------
def _parse_json(raw: str) -> dict | None:
    """Extract JSON block from Claude's reply."""
    # Try fenced block first
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidate = match.group(1) if match else raw.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


async def _handle_food_log(
    customer: dict,
    parsed: dict,
    model: str,
    tokens: int,
    agent_name: str = "food_log",
) -> None:
    items = parsed.get("items", [])
    total_kcal = int(sum(i.get("kcal", 0) for i in items))
    protein = round(sum(float(i.get("protein_g", 0)) for i in items), 1)
    carbs = round(sum(float(i.get("carbs_g", 0)) for i in items), 1)
    fat = round(sum(float(i.get("fat_g", 0)) for i in items), 1)

    # Write food_log row
    raw_desc = ", ".join(
        f"{i.get('qty', '')} {i.get('unit', '')} {i.get('item', '')}".strip()
        for i in items
    )
    db.db().table("food_logs").insert(
        {
            "customer_id": customer["id"],
            "meal_type": parsed.get("meal_type", "snack"),
            "raw_description": raw_desc,
            "parsed_items": items,
            "total_kcal": total_kcal,
            "protein_g": protein,
            "carbs_g": carbs,
            "fat_g": fat,
        }
    ).execute()

    # Build reply: Claude's base reply + today's totals.
    # base_reply is Claude-generated → escape; the totals line is numeric/safe.
    base_reply = telegram_agent.escape_html(parsed.get("reply", "✅ Geloggt!"))
    totals_line = _build_totals_line(customer)
    full_reply = f"{base_reply}\n\n<b>Heute bisher:</b> {totals_line}"

    await _send_and_log(customer, full_reply, agent_name, model, tokens)


def _build_totals_line(customer: dict) -> str:
    """Sum today's food_logs and format vs. targets if set."""
    today_start = datetime.combine(date.today(), datetime.min.time(), tzinfo=TZ)
    resp = (
        db.db()
        .table("food_logs")
        .select("total_kcal, protein_g, carbs_g, fat_g")
        .eq("customer_id", customer["id"])
        .gte("logged_at", today_start.isoformat())
        .execute()
    )
    kcal = sum(r["total_kcal"] or 0 for r in resp.data)
    protein = round(sum(float(r["protein_g"] or 0) for r in resp.data), 1)
    carbs = round(sum(float(r["carbs_g"] or 0) for r in resp.data), 1)
    fat = round(sum(float(r["fat_g"] or 0) for r in resp.data), 1)

    profile = (customer.get("customer_profiles") or [{}])
    profile = profile[0] if isinstance(profile, list) and profile else (profile or {})
    kcal_target = profile.get("daily_kcal_target")

    if kcal_target:
        pct = int(100 * kcal / kcal_target) if kcal_target else 0
        remaining = max(kcal_target - kcal, 0)
        return (
            f"{kcal}/{kcal_target} kcal ({pct}%) · "
            f"{protein}g P · {carbs}g KH · {fat}g F · "
            f"noch {remaining} kcal"
        )
    return f"{kcal} kcal · {protein}g P · {carbs}g KH · {fat}g F"


async def _send_and_log(
    customer: dict, text: str, agent_name: str, model: str, tokens: int
) -> None:
    await telegram_agent.send_message(customer["telegram_chat_id"], text)
    db.log_message(
        customer["id"], "out", text,
        agent_name=agent_name, model_used=model, tokens_used=tokens,
    )
