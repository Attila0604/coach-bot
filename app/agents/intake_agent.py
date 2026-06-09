"""Intake-Agent: Onboarding conversation.

Neue Kunden:
1. Sprache auswählen: Deutsch / Ungarisch / Italienisch
2. Pflichtfelder sammeln
3. Profil speichern
4. Kunde auf active setzen
"""
import json
import re

from app import db
from app.agents import telegram_agent
from app.services import claude_client


REQUIRED_FIELDS = [
    ("age", "Alter in Jahren"),
    ("gender", "Geschlecht"),
    ("height_cm", "Größe in cm"),
    ("weight_start_kg", "aktuelles Gewicht in kg"),
    ("weight_target_kg", "Zielgewicht in kg"),
    ("goal", "Hauptziel"),
    ("experience_level", "Erfahrung"),
    ("equipment", "Equipment"),
    ("allergies", "Allergien oder Unverträglichkeiten"),
    ("food_preferences", "Ernährungsvorlieben"),
]
REQUIRED_KEYS = {k for k, _ in REQUIRED_FIELDS}


LANG = {
    "de": {
        "tone": "Deutsch, per Du, freundlich, motivierend, kurz und menschlich",
        "language_question": (
            "Hi {name}! 👋 Schön, dass du da bist.\n\n"
            "Welche Sprache möchtest du verwenden?\n"
            "Bitte antworte mit: Deutsch, Magyar oder Italiano."
        ),
        "language_saved": "Super, wir machen auf Deutsch weiter. 🙂\n\nGanz ehrlich: Wie alt bist du?",
        "retry_json": "Hm, da ist mir gerade etwas durcheinander geraten — kannst du das nochmal schreiben?",
        "missing_prefix": "Fast geschafft! Eine Sache brauche ich noch:",
        "complete": (
            "✅ Dein Profil ist angelegt. Dein Coach setzt jetzt deine Ziele und Pläne. "
            "Du kannst ab sofort dein Essen als Text oder Foto loggen."
        ),
        "fields": {
            "age": "Wie alt bist du?",
            "gender": "Was ist dein Geschlecht? m, w oder d?",
            "height_cm": "Wie groß bist du in cm?",
            "weight_start_kg": "Wie viel wiegst du aktuell in kg?",
            "weight_target_kg": "Was ist dein Zielgewicht in kg?",
            "goal": "Was ist dein Hauptziel: abnehmen, Muskelaufbau, Erhalt oder Ausdauer?",
            "experience_level": "Wie würdest du deine Erfahrung einschätzen: Anfänger, Fortgeschritten oder Profi?",
            "equipment": "Trainierst du zu Hause ohne Equipment, zu Hause mit Basic-Equipment oder im Gym?",
            "allergies": "Hast du Allergien oder Unverträglichkeiten? Wenn nein, schreib einfach: keine.",
            "food_preferences": "Gibt es Lebensmittel, die du bevorzugst oder nicht magst? Wenn nein, schreib einfach: keine.",
        },
    },
    "hu": {
        "tone": "Ungarisch, tegeződve, barátságosan, motiválóan, röviden és emberien",
        "language_question": (
            "Szia {name}! 👋 Örülök, hogy itt vagy.\n\n"
            "Milyen nyelven szeretnél kommunikálni?\n"
            "Kérlek válaszolj így: Deutsch, Magyar vagy Italiano."
        ),
        "language_saved": "Szuper, akkor magyarul folytatjuk. 🙂\n\nŐszintén: hány éves vagy?",
        "retry_json": "Hmm, valami most összekeveredett nálam — le tudnád írni még egyszer?",
        "missing_prefix": "Majdnem kész vagyunk! Egy dologra még szükségem van:",
        "complete": (
            "✅ A profilod elkészült. Az edződ most be tudja állítani a céljaidat és a terveidet. "
            "Mostantól szövegként vagy fotóval is tudod rögzíteni az ételeidet."
        ),
        "fields": {
            "age": "Hány éves vagy?",
            "gender": "Mi a nemed? férfi, nő vagy egyéb?",
            "height_cm": "Hány cm magas vagy?",
            "weight_start_kg": "Most hány kg vagy?",
            "weight_target_kg": "Mi a céltestsúlyod kg-ban?",
            "goal": "Mi a fő célod: fogyás, izomépítés, szinten tartás vagy állóképesség?",
            "experience_level": "Milyen szinten vagy: kezdő, haladó vagy profi?",
            "equipment": "Hol edzel: otthon eszköz nélkül, otthon alap eszközökkel vagy edzőteremben?",
            "allergies": "Van allergiád vagy ételérzékenységed? Ha nincs, írd: nincs.",
            "food_preferences": "Van olyan étel, amit szeretsz vagy nem szeretsz? Ha nincs különös, írd: nincs.",
        },
    },
    "it": {
        "tone": "Italiano, informale, amichevole, motivante, breve e umano",
        "language_question": (
            "Ciao {name}! 👋 Sono felice che tu sia qui.\n\n"
            "In quale lingua vuoi comunicare?\n"
            "Rispondi con: Deutsch, Magyar oppure Italiano."
        ),
        "language_saved": "Perfetto, continuiamo in italiano. 🙂\n\nPartiamo semplice: quanti anni hai?",
        "retry_json": "Hmm, qualcosa è andato storto — puoi scriverlo ancora una volta?",
        "missing_prefix": "Ci siamo quasi! Mi serve ancora una cosa:",
        "complete": (
            "✅ Il tuo profilo è stato creato. Il tuo coach ora può impostare obiettivi e piani. "
            "Da adesso puoi registrare il cibo con testo o foto."
        ),
        "fields": {
            "age": "Quanti anni hai?",
            "gender": "Qual è il tuo sesso? m, f oppure altro?",
            "height_cm": "Quanto sei alto/a in cm?",
            "weight_start_kg": "Quanto pesi attualmente in kg?",
            "weight_target_kg": "Qual è il tuo peso obiettivo in kg?",
            "goal": "Qual è il tuo obiettivo principale: dimagrire, massa muscolare, mantenimento o resistenza?",
            "experience_level": "Qual è il tuo livello: principiante, intermedio o avanzato?",
            "equipment": "Ti alleni a casa senza attrezzi, a casa con attrezzi base o in palestra?",
            "allergies": "Hai allergie o intolleranze? Se no, scrivi: nessuna.",
            "food_preferences": "Ci sono cibi che preferisci o che non ti piacciono? Se no, scrivi: nessuna preferenza.",
        },
    },
}


