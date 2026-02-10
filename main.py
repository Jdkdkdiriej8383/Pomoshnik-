# main.py
import asyncio
import sqlite3
import re
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# === НАСТРОЙКИ ИЗ ФАЙЛА config.py или ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===
import config

BOT_TOKEN = config.BOT_TOKEN
TEACHER_ID = config.TEACHER_ID
TEACHER_TIMEZONE_OFFSET = config.TEACHER_TIMEZONE_OFFSET
CHANNEL_ID = config.CHANNEL_ID

# === БОТ И ДИСПЕТЧЕР ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === БАЗА ДАННЫХ ===
conn = sqlite3.connect("school_bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        role TEXT,
        status TEXT,
        reason TEXT,
        approved INTEGER DEFAULT 0
    )
''')
conn.commit()

# === СОСТОЯНИЯ FSM ===
class Registration(StatesGroup):
    awaiting_name = State()
    awaiting_reason = State()
    awaiting_duty_name = State()
    awaiting_delete_name = State()
    awaiting_delete_confirm = State()

# === КЛАВИАТУРЫ ===
def get_student_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="✅ Приду в школу")],
        [KeyboardButton(text="❌ Не приду")],
        [KeyboardButton(text="🧹 Отчитаться о дежурстве")],
        [KeyboardButton(text="ℹ️ Помощь")]
    ])

def get_teacher_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="📋 Список класса")],
        [KeyboardButton(text="➕ Добавить дежурного")],
        [KeyboardButton(text="🗑️ Удалить ученика")],
        [KeyboardButton(text="📤 Повторить отчёт в канал")],
        [KeyboardButton(text="ℹ️ Помощь")]
    ])

def get_approval_kb(user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Согласить", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decline_{user_id}")
        ]
    ])

def get_confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить всё", callback_data="confirm_delete_all"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_delete")
        ]
    ])

# === ПРОВЕРКА ВЫХОДНЫХ ===
def is_weekend():
    return datetime.now().weekday() >= 5

# === ОТЧЁТЫ ===
async def send_teacher_report():
    if is_weekend(): return
    cursor.execute("SELECT name, status, reason FROM users WHERE role='student' AND approved=1")
    students = cursor.fetchall()
    if not students:
        await bot.send_message(TEACHER_ID, "📝 Нет зарегистрированных учеников.")
        return
    report = "\n".join([
        f"{name} — ✅ придёт" if s == "present" else
        f"{name} — ❌ не придёт ({r})" if s == "absent" else
        f"{name} — ❓ неизвестно"
        for name, s, r in students
    ])
    await bot.send_message(TEACHER_ID, f"📋 Отчёт по приходу (8:25):\n\n{report}")

async def send_channel_duty():
    if is_weekend(): return
    cursor.execute("SELECT name FROM users WHERE role='student' AND approved=1 AND status='present'")
    present = [r[0] for r in cursor.fetchall()]
    msg = f"🧹 Дежурства на сегодня:\nДежурит: {present[0]}" if present else "🧹 Дежурства на сегодня:\nНикто не приходит."
    try:
        await bot.send_message(CHANNEL_ID, msg)
    except Exception as e:
        print(f"[Ошибка] Канал: {e}")

# === ПЛАНИРОВЩИК ===
async def run_scheduler():
    while True:
        now = datetime.now()
        hour_local = (now.hour + TEACHER_TIMEZONE_OFFSET) % 24
        minute, second = now.minute, now.second
        if not is_weekend():
            if hour_local == 8 and minute == 25 and second < 10:
                await send_teacher_report()
                await asyncio.sleep(60)
            elif hour_local == 8 and minute == 45 and second < 10:
                await send_channel_duty()
                await asyncio.sleep(60)
        await asyncio.sleep(55)

# === /start ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id == TEACHER_ID:
        cursor.execute("INSERT OR IGNORE INTO users (user_id, name, role, approved) VALUES (?, 'Классный руководитель', 'teacher', 1)", (user_id,))
        conn.commit()
        await message.answer("👨‍🏫 Добро пожаловать!", reply_markup=get_teacher_kb())
        return
    cursor.execute("SELECT role, approved FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    if result:
        role, approved = result
        if role == "student":
            await message.answer("🎓 Добро пожаловать!" if approved else "⏳ На рассмотрении.", reply_markup=get_student_kb() if approved else None)
        return
    await message.answer("👋 Введите имя (например: Иван Иванов):")
    await state.set_state(Registration.awaiting_name)

# === Регистрация имени ===
@dp.message(Registration.awaiting_name)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not re.fullmatch(r"^[А-ЯЁA-Z][а-яёa-z]*(?:[- ][А-ЯЁA-Z][а-яёa-z]+)*$", name, re.IGNORECASE):
        await message.answer("📛 Имя: только буквы, пробелы, дефисы. Пример: Анна-Мария")
        return
    user_id = message.from_user.id
    cursor.execute("INSERT OR REPLACE INTO users (user_id, name, role, status) VALUES (?, ?, 'student', 'unknown')", (user_id, name))
    conn.commit()
    await bot.send_message(TEACHER_ID, f"🆕 Заявка:\nИмя: {name}\nЮзер: @{message.from_user.username or 'нет'}", reply_markup=get_approval_kb(user_id))
    await message.answer("📨 Заявка отправлена.")
    await state.clear()

# === Одобрение/отклонение ===
@dp.callback_query(F.data.startswith("approve_"))
async def approve_student(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    cursor.execute("UPDATE users SET approved=1 WHERE user_id=?", (user_id,))
    conn.commit()
    await bot.send_message(user_id, "✅ Вы приняты!", reply_markup=get_student_kb())
    await callback.message.edit_text(f"{callback.message.text}\n\n✅ Принято.")
    await callback.answer("Принято")

@dp.callback_query(F.data.startswith("decline_"))
async def decline_student(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    cursor.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    conn.commit()
    await bot.send_message(user_id, "❌ Отклонено.")
    await callback.message.edit_text(f"{callback.message.text}\n\n❌ Отклонено.")
    await callback.answer("Отклонено")

# === Учитель: Команды ===
@dp.message(F.text == "📋 Список класса")
async def list_students(message: types.Message):
    if message.from_user.id != TEACHER_ID: return
    cursor.execute("SELECT name, status, reason FROM users WHERE role='student' AND approved=1")
    lines = [
        f"{n} — ✅ идёт" if s == "present" else
        f"{n} — ❌ не идёт ({r})" if s == "absent" else
        f"{n} — ⏳ неизвестно"
        for n, s, r in cursor.fetchall()
    ]
    await message.answer("👥 Список класса:\n\n" + "\n".join(lines) if lines else "📚 Пусто.")

@dp.message(F.text == "➕ Добавить дежурного")
async def prompt_duty_name(message: types.Message, state: FSMContext):
    if message.from_user.id != TEACHER_ID: return
    await message.answer("✏️ Введите имя:")
    await state.set_state(Registration.awaiting_duty_name)

@dp.message(Registration.awaiting_duty_name)
async def set_duty(message: types.Message, state: FSMContext):
    name = message.text.strip()
    cursor.execute("SELECT user_id FROM users WHERE name=? AND approved=1", (name,))
    row = cursor.fetchone()
    if row:
        await bot.send_message(row[0], "🧹 Вы дежурный!")
        await message.answer(f"🧹 {name} назначен.")
    else:
        await message.answer("❌ Не найден.")
    await state.clear()

@dp.message(F.text == "🗑️ Удалить ученика")
async def prompt_delete_name(message: types.Message, state: FSMContext):
    if message.from_user.id != TEACHER_ID: return
    await message.answer("✏️ Введите имя или <code>@all</code>:", parse_mode="HTML")
    await state.set_state(Registration.awaiting_delete_name)

@dp.message(Registration.awaiting_delete_name)
async def delete_student(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if name == "@all":
        await message.answer("⚠️ Точно удалить всех?", reply_markup=get_confirm_kb(), parse_mode="HTML")
        await state.set_state(Registration.awaiting_delete_confirm)
    else:
        cursor.execute("DELETE FROM users WHERE name=? AND role='student'", (name,))
        conn.commit()
        await message.answer(f"✅ Удалён: {name}" if cursor.rowcount else "❌ Не найден.")
        await state.clear()

@dp.callback_query(F.data == "confirm_delete_all")
async def confirm_delete_all(callback: types.CallbackQuery, state: FSMContext):
    cursor.execute("DELETE FROM users WHERE role='student'")
    conn.commit()
    await callback.message.edit_text(f"✅ Удалено: {cursor.rowcount} учеников.")
    await callback.answer("Готово")
    await state.clear()

@dp.callback_query(F.data == "cancel_delete")
async def cancel_delete(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ Отменено")
    await callback.answer("Отмена")
    await state.clear()

@dp.message(F.text == "📤 Повторить отчёт в канал")
async def resend_channel_report(message: types.Message):
    if message.from_user.id != TEACHER_ID: return
    await send_channel_duty()
    await message.answer("📤 Отправлено в канал.")

# === Ученик: Команды ===
@dp.message(F.text == "✅ Приду в школу")
async def mark_present(message: types.Message):
    cursor.execute("UPDATE users SET status='present', reason=NULL WHERE user_id=?", (message.from_user.id,))
    conn.commit()
    await message.answer("✅ Приду")

@dp.message(F.text == "❌ Не приду")
async def prompt_absent_reason(message: types.Message, state: FSMContext):
    await message.answer("📝 Причина:")
    await state.set_state(Registration.awaiting_reason)

@dp.message(Registration.awaiting_reason)
async def mark_absent(message: types.Message, state: FSMContext):
    reason = message.text.strip()
    cursor.execute("UPDATE users SET status='absent', reason=? WHERE user_id=?", (reason, message.from_user.id))
    conn.commit()
    await message.answer(f"❌ Не приду: {reason}")
    await state.clear()

@dp.message(F.text == "🧹 Отчитаться о дежурстве")
async def report_duty(message: types.Message):
    await message.answer("🧹 Готово! 💪")

@dp.message(F.text == "ℹ️ Помощь")
async def help_command(message: types.Message):
    role = cursor.execute("SELECT role FROM users WHERE user_id=? AND approved=1", (message.from_user.id,)).fetchone()
    text = ("👨‍🏫 Учитель:\n• Список, дежурный, удалить, отчёт" if role and role[0]=="teacher" else
            "🎓 Ученик:\n• Приду/не приду, отчёт, помощь")
    await message.answer(text)

# === ЗАПУСК ===
async def main():
    asyncio.create_task(run_scheduler())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
