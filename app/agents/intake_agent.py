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
Antworte IMMER ausschließlich mit EINEM JSON-Block, nichts davor, nichts danach:

```json
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
