# AGENTS.md — Projekt- & Ökosystem-Kontext (coach-bot)

> Dauerhaftes Gedächtnis für KI-Agenten und Menschen. Ein neuer Agent startet
> ohne Vorwissen — diese Datei bringt ihn auf Stand.
> PFLEGE: Bei Änderungen an Datenmodell, Flows, Agents oder Repo-Zusammenspiel
> diese Datei im selben PR aktualisieren.

## 1. Das große Ganze: drei Repos, eine Datenbank
Dieses Repo (`coach-bot`) ist der **Daten-Eingang** — eine von drei Komponenten,
die sich EINE Supabase-(Postgres-)DB teilen:
- `coach-bot` (DIESES Repo, Python/FastAPI): Telegram-Bot. Onboarding (Intake) +
  Food-Logging (Text & Foto via Claude) + Coach-Quick-Commands. Schreibt
  customers, customer_profiles, food_logs, messages, conversation_states.
- `coach-app` (Next.js): Coach-Dashboard + KI-Trainings-/Meal-Plan-Generator.
- `coach-customer-app` (Next.js): Kunden-PWA, Workout-Player.
> Das vollständige, rekonstruierte DB-Schema liegt in
> `coach-customer-app/db/schema.reference.sql` — als Karte/Referenz nutzen.

## 2. Dieses Repo im Detail
Stack: Python, FastAPI, Anthropic Claude (SDK), supabase-py (Service-Key,
umgeht RLS). Deploy: Railway (`Procfile`, `railway.json`, uvicorn).

Endpoints (`app/main.py`):
- GET  /                      Health-Check
- POST /webhook/telegram      Telegram-Updates (prüft Secret-Header)
- POST /admin/setup-webhook   Webhook einmalig registrieren

Fluss: Telegram → `telegram_agent.parse_update` → `router.handle_incoming`:
- Coach-Befehle (/help, /list, /today, /settarget, /pause, /resume) →
  `agents/coach_commands.py` (wenn chat_id zu coaches.telegram_chat_id passt).
- Neuer/Intake-Kunde → `agents/intake_agent.py` (KI-Onboarding, 10 Pflichtfelder,
  JSON {extracted, reply, is_complete}, State in conversation_states).
- Aktiver Kunde → `agents/food_log_agent.py` (Text + Foto/Vision; Claude-JSON
  food_log/chat/checkin_hint; schreibt food_logs, antwortet mit Tages-Summe).

Weitere Module: `agents/telegram_agent.py` (Send/Parse/Webhook),
`services/claude_client.py` (ask / ask_with_image / History-Builder),
`db.py` (alle DB-Zugriffe), `config.py` (Settings aus Env).

Modelle: Default claude-haiku-4-5 (günstig, Vision), Premium claude-sonnet-4-6.
Token-Verbrauch wird pro Nachricht in messages.tokens_used getrackt.

Env-Variablen (.env): TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET,
ANTHROPIC_API_KEY, CLAUDE_MODEL_DEFAULT, CLAUDE_MODEL_PREMIUM, SUPABASE_URL,
SUPABASE_SERVICE_KEY, APP_BASE_URL, TZ (Europe/Vienna), LOG_LEVEL,
DEFAULT_COACH_ID.

## 3. Konventionen
- Sprache DE (Bot-Texte + Commits/PRs). Branches `cursor/<name>`, PRs gegen main.
- food_logs.meal_type DEUTSCH (fruehstueck/mittag/abend/snack);
  meal_plans.meals[].meal_type (Coach-App) ENGLISCH — bewusst uneinheitlich.
- messages.direction = 'in' / 'out'.

## 4. Offene Punkte
- `sql/001_init.sql` ist nur das MVP-Schema. Es fehlen die später ergänzten
  Tabellen (training_*, workout_*, meal_plans, coach_notes, magic_codes) und
  Felder (coaches.user_id/role, ...). Vollständige Referenz:
  coach-customer-app/db/schema.reference.sql. To-do: echtes Schema via
  `supabase db dump` versionieren.
- `DEFAULT_COACH_ID` ist jetzt in `config.py` (Settings + `validate`) und in
  `.env.example` ergänzt; `main.py` liest sie über `settings.DEFAULT_COACH_ID`.
