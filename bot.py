# -*- coding: utf-8 -*-
"""
Главный файл бота-напоминалки о прививках.

Как запустить:
1. pip install -r requirements.txt
2. Вставь свой токен в переменную BOT_TOKEN ниже (или задай через переменную окружения BOT_TOKEN)
3. python bot.py

Подробности — в README.md
"""

import asyncio
import logging
import os
from datetime import datetime, date

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

import database
import vaccines
from scheduler import setup_scheduler

# ==== НАСТРОЙКИ ====
# Вставь сюда токен, который дал @BotFather, ИЛИ задай переменную окружения BOT_TOKEN
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_СЮДА_СВОЙ_ТОКЕН")

logging.basicConfig(level=logging.INFO)

router = Router()


# ==== Состояния диалога добавления ребёнка (FSM — машина состояний) ====
class AddChild(StatesGroup):
    waiting_for_name = State()
    waiting_for_birth_date = State()


# ==== Команда /start ====
@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "Привет! Я помогу не пропустить прививки твоего ребёнка по "
        "национальному календарю РФ.\n\n"
        "Команды:\n"
        "/add — добавить ребёнка\n"
        "/list — мои дети и ближайшие прививки\n"
        "/done — отметить прививку как сделанную\n"
        "/help — что я умею"
    )
    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Я слежу за графиком прививок по датам рождения детей, которых ты добавишь.\n"
        "Раз в день проверяю, у кого скоро прививка, и присылаю напоминание заранее.\n\n"
        "/add — добавить ребёнка\n"
        "/list — посмотреть детей и ближайшие прививки\n"
        "/done — отметить прививку как сделанную (чтобы не напоминал зря)"
    )


# ==== Добавление ребёнка: шаг 1 — имя ====
@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext):
    await message.answer(
        "Как зовут малыша?", reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(AddChild.waiting_for_name)


@router.message(AddChild.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer(
        "Дата рождения в формате ДД.ММ.ГГГГ (например, 15.03.2024):"
    )
    await state.set_state(AddChild.waiting_for_birth_date)


# ==== Добавление ребёнка: шаг 2 — дата рождения ====
@router.message(AddChild.waiting_for_birth_date)
async def process_birth_date(message: Message, state: FSMContext):
    raw = message.text.strip()
    try:
        birth_date = datetime.strptime(raw, "%d.%m.%Y").date()
    except ValueError:
        await message.answer(
            "Не получилось распознать дату. Введи в формате ДД.ММ.ГГГГ, "
            "например: 15.03.2024"
        )
        return

    if birth_date > date.today():
        await message.answer("Дата рождения не может быть в будущем. Попробуй ещё раз.")
        return

    data = await state.get_data()
    name = data["name"]

    database.add_child(
        telegram_user_id=message.from_user.id,
        name=name,
        birth_date=birth_date.isoformat(),
    )
    await state.clear()

    await message.answer(
        f"Готово! Добавил(а) {name}, дата рождения {raw}.\n"
        f"Посмотреть график прививок — /list"
    )


# ==== Список детей и ближайшие прививки ====
@router.message(Command("list"))
async def cmd_list(message: Message):
    children = database.get_children(message.from_user.id)

    if not children:
        await message.answer(
            "У тебя пока нет добавленных детей. Добавь через /add"
        )
        return

    today = date.today()
    reply_lines = []

    for child_id, name, birth_date_str in children:
        birth_date = datetime.strptime(birth_date_str, "%Y-%m-%d").date()
        schedule = vaccines.get_vaccines_for_child(birth_date, today)
        completed_ids = database.get_completed_vaccine_ids(child_id)

        # только будущие и недавно прошедшие (последние 30 дней) прививки
        upcoming = [v for v in schedule if v["days_left"] >= -30]
        upcoming.sort(key=lambda v: v["days_left"])

        reply_lines.append(f"👶 {name} (родился {birth_date.strftime('%d.%m.%Y')})")

        if not upcoming:
            reply_lines.append("  Все прививки по базовому графику пройдены.")
        else:
            for v in upcoming[:6]:  # показываем ближайшие 6
                if v["id"] in completed_ids:
                    reply_lines.append(f"  ✅ {v['name']} — сделано")
                    continue
                if v["days_left"] < 0:
                    status = f"была {abs(v['days_left'])} дн. назад"
                elif v["days_left"] == 0:
                    status = "СЕГОДНЯ"
                else:
                    status = f"через {v['days_left']} дн. ({v['due_date'].strftime('%d.%m.%Y')})"
                reply_lines.append(f"  • {v['name']} — {status}")
        reply_lines.append("")

    reply_lines.append("Отметить прививку сделанной — /done")
    await message.answer("\n".join(reply_lines))


# ==== Отметить прививку как сделанную ====
@router.message(Command("done"))
async def cmd_done(message: Message):
    children = database.get_children(message.from_user.id)

    if not children:
        await message.answer("У тебя пока нет добавленных детей. Добавь через /add")
        return

    # если ребёнок один — сразу показываем его прививки
    # если несколько — сначала даём выбрать ребёнка
    if len(children) == 1:
        await show_vaccine_buttons(message, children[0][0], children[0][1])
    else:
        buttons = [
            [InlineKeyboardButton(text=name, callback_data=f"pickchild:{child_id}")]
            for child_id, name, _ in children
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer("Какого ребёнка?", reply_markup=keyboard)


async def show_vaccine_buttons(message: Message, child_id: int, child_name: str):
    """Показывает кнопки с несделанными прививками для отметки."""
    children = database.get_children(message.from_user.id)
    birth_date_str = next(b for cid, n, b in children if cid == child_id)
    birth_date = datetime.strptime(birth_date_str, "%Y-%m-%d").date()

    schedule = vaccines.get_vaccines_for_child(birth_date, date.today())
    completed_ids = database.get_completed_vaccine_ids(child_id)
    not_done = [v for v in schedule if v["id"] not in completed_ids]
    not_done.sort(key=lambda v: v["days_left"])

    if not not_done:
        await message.answer(f"У {child_name} все прививки по графику уже отмечены сделанными.")
        return

    buttons = [
        [InlineKeyboardButton(
            text=v["name"],
            callback_data=f"markdone:{child_id}:{v['id']}",
        )]
        for v in not_done[:8]  # ограничим список, чтобы не перегружать экран
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"Какую прививку отметить сделанной у {child_name}?",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("pickchild:"))
async def on_pick_child(callback: CallbackQuery):
    child_id = int(callback.data.split(":")[1])
    children = database.get_children(callback.from_user.id)
    child_name = next((n for cid, n, _ in children if cid == child_id), None)

    if child_name is None:
        await callback.answer("Не нашёл такого ребёнка", show_alert=True)
        return

    await callback.message.delete()
    await show_vaccine_buttons(callback.message, child_id, child_name)
    await callback.answer()


@router.callback_query(F.data.startswith("markdone:"))
async def on_mark_done(callback: CallbackQuery):
    _, child_id_str, vaccine_id = callback.data.split(":")
    child_id = int(child_id_str)

    database.mark_vaccine_done(
        child_id=child_id,
        vaccine_id=vaccine_id,
        completed_date=date.today().isoformat(),
    )

    vaccine_name = vaccines.get_vaccine_name_by_id(vaccine_id)
    await callback.message.edit_text(f"✅ Отмечено: {vaccine_name}")
    await callback.answer("Готово!")


async def main():
    database.init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Планировщик, который раз в день шлёт напоминания (см. scheduler.py)
    setup_scheduler(bot)

    logging.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

            
