import asyncio
import os

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

import bot_config
from mcp_client import init_mcp
from polling import poll_infra_prs
from tg.handlers import (
    handle_message,
    handle_clear,
    handle_chatid,
    handle_tsim,
    handle_tsim_off,
)


async def post_init(application) -> None:
    bot_config.seed_defaults()
    await init_mcp()
    asyncio.create_task(poll_infra_prs(application.bot))


def main() -> None:
    app = (
        ApplicationBuilder()
        .token(os.getenv("TELEGRAM_BOT_TOKEN"))
        .post_init(post_init)
        .build()
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("clear", handle_clear))
    app.add_handler(CommandHandler("chatid", handle_chatid))
    app.add_handler(CommandHandler("tsim", handle_tsim))
    app.add_handler(CommandHandler("tsim_off", handle_tsim_off))
    print("Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
