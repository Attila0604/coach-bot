"""Food-Log-Agent.

Kann:
- Essen per Text loggen
- Essensfoto analysieren
- Deutsch / Ungarisch / Italienisch antworten
- heutigen Trainingsplan anzeigen
- heutigen Ernährungsplan anzeigen
- freundlichere Antworten geben
- Lebensmittel-Ersatzvorschläge machen
"""
import base64
import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import db
from app.agents import telegram_agent
from app.config import settings
from app.services import claude_client

TZ = ZoneInfo(settings.TZ)


LANG = {
    "de": {
        "reply_language": "Deutsch",
        "tone": "Deutsch, freundlich, motivierend, persönlich, per Du, kurz und nicht kindisch",
        "logged": "✅ Geloggt!",
        "today_total": "Heute bisher",
        "remaining": "noch {remaining} kcal",
        "macros": "{protein}g P · {carbs}g KH · {fat}g F",

        "training_title": "Dein Training heute",
        "no_training_plan": "Ich finde aktuell keinen aktiven Trainingsplan für dich.",
        "no_training_today": "Heute ist kein Training geplant. Erhol dich gut. 😴",
        "no_exercises": "Für heute sind noch keine Übungen eingetragen.",
        "sets": "Sätze",
        "reps": "Wdh.",
        "rest": "Pause",

        "nutrition_title": "Dein Ernährungsplan",
        "today": "Heute",
        "next_plan": "Nächster veröffentlichter Plan",
        "no_meal_plan": "Ich finde aktuell keinen veröffentlichten Ernährungsplan für dich.",
        "no_meals": "In diesem Ernährungsplan sind noch keine Mahlzeiten eingetragen.",
        "total": "Gesamt",
        "protein": "Protein",
        "carbs": "Kohlenhydrate",
        "fat": "Fett",
        "note": "Notiz",
        "ask_coach": "Wenn das nicht stimmt, melde dich bitte kurz bei deinem Coach.",
        "photo_failed": "Ich konnte dein Foto leider nicht analysieren — schick es nochmal oder beschreib es kurz.",
        "meal_labels": {
            "fruehstueck": "Frühstück",
            "breakfast": "Frühstück",
            "mittag": "Mittagessen",
            "lunch": "Mittagessen",
            "abend": "Abendessen",
            "dinner": "Abendessen",
            "snack": "Snack",
            "snacks": "Snacks",
        },
    },
    "hu": {
        "reply_language": "Ungarisch",
        "tone": "Ungarisch, tegeződve, barátságosan, motiválóan, személyesen, röviden és nem gyerekesen",
        "logged": "✅ Rögzítve!",
        "today_total": "Ma eddig",
        "remaining": "még {remaining} kcal",
        "macros": "{protein}g fehérje · {carbs}g szénhidrát · {fat}g zsír",

        "training_title": "Mai edzésed",
        "no_training_plan": "Jelenleg nem találok aktív edzéstervet neked.",
        "no_training_today": "Ma nincs edzés tervezve. Pihend ki magad. 😴",
        "no_exercises": "A mai naphoz még nincsenek gyakorlatok megadva.",
        "sets": "sorozat",
        "reps": "ism.",
        "rest": "pihenő",

        "nutrition_title": "Étrended",
        "today": "Ma",
        "next_plan": "Következő közzétett terv",
        "no_meal_plan": "Jelenleg nem találok közzétett étrendet neked.",
        "no_meals": "Ebben az étrendben még nincsenek étkezések megadva.",
        "total": "Összesen",
        "protein": "Fehérje",
        "carbs": "Szénhidrát",
        "fat": "Zsír",
        "note": "Megjegyzés",
        "ask_coach": "Ha ez nem stimmel, kérlek jelezd az edződnek.",
        "photo_failed": "Sajnos nem sikerült elemeznem a fotót — küldd el újra, vagy írd le röviden.",
        "meal_labels": {
            "fruehstueck": "Reggeli",
            "breakfast": "Reggeli",
            "mittag": "Ebéd",
            "lunch": "Ebéd",
            "abend": "Vacsora",
            "dinner": "Vacsora",
            "snack": "Snack",
            "snacks": "Snackek",
        },
    },
    "it": {
        "reply_language": "Italienisch",
        "tone": "Italienisch, informale, amichevole, motivante, personale, breve e non infantile",
        "logged": "✅ Registrato!",
        "today_total": "Oggi finora",
        "remaining": "ancora {remaining} kcal",
        "macros": "{protein}g P · {carbs}g C · {fat}g G",

        "training_title": "Il tuo allenamento di oggi",
        "no_training_plan": "Al momento non trovo un piano di allenamento attivo per te.",
        "no_training_today": "Oggi non hai allenamento programmato. Recupera bene. 😴",
        "no_exercises": "Per oggi non sono ancora stati inseriti esercizi.",
        "sets": "serie",
        "reps": "rip.",
        "rest": "pausa",

        "nutrition_title": "Il tuo piano alimentare",
        "today": "Oggi",
        "next_plan": "Prossimo piano pubblicato",
        "no_meal_plan": "Al momento non trovo un piano alimentare pubblicato per te.",
        "no_meals": "In questo piano alimentare non ci sono ancora pasti inseriti.",
        "total": "Totale",
        "protein": "Proteine",
        "carbs": "Carboidrati",
        "fat": "Grassi",
        "note": "Nota",
        "ask_coach": "Se non è corretto, scrivi al tuo coach.",
        "photo_failed": "Purtroppo non sono riuscito ad analizzare la foto — inviala di nuovo o descrivimi il pasto.",
        "meal_labels": {
            "fruehstueck": "Colazione",
            "breakfast": "Colazione",
            "mittag": "Pranzo",
            "lunch": "Pranzo",
            "abend": "Cena",
            "dinner": "Cena",
            "snack": "Spuntino",
            "snacks": "Spuntini",
        },
    },
}


