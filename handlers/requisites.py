from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database import db
from keyboards.main import cancel_requisites_menu, requisites_menu
from states.requisites import RequisitesStates
from texts.i18n import ask_requisite_text, requisites_text, saved_requisite_text, t
from utils.lang import get_lang

router = Router()

TON_RE = re.compile(r"^(UQ|EQ|0:)[A-Za-z0-9_-]{46,}$|^[A-Za-z0-9_-]{48}$")
CARD_RE = re.compile(r"^\d{13,19}$")
USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{5,32}$")


def _payout_username(user) -> str | None:
    if not user:
        return None
    try:
        value = user["payout_username"]
    except (KeyError, IndexError, TypeError):
        return None
    return value or None


async def show_requisites(target: Message | CallbackQuery) -> None:
    lang = await get_lang(target.from_user.id)
    user = await db.get_user(target.from_user.id)
    text = requisites_text(
        lang,
        ton_wallet=user["ton_wallet"] if user else None,
        card_number=user["card_number"] if user else None,
        payout_username=_payout_username(user),
    )
    markup = requisites_menu(lang)
    if isinstance(target, CallbackQuery):
        msg = target.message
        if msg.photo:
            await msg.edit_caption(caption=text, reply_markup=markup)
        else:
            await msg.edit_text(text, reply_markup=markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup)


async def _ask(callback: CallbackQuery, state: FSMContext, *, state_name, text_key: str) -> None:
    lang = await get_lang(callback.from_user.id)
    await state.set_state(state_name)
    text = ask_requisite_text(lang, text_key)
    markup = cancel_requisites_menu(lang)
    if callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=markup)
    else:
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "menu:requisites")
async def menu_requisites(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_requisites(callback)


@router.callback_query(F.data == "req:ton")
async def ask_ton(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask(callback, state, state_name=RequisitesStates.waiting_ton, text_key="ask_ton")


@router.callback_query(F.data == "req:card")
async def ask_card(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask(callback, state, state_name=RequisitesStates.waiting_card, text_key="ask_card")


@router.callback_query(F.data == "req:username")
async def ask_username(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask(callback, state, state_name=RequisitesStates.waiting_username, text_key="ask_username")


@router.callback_query(F.data == "req:cancel")
async def cancel_requisites(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_requisites(callback)


@router.message(RequisitesStates.waiting_ton, F.text)
async def save_ton(message: Message, state: FSMContext) -> None:
    lang = await get_lang(message.from_user.id)
    wallet = message.text.strip()
    if not TON_RE.match(wallet):
        await message.answer(
            "❌ Invalid TON address." if lang == "en" else "❌ Неверный адрес TON.",
            reply_markup=cancel_requisites_menu(lang),
        )
        return

    await db.upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )
    await db.set_ton_wallet(message.from_user.id, wallet)
    await state.clear()
    await message.answer(saved_requisite_text(lang, "saved_ton"))
    await show_requisites(message)


@router.message(RequisitesStates.waiting_card, F.text)
async def save_card(message: Message, state: FSMContext) -> None:
    lang = await get_lang(message.from_user.id)
    digits = re.sub(r"\D", "", message.text)
    if not CARD_RE.match(digits):
        await message.answer(
            "❌ Invalid card number." if lang == "en" else "❌ Неверный номер карты.",
            reply_markup=cancel_requisites_menu(lang),
        )
        return

    await db.upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )
    await db.set_card_number(message.from_user.id, digits)
    await state.clear()
    await message.answer(saved_requisite_text(lang, "saved_card"))
    await show_requisites(message)


@router.message(RequisitesStates.waiting_username, F.text)
async def save_username(message: Message, state: FSMContext) -> None:
    lang = await get_lang(message.from_user.id)
    raw = message.text.strip()
    if not USERNAME_RE.match(raw):
        await message.answer(
            "❌ Invalid username." if lang == "en" else "❌ Неверный юзернейм.",
            reply_markup=cancel_requisites_menu(lang),
        )
        return

    username = raw.lstrip("@")
    await db.upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )
    await db.set_payout_username(message.from_user.id, username)
    await state.clear()
    await message.answer(saved_requisite_text(lang, "saved_username"))
    await show_requisites(message)
