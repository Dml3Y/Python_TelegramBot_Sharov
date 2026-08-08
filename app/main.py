import re
import os
import os.path
import datetime

import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters


updater = Updater(token="API_TOKEN")


class Calendar:
    def __init__(self):
        self.events = {}          # {event_id: {id, name, date, time, details}}
        self._next_id = 1         # для автоматической генерации ID

    def create_event(self, event_name, event_date, event_time, event_details):
        event_id = self._next_id
        self._next_id += 1
        event = {
            "id": event_id,
            "name": event_name,
            "date": event_date,
            "time": event_time,
            "details": event_details
        }
        self.events[event_id] = event
        return event_id

    def read_event(self, event_name):
        """
        Возвращает словарь события по его имени.
        Если найдено несколько событий с одинаковым именем – возвращает первое.
        Если не найдено – возвращает None.
        """
        for ev in self.events.values():
            if ev["name"] == event_name:
                return ev
        return None

    def edit_event(self, event_id, event_name=None, event_date=None,
                   event_time=None, event_details=None):
        """
        Обновляет поля события с заданным ID.
        Если какое-то поле не передано – оно не меняется.
        Возвращает True в случае успеха, False – если событие не найдено.
        """
        if event_id not in self.events:
            return False
        ev = self.events[event_id]
        if event_name is not None:
            ev["name"] = event_name
        if event_date is not None:
            ev["date"] = event_date
        if event_time is not None:
            ev["time"] = event_time
        if event_details is not None:
            ev["details"] = event_details
        return True

    def delete_event(self, event_id):
        """
        Удаляет событие по ID.
        Возвращает True, если удаление выполнено, иначе False.
        """
        if event_id in self.events:
            del self.events[event_id]
            return True
        return False

    def display_event(self, event_name=None):
        """
        Если передано имя события – возвращает строку с его данными.
        Если имя не указано – возвращает строку со всеми событиями.
        """
        if event_name is not None:
            ev = self.read_event(event_name)
            if ev is None:
                return f"Событие с именем '{event_name}' не найдено."
            return (f"ID: {ev['id']}\n"
                    f"Название: {ev['name']}\n"
                    f"Дата: {ev['date']}\n"
                    f"Время: {ev['time']}\n"
                    f"Подробности: {ev['details']}")
        else:
            if not self.events:
                return "Список событий пуст."
            lines = ["Список событий:"]
            for ev in self.events.values():
                lines.append(
                    f"ID: {ev['id']}, {ev['name']}, {ev['date']} {ev['time']} - {ev['details']}"
                )
            return "\n".join(lines)

    # Можно также переопределить display_events как синоним display_event без аргументов
    def display_events(self):
        return self.display_event()   # возвращает строку со всеми событиями

# Зададим глобально доступный объект календаря
calendar = Calendar()

# Обновлённый обработчик создания события (принимает все параметры через |)
def create_event_handler(update, context):
    try:
        text = update.message.text
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Укажите данные в формате: /create_event Название|Дата|Время|Описание"
            )
            return
        args = parts[1].split('|')
        if len(args) < 4:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Недостаточно данных. Формат: Название|Дата|Время|Описание"
            )
            return
        event_name = args[0].strip()
        event_date = args[1].strip()
        event_time = args[2].strip()
        event_details = args[3].strip()
        event_id = calendar.create_event(event_name, event_date, event_time, event_details)
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"Событие '{event_name}' создано с ID {event_id}."
        )
    except Exception:
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text="При создании события произошла ошибка."
        )

# Чтение события по имени
def read_event_handler(update, context):
    try:
        text = update.message.text
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            context.bot.send_message(
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
            context.bot.send_message(chat_id=update.message.chat_id, text=msg)
        else:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с названием '{event_name}' не найдено."
            )
    except Exception:
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Произошла ошибка при чтении события."
        )

# Редактирование события по ID (поля через |, пустые поля – не меняются)
def edit_event_handler(update, context):
    try:
        text = update.message.text
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Укажите ID и новые данные через |. Пример: /edit_event 1|Новое название|2023-03-15|15:30|Новое описание"
            )
            return
        args = parts[1].split('|')
        if len(args) < 5:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Недостаточно данных. Формат: ID|Название|Дата|Время|Детали (можно пропускать, но сохранять порядок)"
            )
            return
        try:
            event_id = int(args[0].strip())
        except ValueError:
            context.bot.send_message(chat_id=update.message.chat_id, text="ID должно быть числом.")
            return
        new_name = args[1].strip() if args[1].strip() else None
        new_date = args[2].strip() if args[2].strip() else None
        new_time = args[3].strip() if args[3].strip() else None
        new_details = args[4].strip() if args[4].strip() else None
        success = calendar.edit_event(event_id, new_name, new_date, new_time, new_details)
        if success:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} обновлено."
            )
        else:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} не найдено."
            )
    except Exception:
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Произошла ошибка при редактировании."
        )

# Удаление события по ID
def delete_event_handler(update, context):
    try:
        text = update.message.text
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Укажите ID события. Пример: /delete_event 1"
            )
            return
        try:
            event_id = int(parts[1].strip())
        except ValueError:
            context.bot.send_message(chat_id=update.message.chat_id, text="ID должно быть числом.")
            return
        success = calendar.delete_event(event_id)
        if success:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} удалено."
            )
        else:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Событие с ID {event_id} не найдено."
            )
    except Exception:
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Произошла ошибка при удалении."
        )

# Отображение всех событий
def display_events_handler(update, context):
    try:
        events_str = calendar.display_events()
        context.bot.send_message(chat_id=update.message.chat_id, text=events_str)
    except Exception:
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Произошла ошибка при выводе событий."
        )

updater.dispatcher.add_handler(CommandHandler('create_event', create_event_handler))
updater.dispatcher.add_handler(CommandHandler('read_event', read_event_handler))
updater.dispatcher.add_handler(CommandHandler('edit_event', edit_event_handler))
updater.dispatcher.add_handler(CommandHandler('delete_event', delete_event_handler))
updater.dispatcher.add_handler(CommandHandler('display_events', display_events_handler))


def main():
    # Запуск бота (обработчики уже зарегистрированы, объект calendar существует глобально)
    updater.start_polling()
    updater.idle()  # ожидание остановки бота

if __name__ == "__main__":
    main()