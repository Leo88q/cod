"""Dedicated scheduler: python -m app.worker (one process per database)."""
import asyncio
import fcntl
import logging

from . import config, scheduler
from .db import init_db


async def main():
    init_db(config.DB_PATH)
    # Local filesystem lock is released by OS after crashes.
    with open(config.DB_PATH + ".worker.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A scheduler worker already owns this database")
        bot = None
        if config.BOT_TOKEN:
            from aiogram import Bot
            bot = Bot(config.BOT_TOKEN)
        try:
            await scheduler.run(bot)
        finally:
            if bot:
                await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
