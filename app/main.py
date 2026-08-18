import re
import os
import os.path
import datetime

import telegram
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackContext
)

import psycopg2

# Импортируем секреты
from secrets import API_TOKEN, DB_CONFIG

# Подключение к БД
conn = psycopg2.connect(**DB_CONFIG)

# Создаём таблицы, если их нет
with conn.cursor() as cur:
    # Таблица пользователей
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            username TEXT,
            registered_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # Таблица событий (создаём, если нет)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            date DATE NOT NULL,
            time TIME NOT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # Проверяем, существует ли столбец user_id в events
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='events' AND column_name='user_id';
    """)
    if not cur.fetchone():
        # Добавляем столбец user_id и внешний ключ
        cur.execute("""
            ALTER TABLE events ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
        """)
        print("Добавлен столбец user_id в таблицу events")

    conn.commit()


# Вспомогательная функция: получить или создать пользователя
def get_or_create_user(telegram_id: int, username: str = None) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE telegram_id = %s;", (telegram_id,))
        row = cur.fetchone()
        if row:
            return row[0]
        else:
            cur.execute(
                "INSERT INTO users (telegram_id, username) VALUES (%s, %s) RETURNING id;",
                (telegram_id, username)
            )
            user_id = cur.fetchone()[0]
            conn.commit()
            return user_id


# Класс Calendar – теперь все методы принимают telegram_id
class Calendar:
    def __init__(self, db_connection):
        self.conn = db_connection

    def _get_user_id(self, telegram_id):
        """Возвращает внутренний ID пользователя по telegram_id (авторегистрация)"""
        return get_or_create_user(telegram_id)

    def create_event(self, telegram_id, event_name, event_date, event_time, event_details):
        user_id = self._get_user_id(telegram_id)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events (user_id, name, date, time, details) VALUES (%s, %s, %s, %s, %s) RETURNING id;",
                (user_id, event_name, event_date, event_time, event_details)
            )
            event_id = cur.fetchone()[0]
            self.conn.commit()
            return event_id

    def read_event(self, telegram_id, event_name):
        user_id = self._get_user_id(telegram_id)
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, date, time, details FROM events 
                   WHERE user_id = %s AND name = %s;""",
                (user_id, event_name)
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

    def edit_event(self, telegram_id, event_id, event_name=None, event_date=None,
                   event_time=None, event_details=None):
        user_id = self._get_user_id(telegram_id)
        # Проверяем, что событие принадлежит пользователю
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM events WHERE id = %s AND user_id = %s;", (event_id, user_id))
            if not cur.fetchone():
                return False  # событие не найдено или не принадлежит пользователю

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
        params.append(user_id)
        query = f"UPDATE events SET {', '.join(updates)} WHERE id = %s AND user_id = %s;"
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            self.conn.commit()
            return cur.rowcount > 0

    def delete_event(self, telegram_id, event_id):
        user_id = self._get_user_id(telegram_id)
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM events WHERE id = %s AND user_id = %s;", (event_id, user_id))
            self.conn.commit()
            return cur.rowcount > 0

    def display_events(self, telegram_id):
        user_id = self._get_user_id(telegram_id)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, date, time, details FROM events WHERE user_id = %s ORDER BY date, time;",
                (user_id,)
            )
            rows = cur.fetchall()
            if not rows:
                return "Список событий пуст."
            lines = ["Список событий:"]
            for row in rows:
                lines.append(
                    f"ID: {row[0]}, {row[1]}, {row[2]} {row[3]} - {row[4] if row[4] else ''}"
                )
            return "\n".join(lines)

    def display_event(self, telegram_id, event_name=None):
        if event_name is None:
            return self.display_events(telegram_id)
        event = self.read_event(telegram_id, event_name)
        if event is None:
            return f"Событие с именем '{event_name}' не найдено."
        return (f"ID: {event['id']}\n"
                f"Название: {event['name']}\n"
                f"Дата: {event['date']}\n"
                f"Время: {event['time']}\n"
                f"Подробности: {event['details']}")


# Создаём календарь с подключением
calendar = Calendar(conn)

# Настройка HTTP-клиента с увеличенными тайм-аутами (в секундах)
request = HTTPXRequest(
    connect_timeout=30.0,
    read_timeout=30.0,
    write_timeout=30.0,
)

# Создаём приложение
application = Application.builder().token(API_TOKEN).build()

# ---------- Состояния для диалога создания события ----------
NAME, DATE, TIME, DETAILS = range(4)


# ---------- Команда /register ----------
async def register_command(update: Update, context: CallbackContext):
    user = update.effective_user
    telegram_id = user.id
    username = user.username or "Unknown"
    get_or_create_user(telegram_id, username)
    await update.message.reply_text("✅ Вы успешно зарегистрированы!")


# ---------- Диалог создания события ----------
async def start_create(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "Введите **название** события (или /cancel для отмены):"
    )
    return NAME


