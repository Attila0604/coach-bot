"""Router: decides which agent handles an incoming message based on customer status + state."""
from app import db
from app.agents import telegram_agent, intake_agent, food_log_agent, coach_commands


async def handle_incoming(update: dict, default_coach_id: str) -> None:
    """Main entrypoint for any incoming Telegram message."""
    chat_id = update["chat_id"]

    # ── PHOTO BRANCH ──────────────────────────────────────────────
    if update.get("is_photo"):
        await _handle_photo(update, chat_id, default_coach_id)
        return

    # ── REJECT OTHER NON-TEXT (stickers, voice, video, document) ──
    if update["is_non_text"]:
        await telegram_agent.send_message(
            chat_id,
            "Ich kann Text-Nachrichten und Essens-Fotos verarbeiten. 📝🍽️",
        )
        return

    text = update["text"]

    # Coach-command check first — coaches use the same bot
    if text.startswith("/"):
        handled = await coach_commands.handle_coach_command(chat_id, text)
        if handled:
            return

    # Look up or create customer
    customer = db.get_customer_by_chat_id(chat_id)
    if customer is None:
        customer = db.create_customer(
            coach_id=default_coach_id,
            chat_id=chat_id,
            username=update["username"],
            first_name=update["first_name"],
        )
        # Log incoming + start intake
        db.log_message(customer["id"], "in", text)
        await intake_agent.start(customer)
        return

    # Log the incoming message
    db.log_message(customer["id"], "in", text)

    # Route based on current flow
    state = db.get_conversation_state(customer["id"])
    current_flow = state.get("current_flow") if state else None

    await telegram_agent.send_typing(chat_id)

    if current_flow == "intake" or customer["status"] == "intake":
        await intake_agent.handle_step(customer, state, text)
        return

    if customer["status"] == "active":
        # Food-Log-Agent also handles free chat and redirects check-in hints
        await food_log_agent.handle(customer, text)
        return

    if customer["status"] == "paused":
        await telegram_agent.send_message(
            chat_id,
            "Dein Coaching ist pausiert. Melde dich beim Coach, wenn du wieder einsteigen willst.",
        )
        return

    # archived or unknown
    await telegram_agent.send_message(
        chat_id, "Hmm, hier stimmt etwas nicht. Bitte meld dich beim Coach."
    )


async def _handle_photo(update: dict, chat_id: int, default_coach_id: str) -> None:
    """Photos are only accepted from active customers and routed to vision food-log."""
    customer = db.get_customer_by_chat_id(chat_id)

    if customer is None:
        await telegram_agent.send_message(
            chat_id,
            "Hi! 👋 Bevor wir mit Fotos starten, brauche ich kurz ein paar Infos von dir. "
            "Schreib mir einfach 'Hi' und wir legen los.",
        )
        return

    if customer["status"] == "intake":
        await telegram_agent.send_message(
            chat_id,
            "Cool, dass du schon ein Foto schicken willst! 🍽️ Lass uns aber erst dein Profil "
            "fertig machen — danach kannst du jederzeit Essens-Fotos schicken.",
        )
        return

    if customer["status"] != "active":
        await telegram_agent.send_message(
            chat_id,
            "Dein Coaching ist gerade nicht aktiv. Melde dich beim Coach, wenn du wieder einsteigen willst.",
        )
        return

    # Active customer → download + analyze
    caption = update.get("caption")
    log_label = f"[Foto{' — ' + caption if caption else ''}]"
    db.log_message(customer["id"], "in", log_label)

    await telegram_agent.send_typing(chat_id)

    try:
        photo_bytes, media_type = await telegram_agent.download_photo(
            update["photo_file_id"]
        )
    except Exception:
        await telegram_agent.send_message(
            chat_id,
            "Konnte dein Foto leider nicht laden — kannst du es nochmal schicken?",
        )
        return

    await food_log_agent.handle_photo(customer, photo_bytes, media_type, caption)
