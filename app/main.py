import re
import os
import os.path
import datetime

import telegram
from telegram.ext import Application, CommandHandler

import psycopg2

# Импортируем секреты из отдельного файла
from secrets import API_TOKEN, DB_CONFIG


# Подключение к базе данных
conn = psycopg2.connect(**DB_CONFIG)

# Создаём таблицу events, если она ещё не существует
with conn.cursor() as cur:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            date DATE NOT NULL,
            time TIME NOT NULL,
            details TEXT
        );
    """)
    conn.commit()


class Calendar:
    def __init__(self, db_connection):
        self.conn = db_connection

    def create_event(self, event_name, event_date, event_time, event_details):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events (name, date, time, details) VALUES (%s, %s, %s, %s) RETURNING id;",
                (event_name, event_date, event_time, event_details)
            )
            event_id = cur.fetchone()[0]
            self.conn.commit()
            return event_id

    def read_event(self, event_name):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, date, time, details FROM events WHERE name = %s;",
                (event_name,)
            )
            row = cur.fetchone()
            if row:
                return {
                    "id": row[0],
                    "name": row[1],
                    "date": row[2],
                    "time": row[3],
                    "details": row[4]
                }
            return None

    def edit_event(self, event_id, event_name=None, event_date=None,
                   event_time=None, event_details=None):
        updates = []
        params = []
        if event_name is not None:
            updates.append("name = %s")
            params.append(event_name)
        if event_date is not None:
            updates.append("date = %s")
            params.append(event_date)
        if event_time is not None:
            updates.append("time = %s")
            params.append(event_time)
        if event_details is not None:
            updates.append("details = %s")
            params.append(event_details)

        if not updates:
            return True  # нечего менять

        params.append(event_id)
        query = f"UPDATE events SET {', '.join(updates)} WHERE id = %s;"
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            self.conn.commit()
            return cur.rowcount > 0

    def delete_event(self, event_id):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM events WHERE id = %s;", (event_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def display_events(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, name, date, time, details FROM events ORDER BY date, time;")
            rows = cur.fetchall()
            if not rows:
                return "Список событий пуст."
            lines = ["Список событий:"]
            for row in rows:
                lines.append(
                    f"ID: {row[0]}, {row[1]}, {row[2]} {row[3]} - {row[4] if row[4] else ''}"
                )
            return "\n".join(lines)

    def display_event(self, event_name=None):
        if event_name is None:
            return self.display_events()
        event = self.read_event(event_name)
        if event is None:
            return f"Событие с именем '{event_name}' не найдено."
        return (f"ID: {event['id']}\n"
                f"Название: {event['name']}\n"
                f"Дата: {event['date']}\n"
                f"Время: {event['time']}\n"
                f"Подробности: {event['details']}")


# Создаём экземпляр календаря с подключением к БД
calendar = Calendar(conn)

# Создаём экземпляр Updater с токеном из secrets.py
application = Application.builder().token(API_TOKEN).build()


# ---------- Обработчики команд (остаются без изменений) ----------
async def create_event_handler(update, context):
    try:
        text = update.message.text
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Укажите данные в формате: /create_event Название|Дата|Время|Описание"
            )
            return
        args = parts[1].split('|')
        if len(args) < 4:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Недостаточно данных. Формат: Название|Дата|Время|Описание"
            )
            return
        event_name = args[0].strip()
        event_date = args[1].strip()
        event_time = args[2].strip()
        event_details = args[3].strip()
        event_id = calendar.create_event(event_name, event_date, event_time, event_details)
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"Событие '{event_name}' создано с ID {event_id}."
        )
    except Exception:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text="При создании события произошла ошибка."
        )


async def read_event_handler(update, context):
    try:
        text = update.message.text
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Укажите название события. Пример: /read_event Встреча"
            )
            return
        event_name = parts[1].strip()
        event = calendar.read_event(event_name)
        if event:
            msg = (f"ID: {event['id']}\nНазвание: {event['name']}\n"
                   f"Дата: {event['date']}\nВремя: {event['time']}\n"
                   f"Детали: {event['details']}")
            await context.bot.send_message(chat_id=update.message.chat_id, text=msg)
        else:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с названием '{event_name}' не найдено."
            )
    except Exception:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Произошла ошибка при чтении события."
        )


async def edit_event_handler(update, context):
    try:
        text = update.message.text
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Укажите ID и новые данные через |. Пример: /edit_event 1|Новое название|2023-03-15|15:30|Новое описание"
            )
            return
        args = parts[1].split('|')
        if len(args) < 5:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Недостаточно данных. Формат: ID|Название|Дата|Время|Детали (можно пропускать, но сохранять порядок)"
            )
            return
        try:
            event_id = int(args[0].strip())
        except ValueError:
            await context.bot.send_message(chat_id=update.message.chat_id, text="ID должно быть числом.")
            return
        new_name = args[1].strip() if args[1].strip() else None
        new_date = args[2].strip() if args[2].strip() else None
        new_time = args[3].strip() if args[3].strip() else None
        new_details = args[4].strip() if args[4].strip() else None
        success = calendar.edit_event(event_id, new_name, new_date, new_time, new_details)
        if success:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} обновлено."
            )
        else:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} не найдено."
            )
    except Exception:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Произошла ошибка при редактировании."
        )


async def delete_event_handler(update, context):
    try:
        text = update.message.text
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Укажите ID события. Пример: /delete_event 1"
            )
            return
        try:
            event_id = int(parts[1].strip())
        except ValueError:
            await context.bot.send_message(chat_id=update.message.chat_id, text="ID должно быть числом.")
            return
        success = calendar.delete_event(event_id)
        if success:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} удалено."
            )
        else:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} не найдено."
            )
    except Exception:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Произошла ошибка при удалении."
        )


async def display_events_handler(update, context):
    try:
        events_str = calendar.display_events()
        await context.bot.send_message(chat_id=update.message.chat_id, text=events_str)
    except Exception:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Произошла ошибка при выводе событий."
        )


application.add_handler(CommandHandler('create_event', create_event_handler))
application.add_handler(CommandHandler('read_event', read_event_handler))
application.add_handler(CommandHandler('edit_event', edit_event_handler))
application.add_handler(CommandHandler('delete_event', delete_event_handler))
application.add_handler(CommandHandler('display_events', display_events_handler))


def main():
    application.run_polling()


if __name__ == "__main__":
    main()