from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database import db
from states.admin import AdminStates
from utils.admin_access import is_admin, is_super_admin

router = Router()

CURRENCY_LABELS = {
    "ton": "💎 TON",
    "rub": "💵 RUB",
    "stars": "⭐ Stars",
}


def admin_menu(user_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="💎 Начислить TON", callback_data="admin:add:ton")],
        [InlineKeyboardButton(text="💵 Начислить RUB", callback_data="admin:add:rub")],
        [InlineKeyboardButton(text="⭐ Начислить Stars", callback_data="admin:add:stars")],
        [InlineKeyboardButton(text="👤 Мой баланс", callback_data="admin:balance")],
    ]
    if user_id is not None and is_super_admin(user_id):
        rows.extend(
            [
                [InlineKeyboardButton(text="➕ Добавить воркера", callback_data="admin:grant")],
                [InlineKeyboardButton(text="🚫 Забанить", callback_data="admin:ban")],
                [InlineKeyboardButton(text="✅ Разбанить", callback_data="admin:unban")],
            ]
        )
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="admin:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin:cancel")]]
    )


def _balance_text(user) -> str:
    if not user:
        return "💎 TON: 0.00\n💵 RUB: 0.00\n⭐ Stars: 0"
    return (
        f"💎 TON: {float(user['balance_ton'] or 0):.2f}\n"
        f"💵 RUB: {float(user['balance_rub'] or 0):.2f}\n"
        f"⭐ Stars: {int(user['balance_stars'] or 0)}"
    )


async def _deny_if_not_admin(event: Message | CallbackQuery) -> bool:
    uid = event.from_user.id
    if await is_admin(uid):
        return False
    if isinstance(event, CallbackQuery):
        await event.answer("Нет доступа", show_alert=True)
    else:
        await event.answer(
            "❌ Нет доступа.\n"
            f"Твой ID: <code>{uid}</code>"
        )
    return True


async def _deny_if_not_super_admin(event: Message | CallbackQuery) -> bool:
    if await _deny_if_not_admin(event):
        return True
    uid = event.from_user.id
    if is_super_admin(uid):
        return False
    if isinstance(event, CallbackQuery):
        await event.answer("Нет доступа", show_alert=True)
    else:
        await event.answer("❌ Нет доступа к этой функции.")
    return True


async def _parse_telegram_id(message: Message) -> int | None:
    raw = message.text.strip()
    if not raw.isdigit():
        await message.answer("❌ Нужен числовой Telegram ID.", reply_markup=admin_cancel())
        return None
    user_id = int(raw)
    if user_id <= 0:
        await message.answer("❌ Некорректный ID.", reply_markup=admin_cancel())
        return None
    return user_id


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if await _deny_if_not_admin(message):
        return
    await state.clear()
    await message.answer(
        "🛠 <b>Админ-панель</b>\n\nВыберите действие:",
        reply_markup=admin_menu(message.from_user.id),
    )