TRAINING_WORDS = [
    "was trainiere ich heute",
    "training heute",
    "heutiges training",
    "trainingsplan",
    "trainings plan",
    "was soll ich heute trainieren",
    "übungen heute",
    "uebungen heute",
    "mit edzek ma",
    "mit kell ma edzenem",
    "mai edzés",
    "mai edzes",
    "edzés ma",
    "edzes ma",
    "edzésterv",
    "edzesterv",
    "gyakorlatok ma",
    "allenamento oggi",
    "che allenamento ho oggi",
    "cosa devo allenare oggi",
    "esercizi oggi",
    "training today",
    "workout today",
]

NUTRITION_WORDS = [
    "was soll ich heute essen",
    "was esse ich heute",
    "was esse ich",
    "ernährungsplan",
    "ernaehrungsplan",
    "ernährung",
    "ernaehrung",
    "essensplan",
    "speiseplan",
    "mahlzeitenplan",
    "mahlzeiten heute",
    "essen heute",
    "mit egyek ma",
    "mi a mai étrendem",
    "mi a mai etrendem",
    "mai étrend",
    "mai etrend",
    "étrend ma",
    "etrend ma",
    "étrend",
    "etrend",
    "étrendem",
    "etrendem",
    "diéta",
    "dieta",
    "étlap",
    "etlap",
    "mit kell ennem ma",
    "cosa devo mangiare oggi",
    "piano alimentare",
    "cosa mangio oggi",
    "pasti di oggi",
    "pasti",
    "dieta di oggi",
    "menu",
    "menü",
    "meal plan",
    "nutrition plan",
    "diet plan",
    "what should i eat today",
]

SUBSTITUTION_WORDS = [
    # Deutsch — nur klare Ersatz-Marker
    "statt",
    "anstatt",
    "ersetzen",
    "austauschen",
    "alternative",
    # Ungarisch
    "helyett",
    "kiváltani",
    "cserélni",
    "csere",
    "alternatíva",
    # Italienisch
    "invece",
    "sostituire",
    "alternativa",
    "al posto di",
    # English
    "instead",
    "replace",
    "swap",
]


def _profile(customer: dict) -> dict:
    profile = customer.get("customer_profiles") or [{}]
    if isinstance(profile, list):
        return profile[0] if profile else {}
    return profile or {}


def _language(customer: dict) -> str:
    lang = str(_profile(customer).get("language") or "de").lower()
    return lang if lang in LANG else "de"


