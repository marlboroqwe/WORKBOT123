import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import ASSETS_DIR, DEAL_MANAGER_USERNAME, MIN_COMPLETED_DEALS_WITHDRAW
from database import db
from keyboards.main import (
    back_menu,
    balance_menu,
    buyer_deal_kb,
    buyer_pay_kb,
    language_menu,
    main_menu,
    seller_deal_kb,
)
from states.balance import BalanceStates
from texts.deal_messages import (
    buyer_goods_ready_text,
    buyer_payment_accepted_text,
    buyer_seller_joined_text,
    deal_completed_text,
    seller_deal_connected_text,
)
from texts.i18n import (
    balance_text,
    deal_pay_labels,
    deal_type_labels,
    lang_text,
    t,
    welcome_text,
    withdraw_ask_text,
    withdraw_done_text,
)
from utils.admin_access import is_admin
from utils.emoji import ce
from utils.lang import get_lang

log = logging.getLogger(__name__)

router = Router()

BANNER_CANDIDATES = (
    ASSETS_DIR / "banner.jpg",
    ASSETS_DIR / "banner.png",
    ASSETS_DIR / "welcome.jpg",
    ASSETS_DIR / "welcome.png",
)


def _banner() -> FSInputFile | None:
    for path in BANNER_CANDIDATES:
        if path.is_file():
            return FSInputFile(path)
    return None


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    await state.clear()
    referrer_id = None
    args = (command.args or "").strip()
    lang = await get_lang(message.from_user.id)

    if args.startswith("deal_"):
        await _handle_deal_start(message, args.removeprefix("deal_"), lang)
        return

    if args.isdigit():
        rid = int(args)
        if rid != message.from_user.id:
            referrer_id = rid

    await db.upsert_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        referrer_id=referrer_id,
    )
    lang = await get_lang(message.from_user.id)

    text = welcome_text(lang)
    markup = main_menu(lang, is_admin=await is_admin(message.from_user.id))
    banner = _banner()

    # Удаляем прошлое приветствие, чтобы в чате не копились /start-экраны
    prev_id = await db.get_last_welcome_msg_id(message.from_user.id)
    if prev_id:
        try:
            await message.bot.delete_message(message.chat.id, prev_id)
        except Exception:
            pass

    if banner:
        sent = await message.answer_photo(photo=banner, caption=text, reply_markup=markup)
    else:
        sent = await message.answer(text, reply_markup=markup)

    await db.set_last_welcome_msg_id(message.from_user.id, sent.message_id)
    await _delete_user_message(message)