@router.callback_query(F.data == "menu:admin")
async def menu_admin(callback: CallbackQuery, state: FSMContext) -> None:
    if await _deny_if_not_admin(callback):
        return
    await state.clear()
    await callback.message.answer(
        "🛠 <b>Админ-панель</b>\n\nВыберите действие:",
        reply_markup=admin_menu(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:close")
async def admin_close(callback: CallbackQuery, state: FSMContext) -> None:
    if await _deny_if_not_admin(callback):
        return
    await state.clear()
    await callback.message.edit_text("Админ-панель закрыта.")
    await callback.answer()


@router.callback_query(F.data == "admin:cancel")
async def admin_cancel_cb(callback: CallbackQuery, state: FSMContext) -> None:
    if await _deny_if_not_admin(callback):
        return
    await state.clear()
    await callback.message.edit_text(
        "🛠 <b>Админ-панель</b>\n\nВыберите действие:",
        reply_markup=admin_menu(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:add:"))
async def admin_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if await _deny_if_not_admin(callback):
        return
    currency = callback.data.split(":")[-1]
    if currency not in CURRENCY_LABELS:
        await callback.answer("Неизвестная валюта", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_amount)
    await state.update_data(currency=currency, target_id=callback.from_user.id)
    await callback.message.edit_text(
        f"Начисление {CURRENCY_LABELS[currency]} себе\n\n"
        "Введите сумму начисления\n"
        "(можно отрицательную, чтобы списать)",
        reply_markup=admin_cancel(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:balance")
async def admin_balance_view(callback: CallbackQuery, state: FSMContext) -> None:
    if await _deny_if_not_admin(callback):
        return
    await state.clear()
    user = await db.get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"👤 <b>Ваш баланс</b>\n\n{_balance_text(user)}",
        reply_markup=admin_menu(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:grant")
async def admin_grant_start(callback: CallbackQuery, state: FSMContext) -> None:
    if await _deny_if_not_super_admin(callback):
        return
    await state.set_state(AdminStates.waiting_admin_id)
    await callback.message.edit_text(
        "➕ <b>Добавить воркера</b>\n\n"
        "Отправьте Telegram ID пользователя числом\n"
        "(например <code>123456789</code>)",
        reply_markup=admin_cancel(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_admin_id, F.text)
async def admin_got_admin_id(message: Message, state: FSMContext) -> None:
    if await _deny_if_not_super_admin(message):
        return

    new_id = await _parse_telegram_id(message)
    if new_id is None:
        return

    added = await db.add_admin(new_id)
    await state.clear()

    if added:
        text = f"✅ Пользователь <code>{new_id}</code> добавлен как воркер."
        try:
            await message.bot.send_message(
                new_id,
                "🛠 Вам выдан доступ к <b>админ-панели</b> бота.\n"
                "Напишите /start — в меню появится кнопка «Админ-панель».",
            )
        except Exception:
            text += "\n\n⚠️ Не удалось уведомить пользователя (он ещё не писал боту)."
    else:
        text = f"ℹ️ <code>{new_id}</code> уже является воркером."

    admins = await db.list_admins()
    ids_line = ", ".join(f"<code>{a}</code>" for a in admins)
    await message.answer(
        f"{text}\n\nТекущие воркеры:\n{ids_line}",
        reply_markup=admin_menu(message.from_user.id),
    )


@router.callback_query(F.data == "admin:ban")
async def admin_ban_start(callback: CallbackQuery, state: FSMContext) -> None:
    if await _deny_if_not_super_admin(callback):
        return
    await state.set_state(AdminStates.waiting_ban_id)
    await callback.message.edit_text(
        "🚫 <b>Забанить</b>\n\n"
        "Отправьте Telegram ID пользователя числом\n"
        "(например <code>123456789</code>)",
        reply_markup=admin_cancel(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_ban_id, F.text)
async def admin_got_ban_id(message: Message, state: FSMContext) -> None:
    if await _deny_if_not_super_admin(message):
        return

    target_id = await _parse_telegram_id(message)
    if target_id is None:
        return

    if is_super_admin(target_id):
        await state.clear()
        await message.answer(
            "❌ Нельзя забанить этого пользователя.",
            reply_markup=admin_menu(message.from_user.id),
        )
        return

    banned = await db.ban_user(target_id)
    await state.clear()
    if banned:
        text = f"🚫 Пользователь <code>{target_id}</code> забанен."
    else:
        text = f"ℹ️ <code>{target_id}</code> уже в бане."
    await message.answer(text, reply_markup=admin_menu(message.from_user.id))


@router.callback_query(F.data == "admin:unban")
async def admin_unban_start(callback: CallbackQuery, state: FSMContext) -> None:
    if await _deny_if_not_super_admin(callback):
        return
    await state.set_state(AdminStates.waiting_unban_id)
    await callback.message.edit_text(
        "✅ <b>Разбанить</b>\n\n"
        "Отправьте Telegram ID пользователя числом\n"
        "(например <code>123456789</code>)",
        reply_markup=admin_cancel(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_unban_id, F.text)
async def admin_got_unban_id(message: Message, state: FSMContext) -> None:
    if await _deny_if_not_super_admin(message):
        return

    target_id = await _parse_telegram_id(message)
    if target_id is None:
        return

    unbanned = await db.unban_user(target_id)
    await state.clear()
    if unbanned:
        text = f"✅ Пользователь <code>{target_id}</code> разбанен."
    else:
        text = f"ℹ️ <code>{target_id}</code> не был в бане."
    await message.answer(text, reply_markup=admin_menu(message.from_user.id))


@router.message(AdminStates.waiting_amount, F.text)
async def admin_got_amount(message: Message, state: FSMContext) -> None:
    if await _deny_if_not_admin(message):
        return

    raw = message.text.strip().replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("❌ Введите число.", reply_markup=admin_cancel())
        return

    if amount == 0:
        await message.answer("❌ Сумма не может быть 0.", reply_markup=admin_cancel())
        return

    data = await state.get_data()
    target_id = int(data.get("target_id") or message.from_user.id)
    currency = data.get("currency")
    if currency not in CURRENCY_LABELS:
        await state.clear()
        await message.answer(
            "Сессия сброшена. Откройте /admin снова.",
            reply_markup=admin_menu(message.from_user.id),
        )
        return

    if currency == "stars" and not float(amount).is_integer():
        await message.answer("❌ Stars должны быть целым числом.", reply_markup=admin_cancel())
        return

    user = await db.add_balance(target_id, currency, amount)
    await state.clear()

    sign = "+" if amount > 0 else ""
    await message.answer(
        f"✅ Готово\n\n"
        f"{CURRENCY_LABELS[currency]}: {sign}{amount:g}\n\n"
        f"Текущий баланс:\n"
        f"{_balance_text(user)}",
        reply_markup=admin_menu(message.from_user.id),
    )