async def get_name(update: Update, context: CallbackContext):
    context.user_data['event_name'] = update.message.text
    await update.message.reply_text(
        "Введите **дату** события в формате ГГГГ-ММ-ДД (например, 2025-03-20):"
    )
    return DATE


async def get_date(update: Update, context: CallbackContext):
    context.user_data['event_date'] = update.message.text
    await update.message.reply_text(
        "Введите **время** события в формате ЧЧ:ММ (например, 15:30):"
    )
    return TIME


async def get_time(update: Update, context: CallbackContext):
    context.user_data['event_time'] = update.message.text
    await update.message.reply_text(
        "Введите **описание** события (или отправьте '-' чтобы пропустить):"
    )
    return DETAILS


async def get_details(update: Update, context: CallbackContext):
    details = update.message.text
    if details.strip() == '-':
        details = ''
    context.user_data['event_details'] = details

    # Получаем данные из контекста
    name = context.user_data['event_name']
    date = context.user_data['event_date']
    time = context.user_data['event_time']
    details = context.user_data['event_details']

    # Создаём событие
    telegram_id = update.effective_user.id
    try:
        event_id = calendar.create_event(telegram_id, name, date, time, details)
        await update.message.reply_text(
            f"✅ Событие '{name}' создано с ID {event_id}."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при создании: {e}")

    # Очищаем данные
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: CallbackContext):
    await update.message.reply_text("❌ Создание события отменено.")
    context.user_data.clear()
    return ConversationHandler.END


# ---------- Остальные обработчики (с фильтрацией по пользователю) ----------
async def read_event_handler(update: Update, context: CallbackContext):
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
        telegram_id = update.effective_user.id
        event = calendar.read_event(telegram_id, event_name)
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
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"Произошла ошибка при чтении: {e}"
        )


async def edit_event_handler(update: Update, context: CallbackContext):
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
        telegram_id = update.effective_user.id
        success = calendar.edit_event(telegram_id, event_id, new_name, new_date, new_time, new_details)
        if success:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} обновлено."
            )
        else:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} не найдено или не принадлежит вам."
            )
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"Произошла ошибка при редактировании: {e}"
        )


async def delete_event_handler(update: Update, context: CallbackContext):
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
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="ID должно быть числом."
            )
            return
        telegram_id = update.effective_user.id
        success = calendar.delete_event(telegram_id, event_id)
        if success:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} удалено."
            )
        else:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} не найдено или не принадлежит вам."
            )
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"Произошла ошибка при удалении: {e}"
        )


async def display_events_handler(update: Update, context: CallbackContext):
    try:
        telegram_id = update.effective_user.id
        events_str = calendar.display_events(telegram_id)
        await context.bot.send_message(chat_id=update.message.chat_id, text=events_str)
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"Произошла ошибка при выводе: {e}"
        )


# Регистрируем обработчики

# Команда регистрации
application.add_handler(CommandHandler('register', register_command))

# Диалог создания события
conv_handler = ConversationHandler(
    entry_points=[CommandHandler('create', start_create)],
    states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
        DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
        TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
        DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_details)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv_handler)

# Остальные команды (с разделителями)
application.add_handler(CommandHandler('read_event', read_event_handler))
application.add_handler(CommandHandler('edit_event', edit_event_handler))
application.add_handler(CommandHandler('delete_event', delete_event_handler))
application.add_handler(CommandHandler('display_events', display_events_handler))

# Можно добавить команду /start для приветствия и авторегистрации
async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    telegram_id = user.id
    username = user.username or "Unknown"
    get_or_create_user(telegram_id, username)
    await update.message.reply_text(
        "👋 Привет! Я бот-календарь.\n"
        "Зарегистрируйся командой /register (если ещё не зарегистрирован).\n"
        "Создать событие можно через /create (диалог) или /create_event (разделители).\n"
        "Доступные команды:\n"
        "/create – диалоговое создание\n"
        "/create_event Название|Дата|Время|Описание – быстрый способ\n"
        "/read_event Название – показать событие\n"
        "/edit_event ID|Новое название|Новая дата|Новое время|Новое описание\n"
        "/delete_event ID – удалить\n"
        "/display_events – список всех событий\n"
        "/register – регистрация"
    )
application.add_handler(CommandHandler('start', start))

# Оставляем старую команду /create_event для быстрого создания (без диалога)
async def quick_create_event_handler(update: Update, context: CallbackContext):
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
        telegram_id = update.effective_user.id
        event_id = calendar.create_event(telegram_id, event_name, event_date, event_time, event_details)
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"Событие '{event_name}' создано с ID {event_id}."
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"При создании события произошла ошибка: {e}"
        )
application.add_handler(CommandHandler('create_event', quick_create_event_handler))


def main():
    application.run_polling()


if __name__ == "__main__":
    main()