async def _delete_user_message(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def _send_retry(coro_factory, attempts: int = 4):
    last_exc = None
    for i in range(attempts):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            log.warning("send retry %s/%s failed: %s", i + 1, attempts, exc)
            await asyncio.sleep(1.5 * (i + 1))
    if last_exc:
        raise last_exc


async def _handle_deal_start(message: Message, code: str, lang: str) -> None:
    await db.upsert_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    deal = await db.get_deal_by_code(code)
    admin = await is_admin(message.from_user.id)
    if not deal:
        await _send_retry(
            lambda: message.answer(
                "❌ Сделка не найдена.",
                reply_markup=main_menu(lang, is_admin=admin),
            )
        )
        await _delete_user_message(message)
        return

    seller_id = int(deal["seller_id"] or 0)
    buyer_id = deal["buyer_id"]
    uid = message.from_user.id

    # Уже участник этой сделки — просто показать статус/кнопки снова
    if uid == seller_id or uid == buyer_id:
        if deal["status"] == "open":
            await _send_retry(
                lambda: message.answer(
                    "Это ваша сделка. Отправьте ссылку второй стороне.\n"
                    f"Код: <code>{code}</code>"
                )
            )
            await _delete_user_message(message)
            return
        # active+ — переотправим карточки
        deal = await db.get_deal_by_code(code)
        await _notify_deal_parties(message, deal, code, resend_only_for=uid)
        await _delete_user_message(message)
        return

    if deal["status"] != "open":
        await _send_retry(
            lambda: message.answer(
                "❌ Сделка уже занята или закрыта.",
                reply_markup=main_menu(lang, is_admin=admin),
            )
        )
        await _delete_user_message(message)
        return

    joined_as = await db.join_deal(code, uid)
    if not joined_as:
        await _send_retry(
            lambda: message.answer(
                "❌ Не удалось подключиться к сделке. Попробуйте ещё раз.",
                reply_markup=main_menu(lang, is_admin=admin),
            )
        )
        await _delete_user_message(message)
        return

    deal = await db.get_deal_by_code(code)
    await _notify_deal_parties(message, deal, code)
    await _delete_user_message(message)


async def _notify_deal_parties(
    message: Message,
    deal,
    code: str,
    resend_only_for: int | None = None,
) -> None:
    if not deal:
        return
    description = ""
    try:
        description = deal["description"] or ""
    except (KeyError, IndexError, TypeError):
        description = ""

    seller_id = int(deal["seller_id"] or 0)
    buyer_id = deal["buyer_id"]
    if not seller_id or not buyer_id:
        return

    status = deal["status"]
    amount = float(deal["amount"])
    pay_method = deal["pay_method"]

    buyer_user = await db.get_user(buyer_id)
    seller_user = await db.get_user(seller_id)
    buyer_deals = await db.count_user_deals(buyer_id)
    seller_completed = await db.count_completed_deals(seller_id)

    seller_text = seller_deal_connected_text(
        code=code,
        buyer_username=buyer_user["username"] if buyer_user else None,
        buyer_id=buyer_id,
        buyer_deals=buyer_deals,
        description=description,
        pay_method=pay_method,
        amount=amount,
    )

    if status == "active":
        buyer_text = buyer_seller_joined_text(
            code=code,
            seller_username=seller_user["username"] if seller_user else None,
            seller_id=seller_id,
            seller_completed_deals=seller_completed,
        )
        buyer_markup = buyer_pay_kb(code, amount, pay_method)
        seller_markup = None
    elif status == "paid":
        buyer_text = buyer_payment_accepted_text(
            code=code,
            seller_username=seller_user["username"] if seller_user else None,
            seller_id=seller_id,
            description=description,
            pay_method=pay_method,
            amount=amount,
        )
        buyer_markup = None
        seller_markup = seller_deal_kb(code)
    elif status == "goods_sent":
        buyer_text = buyer_goods_ready_text(code=code)
        buyer_markup = buyer_deal_kb(code)
        seller_markup = None
    elif status == "completed":
        buyer_text = deal_completed_text(code=code, role="buyer")
        buyer_markup = None
        seller_markup = None
        seller_text = deal_completed_text(code=code, role="seller")
    else:
        return

    send_seller = resend_only_for is None or resend_only_for == seller_id
    send_buyer = resend_only_for is None or resend_only_for == buyer_id

    if send_seller:
        try:
            await _send_retry(
                lambda: message.bot.send_message(
                    seller_id,
                    seller_text,
                    disable_web_page_preview=False,
                    reply_markup=seller_markup,
                )
            )
        except Exception as exc:
            log.warning("failed to notify seller: %s", exc)

    if send_buyer:
        try:
            if message.from_user.id == buyer_id:
                await _send_retry(
                    lambda: message.answer(
                        buyer_text,
                        disable_web_page_preview=False,
                        reply_markup=buyer_markup,
                    )
                )
            else:
                await _send_retry(
                    lambda: message.bot.send_message(
                        buyer_id,
                        buyer_text,
                        disable_web_page_preview=False,
                        reply_markup=buyer_markup,
                    )
                )
        except Exception as exc:
            log.error("failed to notify buyer: %s", exc)


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lang = await get_lang(callback.from_user.id)
    text = welcome_text(lang)
    markup = main_menu(lang, is_admin=await is_admin(callback.from_user.id))
    if callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=markup)
    else:
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "menu:balance")
async def menu_balance(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lang = await get_lang(callback.from_user.id)
    user = await db.get_user(callback.from_user.id)
    ton = float(user["balance_ton"] or 0) if user else 0.0
    rub = float(user["balance_rub"] or 0) if user else 0.0
    stars = int(user["balance_stars"] or 0) if user else 0
    await _edit(
        callback,
        balance_text(lang, ton=ton, rub=rub, stars=stars),
        markup=balance_menu(lang),
        lang=lang,
    )
    await callback.answer()


_WITHDRAW_CURRENCY = {
    "ton": ("ton", "TON"),
    "card": ("rub", "RUB"),
    "stars": ("stars", "STARS"),
}


@router.callback_query(F.data.startswith("withdraw:"))
async def withdraw_start(callback: CallbackQuery, state: FSMContext) -> None:
    lang = await get_lang(callback.from_user.id)
    method = callback.data.split(":")[-1]
    if method not in _WITHDRAW_CURRENCY:
        await callback.answer()
        return

    completed = await db.count_completed_deals(callback.from_user.id)
    if completed < MIN_COMPLETED_DEALS_WITHDRAW:
        await callback.answer(
            t(lang, "balance_withdraw_need_deals", count=completed),
            show_alert=True,
        )
        return

    user = await db.get_user(callback.from_user.id)
    balance_key, currency_label = _WITHDRAW_CURRENCY[method]
    if balance_key == "ton":
        available = float(user["balance_ton"] or 0) if user else 0.0
        if not user or not (user["ton_wallet"] or "").strip():
            await callback.answer(t(lang, "balance_withdraw_need_ton"), show_alert=True)
            return
    elif balance_key == "rub":
        available = float(user["balance_rub"] or 0) if user else 0.0
        if not user or not (user["card_number"] or "").strip():
            await callback.answer(t(lang, "balance_withdraw_need_card"), show_alert=True)
            return
    else:
        available = float(int(user["balance_stars"] or 0) if user else 0)
        try:
            payout_u = (user["payout_username"] or "").strip() if user else ""
        except (KeyError, IndexError, TypeError):
            payout_u = ""
        if not payout_u:
            await callback.answer(t(lang, "balance_withdraw_need_username"), show_alert=True)
            return

    if available <= 0:
        await callback.answer(t(lang, "balance_withdraw_empty"), show_alert=True)
        return

    await state.set_state(BalanceStates.waiting_withdraw_amount)
    await state.update_data(withdraw_method=method, withdraw_currency=balance_key)
    avail_text = f"{int(available)}" if balance_key == "stars" else f"{available:.2f}"
    await _edit(
        callback,
        withdraw_ask_text(lang, currency=currency_label, available=avail_text),
        markup=back_menu(lang),
        lang=lang,
    )
    await callback.answer()


@router.message(BalanceStates.waiting_withdraw_amount, F.text)
async def withdraw_amount(message: Message, state: FSMContext) -> None:
    lang = await get_lang(message.from_user.id)
    data = await state.get_data()
    method = data.get("withdraw_method")
    balance_key = data.get("withdraw_currency")
    if method not in _WITHDRAW_CURRENCY or balance_key not in {"ton", "rub", "stars"}:
        await state.clear()
        await message.answer(t(lang, "balance_withdraw_empty"))
        return

    raw = message.text.strip().replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("❌ Введите число.")
        return

    if amount <= 0:
        await message.answer("❌ Сумма должна быть больше 0.")
        return

    if balance_key == "stars" and not float(amount).is_integer():
        await message.answer("❌ Stars должны быть целым числом.")
        return

    completed = await db.count_completed_deals(message.from_user.id)
    if completed < MIN_COMPLETED_DEALS_WITHDRAW:
        await state.clear()
        await message.answer(t(lang, "balance_withdraw_need_deals", count=completed))
        return

    ok = await db.deduct_balance(message.from_user.id, balance_key, amount)
    if not ok:
        await message.answer(t(lang, "balance_withdraw_empty"))
        return

    await state.clear()
    user = await db.get_user(message.from_user.id)
    currency_label = _WITHDRAW_CURRENCY[method][1]
    amount_text = f"{int(amount)}" if balance_key == "stars" else f"{amount:g}"
    manager = DEAL_MANAGER_USERNAME.lstrip("@")

    await message.answer(
        withdraw_done_text(
            lang,
            amount=amount_text,
            currency=currency_label,
            manager=manager,
        )
    )

    dest = "—"
    if method == "ton" and user:
        dest = user["ton_wallet"] or "—"
    elif method == "card" and user:
        dest = user["card_number"] or "—"
    elif method == "stars" and user:
        try:
            uname = user["payout_username"] or message.from_user.username or "—"
        except (KeyError, IndexError, TypeError):
            uname = message.from_user.username or "—"
        dest = f"@{str(uname).lstrip('@')}"

    money = ce("deal_money", "💰")
    person = ce("deal_person", "👤")
    check = ce("deal_check", "✅")
    notify = (
        f"{money} <b>Заявка на вывод</b>\n"
        f"\n"
        f"{person} User: <code>{message.from_user.id}</code> "
        f"(@{message.from_user.username or '—'})\n"
        f"{check} Сумма: <b>{amount_text}</b> {currency_label}\n"
        f"Куда: <code>{dest}</code>"
    )
    try:
        await message.bot.send_message(f"@{manager}", notify)
    except Exception:
        pass


@router.callback_query(F.data == "menu:deals")
async def menu_deals(callback: CallbackQuery) -> None:
    lang = await get_lang(callback.from_user.id)
    deals = await db.list_user_deals(callback.from_user.id)
    type_labels = deal_type_labels(lang)
    pay_labels = deal_pay_labels(lang)
    if not deals:
        text = f"📑 <b>{t(lang, 'deals_title')}</b>\n\n{t(lang, 'deals_empty')}"
    else:
        lines = [f"📑 <b>{t(lang, 'deals_title')}</b>\n"]
        for d in deals:
            role = t(lang, "role_seller") if d["seller_id"] == callback.from_user.id else t(lang, "role_buyer")
            lines.append(
                f"• <code>{d['code']}</code> — {type_labels.get(d['deal_type'], d['deal_type'])} / "
                f"{pay_labels.get(d['pay_method'], d['pay_method'])} — "
                f"<b>{float(d['amount']):g}</b> ({d['status']}, {role})"
            )
        text = "\n".join(lines)
    await _edit(callback, text, lang=lang)
    await callback.answer()


@router.callback_query(F.data == "menu:refs")
async def menu_refs(callback: CallbackQuery) -> None:
    lang = await get_lang(callback.from_user.id)
    count = await db.referral_count(callback.from_user.id)
    bot = await callback.bot.get_me()
    link = f"https://t.me/{bot.username}?start={callback.from_user.id}"
    text = (
        f"🌐 <b>{t(lang, 'refs_title')}</b>\n\n"
        f"{t(lang, 'refs_text')}\n\n"
        f"{t(lang, 'refs_invited')}: <b>{count}</b>\n"
        f"{t(lang, 'refs_link')}:\n<code>{link}</code>"
    )
    await _edit(callback, text, lang=lang)
    await callback.answer()


@router.callback_query(F.data == "menu:lang")
async def menu_lang(callback: CallbackQuery) -> None:
    lang = await get_lang(callback.from_user.id)
    await _edit(callback, lang_text(lang), markup=language_menu(lang), lang=lang)
    await callback.answer()


@router.callback_query(F.data.in_({"lang:ru", "lang:en"}))
async def set_language(callback: CallbackQuery) -> None:
    new_lang = "en" if callback.data.endswith("en") else "ru"
    await db.upsert_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.full_name,
    )
    await db.set_language(callback.from_user.id, new_lang)
    await callback.answer()
    text = welcome_text(new_lang)
    markup = main_menu(new_lang, is_admin=await is_admin(callback.from_user.id))
    if callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=markup)
    else:
        await callback.message.edit_text(text, reply_markup=markup)


async def _edit(callback: CallbackQuery, text: str, markup=None, lang: str | None = "ru") -> None:
    markup = markup or back_menu(lang)
    if callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=markup)
    else:
        await callback.message.edit_text(text, reply_markup=markup)
