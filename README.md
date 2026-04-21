# Coach-Bot

Multi-Agent-System für Ernährungs- und Fitness-Coaching via Telegram.
Kunde chattet mit Bot → Claude antwortet → Coach sieht alles im Dashboard.

## Architektur (MVP)

```
Telegram ──► FastAPI (/webhook/telegram) ──► Router
                                              │
                                   ┌──────────┼──────────┐
                                   ▼          ▼          ▼
                               Intake-    Food-Log-   Check-in-
                                Agent      Agent       Agent
                                   │          │          │
                                   └──────────┼──────────┘
                                              ▼
                                        Supabase (Postgres)
                                        Claude API (Haiku 4.5)
```

## Setup (30 Min gesamt)

### 1. Telegram Bot erstellen
- In Telegram `@BotFather` öffnen
- `/newbot` → Namen und Username wählen
- Token in `.env` als `TELEGRAM_BOT_TOKEN` speichern
- Einen zufälligen String in `.env` als `TELEGRAM_WEBHOOK_SECRET` (z.B. `openssl rand -hex 32`)

### 2. Supabase Projekt
- [supabase.com](https://supabase.com) → Neues Projekt anlegen (Region: Frankfurt)
- SQL Editor öffnen → Inhalt von `sql/001_init.sql` einfügen → Run
- Settings → API → `URL` und `service_role` Key in `.env`
- In der `coaches`-Tabelle manuell **einen Coach-Eintrag** anlegen:
  ```sql
  insert into coaches (name, email, brand_voice) values
  ('Max Mustermann', 'max@example.com', 'locker, motivierend, du-Form');
  ```
  Die zurückgegebene `id` als `DEFAULT_COACH_ID` in Railway Env setzen.

### 3. Anthropic API Key
- [console.anthropic.com](https://console.anthropic.com) → API Keys → neuer Key
- In `.env` als `ANTHROPIC_API_KEY`

### 4. Lokal testen (optional)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # und ausfüllen
uvicorn app.main:app --reload
```

### 5. Railway Deploy
- GitHub-Repo anlegen, Code pushen
- Railway → New Project → Deploy from GitHub
- Alle `.env`-Variablen als Railway-Variablen setzen (inkl. `DEFAULT_COACH_ID`)
- `APP_BASE_URL` = deine Railway-URL (z.B. `https://coach-bot.up.railway.app`)
- Nach Deploy: Einmal `POST https://<deine-url>/admin/setup-webhook` aufrufen
  → registriert den Webhook bei Telegram

### 6. Testen
- Bot in Telegram anschreiben → Intake-Gespräch sollte starten

## Kosten (geschätzt, 10 Kunden)

| Service | Kosten/Monat |
|---|---|
| Railway (Hobby) | $5 |
| Supabase (Free) | $0 |
| Claude Haiku 4.5 (~10 Msgs/Kunde/Tag) | ~$3–6 |
| Telegram | $0 |
| **Gesamt** | **~$8–12** |

## Coach-Commands (im eigenen Chat mit dem Bot)

Damit der Coach selbst seine `telegram_chat_id` bekommt, schreibt er dem Bot einmal.
Dann trägt man die ID manuell in der `coaches.telegram_chat_id`-Spalte ein.

Danach funktionieren im Coach-Chat:
- `/list` — alle Kunden
- `/today <cid>` — Tages-Makros eines Kunden
- `/settarget <cid> <kcal> <p> <c> <f>` — Makro-Ziele setzen (Pflicht-Schritt nach Intake)
- `/pause <cid>` / `/resume <cid>`
- `/help`

`<cid>` = die ersten 8 Zeichen der Kunden-UUID.

## Roadmap

- [x] Projekt-Skeleton, DB-Schema, Telegram-Integration
- [x] Intake-Agent (Onboarding-Chat)
- [x] Food-Log-Agent (Text → Makros, mit Tages-Tracking)
- [x] Coach-Commands (Liste, Tages-Übersicht, Ziele setzen)
- [ ] Check-in-Agent (wöchentlich automatisch)
- [ ] Progress-Agent (Trends + Alerts)
- [ ] Coach-Dashboard (Next.js + Supabase Auth)
