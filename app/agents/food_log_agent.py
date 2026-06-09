"""Food-Log-Agent: parses free-text meal descriptions and food photos into structured entries.

Also handles general chat in 'active' state. The agent is customer-language aware
via customer_profiles.language: de / hu / it.
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

LANGUAGE_CONFIG = {
    "de": {
        "reply_language": "Deutsch",
        "tone": "Deutsch, locker und motivierend, per Du",
        "photo_failed": "Ich konnte dein Foto leider nicht analysieren — schick es nochmal oder beschreib es kurz in Worten.",
        "totals_heading": "Heute bisher",
        "remaining": "noch {remaining} kcal",
        "macros": "{protein}g P · {carbs}g KH · {fat}g F",
        "logged_default": "✅ Geloggt!",
    },
    "hu": {
        "reply_language": "Ungarisch",
        "tone": "Ungarisch, tegeződve, lazán és motiválóan",
        "photo_failed": "Sajnos nem sikerült elemeznem a fotót — küldd el újra, vagy írd le röviden, mit ettél.",
        "totals_heading": "Ma eddig",
        "remaining": "még {remaining} kcal",
        "macros": "{protein}g fehérje · {carbs}g szénhidrát · {fat}g zsír",
        "logged_default": "✅ Rögzítve!",
    },
    "it": {
        "reply_language": "Italienisch",
        "tone": "Italienisch, in modo informale, motivante e dando del tu",
        "photo_failed": "Purtroppo non sono riuscito ad analizzare la foto — inviala di nuovo oppure descrivimi brevemente il pasto.",
        "totals_heading": "Oggi finora",
        "remaining": "ancora {remaining} kcal",
        "macros": "{protein}g P · {carbs}g C · {fat}g G",
        "logged_default": "✅ Registrato!",
    },
}


def _customer_language(customer: dict) -> str:
    profile = customer.get("customer_profiles") or [{}]
    profile = profile[0] if isinstance(profile, list) and profile else (profile or {})
    language = str(profile.get("language") or "de").lower()
    return language if language in LANGUAGE_CONFIG else "de"


def _language_config(customer: dict) -> dict:
    return LANGUAGE_CONFIG[_customer_language(customer)]


def _build_system_prompt(language: str) -> str:
    cfg = LANGUAGE_CONFIG.get(language, LANGUAGE_CONFIG["de"])
    reply_language = cfg["reply_language"]
    tone = cfg["tone"]

    return f"""Du bist der persönliche Assistent eines Ernährungs- und Fitness-Coaches.
Du sprichst mit dem Kunden direkt auf {tone}.

AUFGABE:
Analysiere die Nachricht des Kunden und entscheide, ob es um Essen-Logging, normalen Chat oder Check-in-Daten geht.

SPRACHE:
- Die sichtbare Antwort an den Kunden muss auf {reply_language} sein.
- Die JSON-Schlüssel bleiben exakt gleich.
- meal_type muss für die Datenbank trotzdem einer dieser Werte sein: fruehstueck, mittag, abend, snack.

ANTWORTE AUSSCHLIESSLICH MIT VALIDEM JSON.
Kein Markdown. Kein Vortext. Keine Erklärung.

Fall A — Essen oder Getränk:
{{
  "type": "food_log",
  "meal_type": "fruehstueck",
  "items": [
    {{"item": "Ei", "qty": 2, "unit": "Stk", "kcal": 156, "protein_g": 13.0, "carbs_g": 1.0, "fat_g": 11.0}}
  ],
  "reply": "Kurze freundliche Bestätigung auf {reply_language}."
}}

Fall B — normale Frage, Plauderei, Stimmung, Motivation:
{{
  "type": "chat",
  "reply": "Antwort auf {reply_language}."
}}

Fall C — Check-in-Daten wie Gewicht, Maße, Schlaf, Stimmung:
{{
  "type": "checkin_hint",
  "reply": "Antwort auf {reply_language} mit kurzem Hinweis, dass Check-ins über den wöchentlichen Dialog laufen."
}}

Regeln:
- Kalorien und Makros realistisch schätzen.
- Bei unklarer Menge eine vernünftige Durchschnitts-Annahme treffen.
- item und unit dürfen in der Kundensprache sein.
- kcal, protein_g, carbs_g, fat_g müssen Zahlen sein.
"""


def _build_photo_system_prompt(language: str) -> str:
    cfg = LANGUAGE_CONFIG.get(language, LANGUAGE_CONFIG["de"])
    reply_language = cfg["reply_language"]
    tone = cfg["tone"]

    return f"""Du bist der persönliche Assistent eines Ernährungs- und Fitness-Coaches.
Du sprichst mit dem Kunden direkt auf {tone}.

AUFGABE:
Analysiere das Foto des Kunden.

DATENSCHUTZ:
- Analysiere ausschließlich Fotos von Essen und Getränken.
- Bei Personen, Selfies, Körperfotos, Screenshots, Memes, Landschaften, Tieren oder anderen Privatfotos: höflich ablehnen.
- Beschreibe keine Personen und keine privaten Bildinhalte.

SPRACHE:
- Die sichtbare Antwort an den Kunden muss auf {reply_language} sein.
- Die JSON-Schlüssel bleiben exakt gleich.
- meal_type muss für die Datenbank trotzdem einer dieser Werte sein: fruehstueck, mittag, abend, snack.

ANTWORTE AUSSCHLIESSLICH MIT VALIDEM JSON.
Kein Markdown. Kein Vortext. Keine Erklärung.

