"""Telegram-бот «Сцена» (aiogram 3, polling).

Команды: /start [g_<код>|card_<код>], /new, /my, /catalog, /dates, /help.
Mini App отправляет sendData({gift_id}) после создания подарка — бот отвечает кнопками шаринга.
Уведомления из очереди рассылает шедулер в процессе API (app/scheduler.py).

Запуск:  BOT_TOKEN=... python -m app.bot
"""
import asyncio
import json
import logging

import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from . import config

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scena.bot")
dp = Dispatcher()
bot: Bot | None = None  # общий экземпляр: заполняется в main() или в webhook-режиме API-процесса


def webapp_button(text: str, hash_path: str = "") -> InlineKeyboardButton:
    url = config.webapp_url(config.PUBLIC_URL) + ("/#" + hash_path if hash_path else "")
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [webapp_button("🎁 Создать подарок", "/create")],
        [webapp_button("🎞️ Каталог сцен", "/catalog")],
        [webapp_button("💞 Мой таймлайн", "/timeline")],
        [webapp_button("📅 Важные даты", "/dates")],
    ])


def gift_keyboard(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [webapp_button("🎬 Открыть открытку", f"/g/{code}")],
        [InlineKeyboardButton(
            text="💬 Открыть в боте",
            url=f"https://t.me/{config.BOT_USERNAME}?startapp=g_{code}")],
    ])


@dp.message(CommandStart())
async def start(message: types.Message, command: CommandStart):
    payload = (command.args or "").strip()
    if payload.startswith("g_"):
        code = payload[2:]
        await message.answer(
            f"Тебе подарили сцену из фильма 🎬\nПолучатель сможет ответить своей сценой — "
            f"и у вас соберётся общий таймлайн, «ваш фильм».",
            reply_markup=gift_keyboard(code),
        )
    elif payload.startswith("card_"):
        code = payload[5:]
        await message.answer(
            "В букете была карточка «Сцена» 🌷\nСоздай подарок со сценой из фильма для получателя:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[webapp_button("✨ Создать подарок", f"/card/{code}")]]),
        )
    elif payload:
        await message.answer("Открываю подарок…", reply_markup=gift_keyboard(payload))
    else:
        # онбординг: новый пользователь — 3 шага
        from .db import connect
        conn = connect(config.DB_PATH)
        known = conn.execute("SELECT id FROM users WHERE telegram_id=?", (message.from_user.id,)).fetchone()
        conn.close()
        if known:
            text = ("С возвращением! 🎬\nВыбери действие внизу 👇")
        else:
            text = (
                "Привет! Я «Сцена» 🎬\n\n"
                "1️⃣ Выбери сцену из фильма в каталоге (по смыслу: «извиниться», «8 марта»…) — у каждой есть ⏱ таймкод-отрезок\n"
                "2️⃣ Напиши пару слов (или запиши голос 🎙) — и отправь подарок ссылкой или QR\n"
                "3️⃣ Получатель открывает и отвечает своей сценой — у вас собирается общий таймлайн, «ваш фильм» 💞\n\n"
                f"Вопросы — {config.SUPPORT_CONTACT}"
            )
        await message.answer(text, reply_markup=main_menu())


@dp.message(Command("new"))
async def new_gift(message: types.Message):
    await message.answer("Создаём подарок 👇", reply_markup=main_menu())


@dp.message(Command("catalog"))
async def catalog(message: types.Message):
    await message.answer("Каталог сцен 👇", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[webapp_button("🎞️ Открыть каталог", "/catalog")]]))


@dp.message(Command("dates"))
async def dates(message: types.Message):
    await message.answer("Важные даты и напоминания 👇", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[webapp_button("📅 Открыть календарь", "/dates")]]))


@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "🎬 «Сцена» — дарите моменты из фильмов\n\n"
        "• /new — создать подарок (сцена + таймкод + сообщение)\n"
        "• QR-карточку из букета сканирует получатель — открывается его открытка\n"
        "• получатель отвечает своей сценой → ваш общий таймлайн\n"
        "• /my — мой таймлайн, /catalog — каталог сцен, /dates — напоминания о датах",
        reply_markup=main_menu(),
    )


@dp.message(Command("my"))
async def my(message: types.Message):
    await message.answer(
        "💞 Открой свой таймлайн в Mini App — доступ защищён Telegram-авторизацией.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            webapp_button("💞 Мой таймлайн", "/timeline")]]))



@dp.message(F.web_app_data)
async def on_web_app_data(message: types.Message):
    """Mini App после создания подарка шлёт sendData({gift_id, open_url})."""
    try:
        payload = json.loads(message.web_app_data.data)
    except json.JSONDecodeError:
        return
    code = payload.get("gift_id")
    if not code:
        return
    deeplink = f"https://t.me/{config.BOT_USERNAME}?startapp=g_{code}"
    await message.answer(
        "Подарок создан 🎁 Отправь ссылку получателю или вставь QR в открытку:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Deeplink получателя", url=deeplink)],
            [webapp_button("🎬 Открыть самому", f"/g/{code}")],
        ]),
    )


@dp.message()
async def fallback(message: types.Message):
    await message.answer("Используй меню 👇", reply_markup=main_menu())


async def main():
    global bot
    if not config.BOT_TOKEN:
        raise SystemExit("Задай BOT_TOKEN в .env (токен от @BotFather)")
    bot = Bot(config.BOT_TOKEN)
    log.info("bot polling started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
