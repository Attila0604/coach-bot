"""Food-Log-Agent: parses free-text meal descriptions and food photos into structured entries.

Also handles general chat in 'active' state — one Claude call classifies the
intent and returns either a food_log JSON or a chat reply.

V2: Customer language aware (de / hu / it) via customer_profiles.language.
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
        "label": "Deutsch",
        "tone": "Deutsch, locker und motivierend, per Du",
        "reply_language": "Deutsch",
        "photo_rejected": (
            "Hey, ich kann nur Fotos von Essen oder Getränken analysieren. "
            "Schick mir gern ein Foto von deiner nächsten Mahlzeit! 🍽️"
        ),
        "photo_unclear": (
            "Ich erkenne auf dem Foto leider nicht eindeutig, was drauf ist — "
            "kannst du es nochmal bei besserem Licht aufnehmen oder mir kurz schreiben, was es war?"
        ),
        "photo_failed": (
            "Ich konnte dein Foto leider nicht analysieren — schick es nochmal "
            "oder beschreib es kurz in Worten."
        ),
        "totals_heading": "Heute bisher",
        "remaining": "noch {remaining} kcal",
        "macros": "{protein}g P · {carbs}g KH · {fat}g F",
        "logged_default": "✅ Geloggt!",
    },
    "hu": {
        "label": "Ungarisch",
        "tone": "Ungarisch, tegeződve, lazán és motiválóan",
        "reply_language": "Ungarisch",
        "photo_rejected": (
            "Szia, csak ételekről és italokról készült fotókat tudok elemezni. "
            "Küldj nyugodtan egy képet a következő étkezésedről! 🍽️"
        ),
        "photo_unclear": (
            "Sajnos nem látom elég egyértelműen, mi van a képen — "
            "le tudnád fotózni jobb fényben, vagy röviden leírni, mit ettél?"
        ),
        "photo_failed": (
            "Sajnos nem sikerült elemeznem a fotót — küldd el újra, "
            "vagy írd le röviden szövegben, mit ettél."
        ),
        "totals_heading": "Ma eddig",
        "remaining": "még {remaining} kcal",
        "macros": "{protein}g fehérje · {carbs}g szénhidrát · {fat}g zsír",
        "logged_default": "✅ Rögzítve!",
    },
    "it": {
        "label": "Italienisch",
        "tone": "Italienisch, in modo informale, motivante e dando del tu",
        "reply_language": "Italienisch",
        "photo_rejected": (
            "Ehi, posso analizzare solo foto di cibo o bevande. "
            "Mandami pure una foto del tuo prossimo pasto! 🍽️"
        ),
        "photo_unclear": (
            "Purtroppo non riesco a capire chiaramente cosa c'è nella foto — "
            "puoi rifarla con più luce o scrivermi brevemente cosa hai mangiato?"
        ),
        "photo_failed": (
            "Purtroppo non sono riuscito ad analizzare la foto — inviala di nuovo "
            "oppure descrivimi brevemente il pasto a parole."
        ),
        "totals_heading": "Oggi finora",
        "remaining": "ancora {remaining} kcal",
        "macros": "{protein}g P · {carbs}g C · {fat}g G",
        "logged_default": "✅ Registrato!",
    },
}


def _customer_language(customer: dict) -> str:
    """Return normalized customer language from embedded customer_profiles."""
    profile = customer.get("customer_profiles") or [{}]
    profile = profile[0] if isinstance(profile, list) and profile else (profile or {})
    language = (profile.get("language") or "de").lower()
    return language if language in LANGUAGE_CONFIG else "de"


def _language_config(customer: dict) -> dict:
    return LANGUAGE_CONFIG[_customer_language(customer)]


def _build_system_prompt(language: str) -> str:
    cfg = LANGUAGE_CONFIG.get(language, LANGUAGE_CONFIG["de"])
    reply_language = cfg["reply_language"]
    tone = cfg["tone"]

    return f"""Du bist der persönliche Assistent eines Ernährungs- und Fitness-Coaches.
Du sprichst mit dem Kunden direkt auf {tone}.

AUFGABE: Analysiere die Nachricht des Kunden und entscheide, um was es geht.

WICHTIG ZUR SPRACHE:
- Die sichtbare Antwort an den Kunden muss auf {reply_language} sein.
- Die JSON-Schlüssel bleiben exakt gleich.
- meal_type MUSS trotzdem einer dieser deutschen Datenbank-Werte sein:
  "fruehstueck", "mittag", "abend", "snack".

Antworte IMMER ausschließlich mit einem JSON-Block, nichts davor, nichts danach:

Fall A — der Kunde beschreibt etwas, das er gegessen oder getrunken hat:
```json
{{
  "type": "food_log",
  "meal_type": "fruehstueck" | "mittag" | "abend" | "snack",
  "items": [
    {{"item": "Ei", "qty": 2, "unit": "Stk", "kcal": 156, "protein_g": 13.0, "carbs_g": 1.0, "fat_g": 11.0}},
    {{"item": "Vollkorntoast", "qty": 1, "unit": "Scheibe", "kcal": 80, "protein_g": 3.0, "carbs_g": 14.0, "fat_g": 1.0}}
  ],
  "reply": "Kurze, freundliche Bestätigung auf {reply_language}, 1-2 Sätze."
}}
