import sqlite3
import os
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
import logging

# Налаштування логування
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Отримання токена з змінних середовища
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("Токен не знайдено! Встановіть змінну середовища TOKEN.")

DB_FILE = "bot.db"  # Файл бази даних SQLite

user_states = {}

# --- База даних --- #

def init_db() -> None:
    """Ініціалізація бази даних"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Таблиця для справ
    c.execute('''CREATE TABLE IF NOT EXISTS tasks 
                 (user_id TEXT, day TEXT, task_text TEXT, done INTEGER)''')
    # Таблиця для нагадувань
    c.execute('''CREATE TABLE IF NOT EXISTS reminders 
                 (user_id TEXT PRIMARY KEY, interval TEXT)''')
    conn.commit()
    conn.close()

def load_tasks(user_id: str) -> dict:
    """Завантаження справ користувача"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT day, task_text, done FROM tasks WHERE user_id = ?", (user_id,))
    rows = c.fetchall()
    conn.close()
    
    tasks = {"today": [], "tomorrow": []}
    for day, text, done in rows:
        tasks[day].append({"text": text, "done": bool(done)})
    return tasks

def save_task(user_id: str, day: str, task_text: str, done: bool = False) -> None:
    """Збереження справи"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO tasks (user_id, day, task_text, done) VALUES (?, ?, ?, ?)",
              (user_id, day, task_text, int(done)))
    conn.commit()
    conn.close()

def load_reminder(user_id: str) -> str:
    """Завантаження налаштувань нагадувань"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT interval FROM reminders WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else "off"

def save_reminder(user_id: str, interval: str) -> None:
    """Збереження налаштувань нагадувань"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO reminders (user_id, interval) VALUES (?, ?)",
              (user_id, interval))
    conn.commit()
    conn.close()

# --- Головне меню --- #

def get_main_menu() -> InlineKeyboardMarkup:
    """Створення головного меню з кнопками"""
    keyboard = [
        [InlineKeyboardButton("Додати справу на сьогодні", callback_data="add_today")],
        [InlineKeyboardButton("Додати справу на завтра", callback_data="add_tomorrow")],
        [InlineKeyboardButton("Переглянути заплановане", callback_data="list_tasks")],
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Команди --- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробник команди /start"""
    await update.message.reply_text(
        "Привіт! Я бот для списку справ.\nОберіть дію:",
        reply_markup=get_main_menu()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробник натискань кнопок"""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    if query.data == "add_today":
        user_states[user_id] = "awaiting_today_task"
        await query.edit_message_text("Введіть справу на сьогодні:")
    elif query.data == "add_tomorrow":
        user_states[user_id] = "awaiting_tomorrow_task"
        await query.edit_message_text("Введіть справу на завтра:")
    elif query.data == "list_tasks":
        await show_task_list(query, user_id)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробка текстових повідомлень для додавання справ"""
    user_id = str(update.effective_user.id)
    task_text = update.message.text

    if user_id in user_states:
        if user_states[user_id] == "awaiting_today_task":
            save_task(user_id, "today", task_text)
            await update.message.reply_text(f"✅ Додано на сьогодні: {task_text}", reply_markup=get_main_menu())
        elif user_states[user_id] == "awaiting_tomorrow_task":
            save_task(user_id, "tomorrow", task_text)
            await update.message.reply_text(f"✅ Додано на завтра: {task_text}", reply_markup=get_main_menu())
        del user_states[user_id]
    else:
        await update.message.reply_text("Оберіть дію:", reply_markup=get_main_menu())

async def show_task_list(query, user_id: str) -> None:
    """Показ списку справ"""
    tasks = load_tasks(user_id)
    today_tasks = tasks["today"]
    tomorrow_tasks = tasks["tomorrow"]
    
    response = ""
    if today_tasks:
        response += "📅 Сьогодні:\n" + "\n".join(
            f"{i+1}. {'✔️' if t['done'] else '❌'} {t['text']}" for i, t in enumerate(today_tasks)
        ) + "\n\n"
    if tomorrow_tasks:
        response += "📆 Завтра:\n" + "\n".join(
            f"{len(today_tasks)+i+1}. {'✔️' if t['done'] else '❌'} {t['text']}" for i, t in enumerate(tomorrow_tasks)
        )
    
    if not response:
        response = "🗒 Немає справ."
    await query.edit_message_text(response, reply_markup=get_main_menu())

# --- Нагадування --- #

async def set_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Меню налаштування нагадувань"""
    text = (
        "⏰ Як часто нагадувати про невиконані справи?\n"
        "/remind_1h – кожну годину\n"
        "/remind_2h – кожні 2 години\n"
        "/remind_off – не нагадувати"
    )
    await update.message.reply_text(text)

async def set_reminder_interval(update: Update, context: ContextTypes.DEFAULT_TYPE, interval: str) -> None:
    """Встановлення інтервалу нагадувань"""
    user_id = str(update.effective_user.id)
    save_reminder(user_id, interval)
    label = {"1h": "кожну годину", "2h": "кожні 2 години", "off": "не нагадувати"}[interval]
    await update.message.reply_text(f"🔔 Нагадування встановлено: {label}", reply_markup=get_main_menu())

async def send_reminders(app) -> None:
    """Відправка нагадувань користувачам"""
    try:
        now = datetime.datetime.now().time()
        if not (datetime.time(9, 0) <= now <= datetime.time(22, 0)):
            return

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT user_id, interval FROM reminders WHERE interval != 'off'")
        reminders = c.fetchall()
        conn.close()

        for user_id, _ in reminders:
            tasks = load_tasks(user_id)
            today_incomplete = [t["text"] for t in tasks["today"] if not t["done"]]
            tomorrow_incomplete = [t["text"] for t in tasks["tomorrow"] if not t["done"]]
            
            if today_incomplete or tomorrow_incomplete:
                text = "🔔 Ви ще не виконали:\n"
                if today_incomplete:
                    text += "📅 Сьогодні:\n" + "\n".join(f"❌ {t}" for t in today_incomplete) + "\n"
                if tomorrow_incomplete:
                    text += "📆 Завтра:\n" + "\n".join(f"❌ {t}" for t in tomorrow_incomplete)
                await app.bot.send_message(chat_id=int(user_id), text=text)
    except Exception as e:
        logger.error(f"Помилка при відправці нагадувань: {e}")

# --- Запуск --- #

async def main() -> None:
    """Основна функція запуску бота"""
    try:
        init_db()  # Ініціалізація бази даних
        app = ApplicationBuilder().token(TOKEN).build()

        # Додавання обробників
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(button_handler))
        app.add_handler(CommandHandler("remind", set_reminder))
        app.add_handler(CommandHandler("remind_1h", lambda u, c: set_reminder_interval(u, c, "1h")))
        app.add_handler(CommandHandler("remind_2h", lambda u, c: set_reminder_interval(u, c, "2h")))
        app.add_handler(CommandHandler("remind_off", lambda u, c: set_reminder_interval(u, c, "off")))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

        # Налаштування планувальника
        scheduler = AsyncIOScheduler()
        scheduler.add_job(lambda: send_reminders(app), "interval", hours=1)
        scheduler.start()

        logger.info("Бот запущено успішно на Render")

        # Запуск бота
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        # Тримаємо бота запущеним
        while True:
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"Помилка в main: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())