import asyncio
import traceback

from telegram import Update
from telegram.ext import ContextTypes

import bot_config
import claude_client
from claude_client import ask_claude, clear_history, set_tsim, is_tsim
from tg.formatting import to_telegram_html, split_message, parse_reset_wait


# ── Command handlers ──────────────────────────────────────────────────────────

async def handle_clear(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_history(update.effective_user.id)
    await update.message.reply_text("🗑️ Conversation cleared.")


async def handle_chatid(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"Chat ID: <code>{update.effective_chat.id}</code>\n"
        f"Your user ID: <code>{update.effective_user.id}</code>",
        parse_mode="HTML",
    )


async def handle_tsim(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    set_tsim(update.effective_chat.id, True)
    await update.message.reply_text("tsim mode on. dont expect me to be nice.")


async def handle_tsim_off(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    set_tsim(update.effective_chat.id, False)
    await update.message.reply_text("✅ Back to normal mode.")


# ── Inline command dispatch (for @bot /cmd messages in groups) ────────────────

_INLINE_COMMANDS = {
    "tsim": handle_tsim,
    "tsim_off": handle_tsim_off,
    "clear": handle_clear,
    "chatid": handle_chatid,
}


# ── Main message handler ──────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    allowed_chats = bot_config.get_allowed_chats()
    if chat_id not in allowed_chats:
        return

    allowed_threads = allowed_chats[chat_id]
    thread_id = update.message.message_thread_id if update.message else None
    if allowed_threads and thread_id not in allowed_threads:
        return

    # groups: respond only on @mention or reply to bot
    if chat_id < 0:
        bot_me = await context.bot.get_me()
        bot_username = bot_me.username
        bot_id = bot_me.id

        reply_to = update.message.reply_to_message
        is_reply_to_bot = (
            reply_to is not None
            and reply_to.from_user is not None
            and reply_to.from_user.id == bot_id
        )
        mentioned = any(
            e.type == "mention"
            and update.message.text[e.offset:e.offset + e.length] == f"@{bot_username}"
            for e in (update.message.entities or [])
        )

        if not mentioned and not is_reply_to_bot:
            return

        # strip @mention from text
        user_text = update.message.text
        for e in sorted(update.message.entities or [], key=lambda x: x.offset, reverse=True):
            if e.type == "mention":
                user_text = (user_text[:e.offset] + user_text[e.offset + e.length:]).strip()

        if not user_text:
            return

        # prepend quoted context when replying to bot
        if is_reply_to_bot and reply_to.text:
            quoted = reply_to.text[:500]
            user_text = f"[Replying to your message:\n{quoted}\n]\n\n{user_text}"

        # dispatch inline commands e.g. "@bot /tsim"
        if user_text.startswith("/"):
            cmd = user_text.split()[0].lstrip("/").split("@")[0].lower()
            if cmd in _INLINE_COMMANDS:
                await _INLINE_COMMANDS[cmd](update, context)
                return
    else:
        user_text = update.message.text

    # history keyed per-user inside groups, per-chat in private
    user = update.effective_user
    history_key = user.id if chat_id < 0 else chat_id
    username = f"@{user.username}" if user.username else user.full_name

    thinking_msg = await update.message.reply_text("⏳ Thinking...")

    async def edit(text: str, parse_mode: str = None) -> None:
        try:
            await thinking_msg.edit_text(text, parse_mode=parse_mode)
        except Exception:
            pass

    for attempt in range(3):
        try:
            reply = await ask_claude(user_text, history_key,
                                     user_id=user.id, username=username,
                                     bot=context.bot, chat_id=chat_id)
            chunks = split_message(reply)
            try:
                await thinking_msg.edit_text(to_telegram_html(chunks[0]), parse_mode="HTML")
            except Exception:
                await thinking_msg.edit_text(chunks[0])
            for chunk in chunks[1:]:
                try:
                    await update.message.reply_text(to_telegram_html(chunk), parse_mode="HTML")
                except Exception:
                    await update.message.reply_text(chunk)
            return
        except Exception as e:
            traceback.print_exc()
            inner = e.exceptions[0] if hasattr(e, "exceptions") and e.exceptions else e
            err_str = str(inner)
            if "429" in err_str or "RateLimitError" in err_str:
                wait = parse_reset_wait(err_str)
                await edit(
                    f"⏳ Rate limited by LLM proxy (shared 100k tokens/min class quota is exhausted).\n"
                    f"Retrying in {wait}s (attempt {attempt + 1}/3)..."
                )
                await asyncio.sleep(wait)
            else:
                msg = "\n".join(str(ex) for ex in e.exceptions) if hasattr(e, "exceptions") else err_str
                await edit(f"❌ Error: {msg}")
                return

    await edit(
        "❌ Still rate limited after 3 attempts.\n"
        "The shared LLM proxy key is out of tokens for this minute — "
        "other students in the class are using it too.\n"
        "Try again in a moment, or ask your instructor for a personal API key."
    )