SYSTEM_PROMPT = """Du bist der Onboarding-Assistent eines Ernährungs- und Fitness-Coaches.
Du sprichst mit dem Kunden auf: {tone}

AUFGABE
- Sammle die Pflichtfelder im Gespräch.
- Stelle immer nur EINE Frage pro Nachricht.
- Wiederhole NIEMALS eine Frage zu einem Feld, das unter "Bereits erfasst" steht.
- Wenn die letzte User-Nachricht neue Infos enthält, lies sie heraus und fülle sie in "extracted".
- Antworte freundlich, kurz und natürlich.

WICHTIG ZU DEN WERTEN
Speichere intern immer diese festen Werte:
- gender: "m", "w" oder "d"
- goal: "abnehmen", "muskelaufbau", "erhalt" oder "ausdauer"
- experience_level: "anfaenger", "fortgeschritten" oder "profi"
- equipment: "home_none", "home_basic" oder "gym"
- allergies und food_preferences immer als Array, leer wenn keine

Bereits erfasst:
{collected_summary}

Noch zu erfassen:
{missing_list}

ANTWORT-FORMAT
Antworte IMMER ausschließlich als gültiges JSON.
Kein Text davor, kein Text danach.

JSON-Struktur:
{{
  "extracted": {{
    "age": null,
    "gender": null,
    "height_cm": null,
    "weight_start_kg": null,
    "weight_target_kg": null,
    "goal": null,
    "experience_level": null,
    "equipment": null,
    "allergies": null,
    "food_preferences": null
  }},
  "reply": "Deine nächste Nachricht an den Kunden — 1 bis 2 Sätze, eine Frage.",
  "is_complete": false
}}

REGELN
- In extracted nur Werte setzen, die du aus der letzten User-Nachricht sicher herausliest.
- Wenn nichts Neues sicher ist, extracted als leeres Objekt zurückgeben.
- is_complete nur true, wenn nach dem Merge alle Pflichtfelder vorhanden sind.
- Wenn is_complete true ist: reply ist ein kurzer motivierender Abschluss-Satz, keine neue Frage.
- Kein Text außerhalb des JSON.
"""


def _language_from_text(text: str) -> str | None:
    t = (text or "").strip().lower()

    if t in {"de", "deutsch", "german", "német", "nemet"} or "deutsch" in t:
        return "de"
    if t in {"hu", "magyar", "hungarian", "ungarisch", "magyarul"} or "magyar" in t:
        return "hu"
    if t in {"it", "italiano", "italian", "italienisch", "olasz"} or "italiano" in t:
        return "it"

    return None


def _profile(customer: dict) -> dict:
    profile = customer.get("customer_profiles") or []
    if isinstance(profile, list):
        return profile[0] if profile else {}
    if isinstance(profile, dict):
        return profile
    return {}


def _field_question(lang: str, key: str) -> str:
    return LANG.get(lang, LANG["de"])["fields"].get(key, key)


def _missing_question(lang: str, missing_key: str) -> str:
    c = LANG.get(lang, LANG["de"])
    return f"{c['missing_prefix']} {c['fields'].get(missing_key, missing_key)}"


