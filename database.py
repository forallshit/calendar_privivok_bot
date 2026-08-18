# -*- coding: utf-8 -*-
"""
Простая база данных на SQLite (файл на диске, не требует установки сервера).
Хранит: пользователей Telegram и их детей (имя + дата рождения).
"""

import sqlite3
from contextlib import contextmanager

DB_PATH = "privivki_bot.db"


def init_db():
    """Создаёт таблицы, если их ещё нет. Вызывается один раз при старте бота."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS children (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                birth_date TEXT NOT NULL
            )
        """)
        conn.commit()


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def add_child(telegram_user_id: int, name: str, birth_date: str):
    """birth_date в формате YYYY-MM-DD"""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO children (telegram_user_id, name, birth_date) VALUES (?, ?, ?)",
            (telegram_user_id, name, birth_date),
        )
        conn.commit()


def get_children(telegram_user_id: int):
    """Возвращает список детей конкретного пользователя."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id, name, birth_date FROM children WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        return cursor.fetchall()


def get_all_children():
    """Возвращает всех детей всех пользователей — нужно для планировщика напоминаний."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT telegram_user_id, name, birth_date FROM children"
        )
        return cursor.fetchall()


def delete_child(child_id: int, telegram_user_id: int):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM children WHERE id = ? AND telegram_user_id = ?",
            (child_id, telegram_user_id),
        )
        conn.commit()