Fall A — Foto zeigt Essen oder Getränke:
{{
  "type": "food_log",
  "meal_type": "mittag",
  "items": [
    {{"item": "Reis", "qty": 200, "unit": "g", "kcal": 260, "protein_g": 5.0, "carbs_g": 56.0, "fat_g": 1.0}}
  ],
  "reply": "Kurze freundliche Bestätigung auf {reply_language}."
}}

Fall B — Foto zeigt kein Essen:
{{
  "type": "rejected",
  "reply": "Höfliche Ablehnung auf {reply_language}: Ich kann nur Essen oder Getränke analysieren."
}}

Fall C — Foto ist unklar:
{{
  "type": "unclear",
  "reply": "Kurze Bitte auf {reply_language}, das Essen noch einmal besser zu fotografieren oder kurz zu beschreiben."
}}
"""


async def handle(customer: dict, text: str) -> None:
    """Main entry: analyze message, route to food_log or chat."""
    language = _customer_language(customer)
    system_prompt = _build_system_prompt(language)

    history = db.recent_messages(customer["id"], limit=10)
    history = [h for h in history if not (h["direction"] == "in" and h["content"] == text)]

    messages = claude_client.build_messages_from_history(history, text)
    reply_raw, tokens, model = await claude_client.ask(system_prompt, messages, max_tokens=800)

    parsed = _parse_json(reply_raw)
    if parsed is None:
        await _send_and_log(customer, telegram_agent.escape_html(reply_raw), "food_log", model, tokens)
        return

    if parsed.get("type") == "food_log":
        await _handle_food_log(customer, parsed, model, tokens)
        return

    await _send_and_log(
        customer,
        telegram_agent.escape_html(parsed.get("reply", "…")),
        "food_log",
        model,
        tokens,
    )


async def handle_photo(customer: dict, photo_bytes: bytes, media_type: str, caption: str | None = None) -> None:
    """Analyze a food photo with Claude Vision and log it like a text food entry."""
    language = _customer_language(customer)
    cfg = LANGUAGE_CONFIG.get(language, LANGUAGE_CONFIG["de"])
    image_b64 = base64.b64encode(photo_bytes).decode("ascii")

    if language == "hu":
        user_text = "Itt az ételem — kérlek elemezd."
    elif language == "it":
        user_text = "Ecco il mio pasto — per favore analizzalo."
    else:
        user_text = "Hier ist mein Essen — bitte analysieren."

    if caption:
        user_text = f"{user_text}\nHinweis / note: {caption}"

    reply_raw, tokens, model = await claude_client.ask_with_image(
        _build_photo_system_prompt(language),
        image_b64,
        media_type,
        user_text,
        max_tokens=900,
    )

    parsed = _parse_json(reply_raw)
    if parsed is None:
        await _send_and_log(customer, cfg["photo_failed"], "food_log_photo", model, tokens)
        return

    if parsed.get("type") == "food_log":
        await _handle_food_log(customer, parsed, model, tokens, agent_name="food_log_photo")
        return

    await _send_and_log(
        customer,
        telegram_agent.escape_html(parsed.get("reply", "…")),
        "food_log_photo",
        model,
        tokens,
    )


def _parse_json(raw: str) -> dict | None:
    """Extract JSON from Claude's reply."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidate = match.group(1) if match else raw.strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    first = candidate.find("{")
    last = candidate.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(candidate[first:last + 1])
        except json.JSONDecodeError:
            return None

    return None


async def _handle_food_log(customer: dict, parsed: dict, model: str, tokens: int, agent_name: str = "food_log") -> None:
    items = parsed.get("items", [])
    total_kcal = int(sum(i.get("kcal", 0) for i in items))
    protein = round(sum(float(i.get("protein_g", 0)) for i in items), 1)
    carbs = round(sum(float(i.get("carbs_g", 0)) for i in items), 1)
    fat = round(sum(float(i.get("fat_g", 0)) for i in items), 1)

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

    cfg = _language_config(customer)
    base_reply = telegram_agent.escape_html(parsed.get("reply", cfg["logged_default"]))
    totals_line = _build_totals_line(customer)
    full_reply = f"{base_reply}\n\n<b>{cfg['totals_heading']}:</b> {totals_line}"

    await _send_and_log(customer, full_reply, agent_name, model, tokens)


def _build_totals_line(customer: dict) -> str:
    """Sum today's food_logs and format vs. targets if set."""
    cfg = _language_config(customer)
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

    profile = customer.get("customer_profiles") or [{}]
    profile = profile[0] if isinstance(profile, list) and profile else (profile or {})
    kcal_target = profile.get("daily_kcal_target")

    macro_line = cfg["macros"].format(protein=protein, carbs=carbs, fat=fat)

    if kcal_target:
        pct = int(100 * kcal / kcal_target) if kcal_target else 0
        remaining = max(kcal_target - kcal, 0)
        remaining_text = cfg["remaining"].format(remaining=remaining)
        return f"{kcal}/{kcal_target} kcal ({pct}%) · {macro_line} · {remaining_text}"

    return f"{kcal} kcal · {macro_line}"


async def _send_and_log(customer: dict, text: str, agent_name: str, model: str, tokens: int) -> None:
    await telegram_agent.send_message(customer["telegram_chat_id"], text)
    db.log_message(
        customer["id"],
        "out",
        text,
        agent_name=agent_name,
        model_used=model,
        tokens_used=tokens,
    )