def _cfg(customer: dict) -> dict:
    return LANG[_language(customer)]


def _customer_name(customer: dict) -> str:
    return str(customer.get("first_name") or "").strip()


def _norm(text: str) -> str:
    return text.lower().strip().replace("?", "").replace("!", "").replace(".", "")


def _contains(text: str, words: list[str]) -> bool:
    n = _norm(text)
    return any(w in n for w in words)


def _today() -> str:
    return datetime.now(TZ).date().isoformat()


def _num(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value.is_integer():
        return int(value)
    return round(value, 1)


def _claude_prompt(lang: str, customer_name: str = "") -> str:
    c = LANG.get(lang, LANG["de"])
    name_hint = f"Der Kunde heißt {customer_name}. Nutze den Namen gelegentlich, aber nicht in jeder Antwort." if customer_name else ""

    return f"""Du bist ein freundlicher Ernährungs- und Fitness-Assistent.
Sprich mit dem Kunden auf {c['tone']}.
{name_hint}

Antworte IMMER nur als gültiges JSON.

Wenn der Kunde Essen oder Trinken beschreibt:
{{
  "type": "food_log",
  "meal_type": "fruehstueck",
  "items": [
    {{"item": "Ei", "qty": 2, "unit": "Stk", "kcal": 156, "protein_g": 13, "carbs_g": 1, "fat_g": 11}}
  ],
  "reply": "Kurze, freundliche, motivierende Antwort auf {c['reply_language']}."
}}

Wenn es normale Unterhaltung ist:
{{
  "type": "chat",
  "reply": "Freundliche kurze Antwort auf {c['reply_language']}."
}}

Wenn es Check-in-Daten sind:
{{
  "type": "checkin_hint",
  "reply": "Kurzer Hinweis auf {c['reply_language']}, dass Check-ins über den wöchentlichen Dialog laufen."
}}

Regeln:
- meal_type muss einer dieser Werte sein: fruehstueck, mittag, abend, snack
- Kalorien und Makros realistisch schätzen
- Reply freundlich, menschlich, motivierend, aber kurz
- nicht übertreiben, keine langen Romane
- nur JSON, kein Markdown
"""


def _photo_prompt(lang: str, customer_name: str = "") -> str:
    c = LANG.get(lang, LANG["de"])
    name_hint = f"Der Kunde heißt {customer_name}. Nutze den Namen gelegentlich, aber nicht in jeder Antwort." if customer_name else ""

    return f"""Du bist ein freundlicher Ernährungsassistent.
Analysiere nur Fotos von Essen oder Getränken.
Bei Personen, Selfies, Körperfotos, Screenshots oder anderen privaten Bildern: ablehnen.
Sprich auf {c['reply_language']}.
{name_hint}

Antworte nur als gültiges JSON.

Essen:
{{
  "type": "food_log",
  "meal_type": "mittag",
  "items": [
    {{"item": "Reis", "qty": 200, "unit": "g", "kcal": 260, "protein_g": 5, "carbs_g": 56, "fat_g": 1}}
  ],
  "reply": "Kurze, freundliche, motivierende Antwort auf {c['reply_language']}."
}}

Kein Essen:
{{
  "type": "rejected",
  "reply": "Kurze höfliche Ablehnung auf {c['reply_language']}."
}}

Unklar:
{{
  "type": "unclear",
  "reply": "Bitte auf {c['reply_language']}, das Essen besser zu fotografieren oder zu beschreiben."
}}
"""


def _substitution_prompt(customer: dict, user_text: str, meal_context: str) -> str:
    lang = _language(customer)
    c = _cfg(customer)
    name = _customer_name(customer)
    name_hint = f"Der Kunde heißt {name}. Nutze den Namen natürlich, aber nicht übertrieben." if name else ""

    return f"""Du bist ein freundlicher Ernährungscoach.
Sprich mit dem Kunden auf {c['tone']}.
{name_hint}

Der Kunde fragt nach einem Lebensmittel-Ersatz oder einer Alternative.

Kundenfrage:
{user_text}

Aktueller Ernährungsplan-Kontext, falls vorhanden:
{meal_context}

Aufgabe:
- Antworte direkt und hilfreich.
- Gib eine praktische Ersatzmenge, wenn möglich.
- Achte auf ähnliche Kalorien und ähnliche Makros.
- Wenn Öl, Sauce, Käse, Zucker oder Nüsse relevant sind, weise kurz darauf hin.
- Bleibe freundlich, motivierend und kurz.
- Keine medizinischen Versprechen.
- Keine lange Tabelle.
- Antwort nur als normaler Text auf {c['reply_language']}.
"""


PLAN_WORDS_EXACT = {"plan", "mein plan", "terv", "tervem", "piano", "il piano"}


async def handle(customer: dict, text: str) -> None:
    """Main entry."""
    # Bloßes "plan" (ohne Kontext) -> Ernährungsplan (häufigste Tagesabfrage).
    if _norm(text) in PLAN_WORDS_EXACT:
        await _handle_nutrition(customer)
        return

    if _contains(text, SUBSTITUTION_WORDS):
        await _handle_substitution(customer, text)
        return

    if _contains(text, NUTRITION_WORDS):
        await _handle_nutrition(customer)
        return

    if _contains(text, TRAINING_WORDS):
        await _handle_training(customer)
        return

    lang = _language(customer)
    name = _customer_name(customer)

    history = db.recent_messages(customer["id"], limit=10)
    history = [h for h in history if not (h["direction"] == "in" and h["content"] == text)]
    messages = claude_client.build_messages_from_history(history, text)

    reply_raw, tokens, model = await claude_client.ask(
        _claude_prompt(lang, name),
        messages,
        max_tokens=800,
    )

    parsed = _parse_json(reply_raw)

    if not parsed:
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
    """Analyze a food photo."""
    lang = _language(customer)
    c = _cfg(customer)
    name = _customer_name(customer)

    image_b64 = base64.b64encode(photo_bytes).decode("ascii")

    if lang == "hu":
        user_text = "Itt az ételem — kérlek elemezd."
    elif lang == "it":
        user_text = "Ecco il mio pasto — per favore analizzalo."
    else:
        user_text = "Hier ist mein Essen — bitte analysieren."

    if caption:
        user_text += f"\nHinweis / note: {caption}"

    reply_raw, tokens, model = await claude_client.ask_with_image(
        _photo_prompt(lang, name),
        image_b64,
        media_type,
        user_text,
        max_tokens=900,
    )

    parsed = _parse_json(reply_raw)

    if not parsed:
        await _send_and_log(customer, c["photo_failed"], "food_log_photo", model, tokens)
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


async def _handle_substitution(customer: dict, text: str) -> None:
    meal_context = _meal_plan_context(customer["id"])
    prompt = _substitution_prompt(customer, text, meal_context)

    reply, tokens, model = await claude_client.ask(
        prompt,
        [{"role": "user", "content": text}],
        max_tokens=450,
    )

    await _send_and_log(
        customer,
        telegram_agent.escape_html(reply),
        "substitution",
        model,
        tokens,
    )


def _meal_plan_context(customer_id: str) -> str:
    plan, is_today = _get_meal_plan(customer_id)

    if not plan:
        return "Kein veröffentlichter Ernährungsplan gefunden."

    meals = plan.get("meals") or []

    if not isinstance(meals, list) or not meals:
        return "Ein Plan ist vorhanden, aber ohne Mahlzeiten."

    lines = [f"Plan-Datum: {plan.get('plan_date')}"]

    for meal in meals:
        meal_type = meal.get("meal_type") or meal.get("type") or ""
        name = meal.get("name") or ""
        lines.append(f"- {meal_type}: {name}")

        items = meal.get("items") or []
        if isinstance(items, list):
            for item in items[:6]:
                food = item.get("food") or item.get("item") or item.get("name") or ""
                grams = item.get("grams")
                kcal = item.get("kcal")

                if food:
                    detail = f"  • {food}"
                    if grams:
                        detail += f" {grams}g"
                    if kcal:
                        detail += f" {kcal} kcal"
                    lines.append(detail)

    return "\n".join(lines[:40])


async def _handle_training(customer: dict) -> None:
    c = _cfg(customer)

    plan_resp = (
        db.db()
        .table("training_plans")
        .select("id, name, current_week, status, updated_at, translations")
        .eq("customer_id", customer["id"])
        .eq("status", "active")
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )

    if not plan_resp.data:
        await _send_and_log(customer, f"{c['no_training_plan']}\n\n{c['ask_coach']}", "training_today", "system", 0)
        return

    plan = plan_resp.data[0]
    weekday = datetime.now(TZ).weekday()

    days_resp = (
        db.db()
        .table("training_days")
        .select("id, title, subtitle, weekday, time_of_day, sort_order")
        .eq("plan_id", plan["id"])
        .order("sort_order", desc=False)
        .execute()
    )

    days = days_resp.data or []
    todays = [d for d in days if d.get("weekday") is not None and int(d["weekday"]) == weekday]

    if not todays:
        await _send_and_log(customer, c["no_training_today"], "training_today", "system", 0)
        return

    day = sorted(todays, key=lambda d: d.get("sort_order") or 0)[0]

    ex_resp = (
        db.db()
        .table("exercises")
        .select("id, name, sets, reps_min, reps_max, notes, rest_seconds, sort_order")
        .eq("day_id", day["id"])
        .order("sort_order", desc=False)
        .execute()
    )

    exercises = ex_resp.data or []
    plan, day, exercises = _overlay_training_translation(
        plan, day, exercises, _language(customer)
    )
    msg = _format_training(c, plan, day, exercises)

    await _send_and_log(customer, msg, "training_today", "system", 0)


def _overlay_training_translation(
    plan: dict, day: dict, exercises: list[dict], lang: str
) -> tuple[dict, dict, list[dict]]:
    """Legt translations[lang] über das deutsche Original (Fallback: Deutsch)."""
    if lang == "de":
        return plan, day, exercises

    tr = (plan.get("translations") or {}).get(lang)
    if not tr:
        return plan, day, exercises

    plan = {**plan, "name": tr.get("name") or plan.get("name")}

    day_tr = (tr.get("days") or {}).get(day.get("id")) or {}
    day = {
        **day,
        "title": day_tr.get("title") or day.get("title"),
        "subtitle": day_tr.get("subtitle") or day.get("subtitle"),
    }

    ex_tr = tr.get("exercises") or {}
    new_exercises = []
    for ex in exercises:
        e = ex_tr.get(ex.get("id")) or {}
        new_exercises.append(
            {
                **ex,
                "name": e.get("name") or ex.get("name"),
                "notes": e.get("notes") or ex.get("notes"),
            }
        )

    return plan, day, new_exercises


def _format_training(c: dict, plan: dict, day: dict, exercises: list[dict]) -> str:
    lines = [f"🏋️ <b>{c['training_title']}</b>", ""]

    plan_name = telegram_agent.escape_html(plan.get("name") or "")
    title = telegram_agent.escape_html(day.get("title") or "")
    subtitle = telegram_agent.escape_html(day.get("subtitle") or "")
    time_of_day = day.get("time_of_day")

    if plan_name:
        lines.append(f"<b>{plan_name}</b>")
    if title:
        lines.append(title)
    if subtitle:
        lines.append(f"<i>{subtitle}</i>")
    if time_of_day:
        lines.append(f"⏰ {telegram_agent.escape_html(str(time_of_day)[:5])}")

    lines.append("")

    if not exercises:
        lines.append(c["no_exercises"])
        return "\n".join(lines).strip()

    for i, ex in enumerate(exercises, start=1):
        name = telegram_agent.escape_html(ex.get("name") or "Übung")
        sets = ex.get("sets")
        reps = _format_reps(ex.get("reps_min"), ex.get("reps_max"))
        rest = ex.get("rest_seconds")
        notes = telegram_agent.escape_html(ex.get("notes") or "")

        detail = []
        if sets:
            detail.append(f"{sets} {c['sets']}")
        if reps:
            detail.append(f"{reps} {c['reps']}")
        if rest:
            detail.append(f"{rest}s {c['rest']}")

        line = f"{i}. <b>{name}</b>"
        if detail:
            line += " — " + " · ".join(detail)

        lines.append(line)

        if notes:
            lines.append(f"   <i>{notes}</i>")

    return "\n".join(lines).strip()


def _format_reps(reps_min, reps_max) -> str:
    if reps_min is None:
        return ""
    if reps_max is not None and reps_max != reps_min:
        return f"{reps_min}–{reps_max}"
    return str(reps_min)


async def _handle_nutrition(customer: dict) -> None:
    c = _cfg(customer)
    plan, is_today = _get_meal_plan(customer["id"])

    if not plan:
        await _send_and_log(customer, f"{c['no_meal_plan']}\n\n{c['ask_coach']}", "nutrition_plan", "system", 0)
        return

    # Übersetzung des Kunden über das deutsche Original legen (Fallback: Deutsch).
    lang = _language(customer)
    if lang != "de":
        tr = (plan.get("translations") or {}).get(lang)
        if isinstance(tr, list):
            plan = {**plan, "meals": tr}

    msg = _format_nutrition(c, plan, is_today)
    await _send_and_log(customer, msg, "nutrition_plan", "system", 0)


async def push_meal_plan(customer: dict) -> bool:
    """Proaktiver Push des aktuellen Ernährungsplans (z.B. wenn der Coach freigibt).

    Sendet nur, wenn der Kunde 'meal_plan_via_telegram' aktiviert hat und ein
    veröffentlichter Plan existiert. Gibt True zurück, wenn gesendet wurde.
    """
    profile = _profile(customer)
    if not profile.get("meal_plan_via_telegram"):
        return False
    if not customer.get("telegram_chat_id"):
        return False

    plan, is_today = _get_meal_plan(customer["id"])
    if not plan:
        return False

    c = _cfg(customer)
    lang = _language(customer)
    if lang != "de":
        tr = (plan.get("translations") or {}).get(lang)
        if isinstance(tr, list):
            plan = {**plan, "meals": tr}

    msg = _format_nutrition(c, plan, is_today)
    await _send_and_log(customer, msg, "nutrition_plan_push", "system", 0)
    return True


def _get_meal_plan(customer_id: str) -> tuple[dict | None, bool]:
    today = _today()
    until = (datetime.now(TZ).date() + timedelta(days=14)).isoformat()

    resp = (
        db.db()
        .table("meal_plans")
        .select("id, plan_date, meals, total_kcal, total_protein_g, total_carbs_g, total_fat_g, updated_at, translations")
        .eq("customer_id", customer_id)
        .eq("status", "published")
        .gte("plan_date", today)
        .lte("plan_date", until)
        .order("plan_date", desc=False)
        .order("updated_at", desc=True)
        .execute()
    )

    rows = resp.data or []

    if not rows:
        return None, False

    seen = set()
    plans = []

    for row in rows:
        d = row.get("plan_date")
        if d in seen:
            continue
        seen.add(d)
        plans.append(row)

    for plan in plans:
        if plan.get("plan_date") == today:
            return plan, True

    return plans[0], False


def _format_nutrition(c: dict, plan: dict, is_today: bool) -> str:
    plan_date = telegram_agent.escape_html(str(plan.get("plan_date") or ""))
    label = c["today"] if is_today else c["next_plan"]

    lines = [
        f"🥗 <b>{c['nutrition_title']}</b>",
        f"<b>{telegram_agent.escape_html(label)}</b> · {plan_date}",
        "",
    ]

    total_line = _format_total_line(c, plan)
    if total_line:
        lines.append(total_line)
        lines.append("")

    meals = plan.get("meals") or []
    if not isinstance(meals, list):
        meals = []

    if not meals:
        lines.append(c["no_meals"])
        return "\n".join(lines).strip()

    for meal in _sort_meals(meals):
        meal_type = str(meal.get("meal_type") or meal.get("type") or "").lower()
        meal_label = c["meal_labels"].get(meal_type, meal.get("name") or meal_type or "Meal")
        meal_label = telegram_agent.escape_html(str(meal_label))

        name = telegram_agent.escape_html(str(meal.get("name") or ""))
        notes = telegram_agent.escape_html(str(meal.get("notes") or ""))

        lines.append(f"🍽️ <b>{meal_label}</b>")

        if name and name.lower() != meal_label.lower():
            lines.append(f"<i>{name}</i>")

        meal_total = _format_total_line(c, meal)
        if meal_total:
            lines.append(meal_total)

        items = meal.get("items") or []
        if isinstance(items, list):
            for item in items:
                item_line = _format_item(item)
                if item_line:
                    lines.append(f"• {item_line}")

        if notes:
            lines.append(f"{c['note']}: <i>{notes}</i>")

        lines.append("")

    return "\n".join(lines).strip()


def _sort_meals(meals: list[dict]) -> list[dict]:
    order = {
        "fruehstueck": 1,
        "breakfast": 1,
        "mittag": 2,
        "lunch": 2,
        "abend": 3,
        "dinner": 3,
        "snack": 4,
        "snacks": 4,
    }

    def key(meal: dict) -> int:
        meal_type = str(meal.get("meal_type") or meal.get("type") or "").lower()
        return order.get(meal_type, 99)

    return sorted(meals, key=key)


def _format_total_line(c: dict, obj: dict) -> str:
    kcal = _num(obj.get("total_kcal"))
    protein = _num(obj.get("total_protein_g"))
    carbs = _num(obj.get("total_carbs_g"))
    fat = _num(obj.get("total_fat_g"))

    parts = []

    if kcal is not None:
        parts.append(f"<b>{kcal} kcal</b>")

    macros = []
    if protein is not None:
        macros.append(f"{c['protein']} {protein}g")
    if carbs is not None:
        macros.append(f"{c['carbs']} {carbs}g")
    if fat is not None:
        macros.append(f"{c['fat']} {fat}g")

    if macros:
        parts.append(" · ".join(macros))

    return " | ".join(parts)


def _format_item(item: dict) -> str:
    if not isinstance(item, dict):
        return ""

    name = item.get("food") or item.get("item") or item.get("name") or item.get("title") or ""
    name = telegram_agent.escape_html(str(name))

    if not name:
        return ""

    grams = item.get("grams")
    qty = item.get("qty")
    unit = item.get("unit")
    kcal = _num(item.get("kcal"))

    prefix = ""

    if grams is not None:
        prefix = f"{_num(grams)}g "
    elif qty is not None:
        prefix = f"{_num(qty)}"
        if unit:
            prefix += f" {telegram_agent.escape_html(str(unit))}"
        prefix += " "

    suffix = f" ({kcal} kcal)" if kcal is not None else ""
    return f"{prefix}{name}{suffix}"


def _parse_json(raw: str) -> dict | None:
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

    c = _cfg(customer)
    base_reply = telegram_agent.escape_html(parsed.get("reply", c["logged"]))
    totals_line = _build_totals_line(customer)
    full_reply = f"{base_reply}\n\n<b>{c['today_total']}:</b> {totals_line}"

    await _send_and_log(customer, full_reply, agent_name, model, tokens)


def _build_totals_line(customer: dict) -> str:
    c = _cfg(customer)
    today_start = datetime.combine(datetime.now(TZ).date(), datetime.min.time(), tzinfo=TZ)

    resp = (
        db.db()
        .table("food_logs")
        .select("total_kcal, protein_g, carbs_g, fat_g")
        .eq("customer_id", customer["id"])
        .gte("logged_at", today_start.isoformat())
        .execute()
    )

    rows = resp.data or []

    kcal = sum(r["total_kcal"] or 0 for r in rows)
    protein = round(sum(float(r["protein_g"] or 0) for r in rows), 1)
    carbs = round(sum(float(r["carbs_g"] or 0) for r in rows), 1)
    fat = round(sum(float(r["fat_g"] or 0) for r in rows), 1)

    profile = _profile(customer)
    target = profile.get("daily_kcal_target")

    macro_line = c["macros"].format(protein=protein, carbs=carbs, fat=fat)

    if target:
        pct = int(100 * kcal / target) if target else 0
        remaining = max(target - kcal, 0)
        remaining_text = c["remaining"].format(remaining=remaining)
        return f"{kcal}/{target} kcal ({pct}%) · {macro_line} · {remaining_text}"

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