def _language_question(customer: dict, lang: str = "de") -> str:
    first_name = telegram_agent.escape_html(customer.get("first_name") or "")
    name = first_name if first_name else "!"
    return LANG[lang]["language_question"].format(name=name)


async def start(customer: dict) -> None:
    """Send the very first intake message."""
    greeting = _language_question(customer, "de")

    await telegram_agent.send_message(customer["telegram_chat_id"], greeting)
    db.log_message(customer["id"], "out", greeting, agent_name="intake")

    db.set_conversation_state(
        customer_id=customer["id"],
        current_flow="intake",
        current_step="ask_language",
        state_data={"collected": {}, "language": None},
    )


async def handle_step(customer: dict, state: dict, user_text: str) -> None:
    """Single Claude call: extract + reply + completeness check."""
    state_data = (state or {}).get("state_data", {}) or {}
    collected = state_data.get("collected", {}) or {}
    language = state_data.get("language")

    if language not in LANG:
        detected = _language_from_text(user_text)

        if detected not in LANG:
            msg = _language_question(customer, "de")
            await telegram_agent.send_message(customer["telegram_chat_id"], msg)
            db.log_message(customer["id"], "out", msg, agent_name="intake")

            db.set_conversation_state(
                customer_id=customer["id"],
                current_flow="intake",
                current_step="ask_language",
                state_data={"collected": collected, "language": None},
            )
            return

        language = detected
        msg = LANG[language]["language_saved"]

        await telegram_agent.send_message(customer["telegram_chat_id"], msg)
        db.log_message(customer["id"], "out", msg, agent_name="intake")

        db.set_conversation_state(
            customer_id=customer["id"],
            current_flow="intake",
            current_step="ask_age",
            state_data={"collected": collected, "language": language},
        )
        return

    c = LANG[language]

    collected_summary = (
        "\n".join(f"- {k}: {_fmt(v)}" for k, v in collected.items())
        if collected
        else "(noch nichts)"
    )

    missing = [
        f"- {key}: {_field_question(language, key)}"
        for key, _desc in REQUIRED_FIELDS
        if key not in collected
    ]
    missing_list = "\n".join(missing) if missing else "(alle Felder erfasst — setze is_complete=true)"

    system = SYSTEM_PROMPT.format(
        tone=c["tone"],
        collected_summary=collected_summary,
        missing_list=missing_list,
    )

    history = db.recent_messages(customer["id"], limit=20)
    history = [
        h for h in history
        if h["content"] != user_text or h["direction"] != "in"
    ]

    messages = claude_client.build_messages_from_history(history, user_text)
    raw, tokens, model = await claude_client.ask(system, messages, max_tokens=600)

    data = _parse_json(raw)

    if data is None:
        fallback = c["retry_json"]
        await telegram_agent.send_message(customer["telegram_chat_id"], fallback)
        db.log_message(
            customer["id"],
            "out",
            fallback,
            agent_name="intake",
            model_used=model,
            tokens_used=tokens,
        )
        return

    extracted = data.get("extracted") or {}

    for k, v in extracted.items():
        if k not in REQUIRED_KEYS:
            continue
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        collected[k] = v

    reply_text = telegram_agent.escape_html((data.get("reply") or "").strip())
    claude_complete = bool(data.get("is_complete"))

    all_present = REQUIRED_KEYS.issubset(collected.keys())
    is_complete = claude_complete and all_present

    if claude_complete and not all_present:
        missing_keys = [key for key, _desc in REQUIRED_FIELDS if key not in collected]
        reply_text = telegram_agent.escape_html(_missing_question(language, missing_keys[0]))

    if not is_complete:
        done = len(REQUIRED_KEYS & set(collected.keys()))
        total = len(REQUIRED_KEYS)
        if done > 0 and reply_text:
            reply_text = f"({done}/{total}) {reply_text}"

    db.set_conversation_state(
        customer_id=customer["id"],
        current_flow="intake" if not is_complete else None,
        current_step="ask_next" if not is_complete else None,
        state_data={"collected": collected, "language": language} if not is_complete else {},
    )

    if reply_text:
        await telegram_agent.send_message(customer["telegram_chat_id"], reply_text)
        db.log_message(
            customer["id"],
            "out",
            reply_text,
            agent_name="intake",
            model_used=model,
            tokens_used=tokens,
        )

    if is_complete:
        _finalize_intake(customer, collected, language)

        final_msg = c["complete"]
        await telegram_agent.send_message(customer["telegram_chat_id"], final_msg)
        db.log_message(customer["id"], "out", final_msg, agent_name="intake")


def _parse_json(raw: str) -> dict | None:
    """Parse JSON aus Claude-Antwort."""
    if not raw:
        return None

    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _fmt(v) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "(keine)"
    return str(v)


def _finalize_intake(customer: dict, collected: dict, language: str) -> None:
    """Write profile to DB and mark customer as active."""
    profile_row = {
        "customer_id": customer["id"],
        "language": language,
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
