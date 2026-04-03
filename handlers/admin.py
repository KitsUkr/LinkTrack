import logging
import math

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from database import (
    get_all_channels,
    get_channel,
    delete_channel,
    save_invite_link,
    get_links_by_channel,
    get_link_stats,
    delete_link_by_id,
    update_link_post_data,
)

logger = logging.getLogger(__name__)

router = Router()

PAGE_SIZE = 5

# ─── Утиліти ──────────────────────────────────────────────
def parse_callback_ids(data: str, count: int = 1) -> tuple[int, ...]:
    """Парсить ID з callback_data формату 'prefix:id1:id2:...'

    Повертає кортеж із count цілих чисел (або None для відсутніх).
    """
    parts = data.split(":")
    result = []
    for i in range(1, count + 1):
        if i < len(parts) and parts[i] != "None":
            result.append(int(parts[i]))
        else:
            result.append(None)
    return tuple(result)


def _pagination_row(
    current_page: int,
    total_items: int,
    cb_prefix: str,
) -> list[InlineKeyboardButton]:
    total_pages = math.ceil(total_items / PAGE_SIZE)
    if total_pages <= 1:
        return []

    row: list[InlineKeyboardButton] = []

    if current_page > 0:
        row.append(InlineKeyboardButton(
            text="«",
            callback_data=f"{cb_prefix}:{current_page - 1}",
        ))

    row.append(InlineKeyboardButton(
        text=f"{current_page + 1}/{total_pages}",
        callback_data="noop",
    ))

    if current_page < total_pages - 1:
        row.append(InlineKeyboardButton(
            text="»",
            callback_data=f"{cb_prefix}:{current_page + 1}",
        ))

    return row  # ← один список = один рядок в клавіатурі


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📡 Канали", callback_data="channels")],
            [InlineKeyboardButton(text="✏️ Створити посилання", callback_data="quick_create_link")],
        ]
    )


def back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« В меню", callback_data="main_menu")],
        ]
    )


def back_to_stats_kb(link_id: int, channel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="« Назад",
            callback_data=f"stats:{link_id}:{channel_id}",
        )],
    ])


# ─── FSM ──────────────────────────────────────────────────
class CreateLinkFSM(StatesGroup):
    waiting_campaign_name = State()


class PostLinkFSM(StatesGroup):
    waiting_ad_price = State()


# ══════════════════════════════════════════════════════════
#   Головне меню + навігація
# ══════════════════════════════════════════════════════════

MAIN_MENU_TEXT = (
    "👋 <b>Link Tracking Bot</b>\n\n"
    "Бот для відстеження підписників за invite-посиланнями.\n"
    "Оберіть дію:"
)


async def show_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(MAIN_MENU_TEXT, reply_markup=main_menu_kb())
    await callback.answer()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(MAIN_MENU_TEXT, reply_markup=main_menu_kb())


@router.callback_query(F.data.in_({"main_menu", "cancel"}))
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    await show_main_menu(callback, state)


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


# ══════════════════════════════════════════════════════════
#   Швидке створення посилання — вибір каналу з меню
# ══════════════════════════════════════════════════════════

@router.callback_query(F.data == "quick_create_link")
async def cb_quick_create_link(callback: CallbackQuery):
    owner_id = callback.from_user.id
    channels = await get_all_channels(owner_id)

    if not channels:
        await callback.message.edit_text(
            "📭 <b>Немає підключених каналів</b>\n\n"
            "Спочатку додайте бота адміністратором у канал — "
            "він з'явиться тут автоматично.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📡 Перейти до каналів", callback_data="channels")],
                    [InlineKeyboardButton(text="« В меню", callback_data="main_menu")],
                ]
            ),
        )
        await callback.answer()
        return

    buttons = []
    for ch in channels:
        buttons.append([
            InlineKeyboardButton(
                text=f"📡 {ch['chat_title']}",
                callback_data=f"create_link:{ch['id']}:from_menu",
            )
        ])
    buttons.append([InlineKeyboardButton(text="« В меню", callback_data="main_menu")])

    await callback.message.edit_text(
        "✏️ <b>Створити посилання</b>\n\n"
        "Оберіть канал для якого створити посилання:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════
#   Канали — список, деталі, видалення
# ══════════════════════════════════════════════════════════

@router.callback_query(F.data == "add_channel")
async def cb_add_channel(callback: CallbackQuery):
    me = await callback.bot.get_me()
    bot_username = me.username

    text = (
        "➕ <b>Додавання каналу</b>\n\n"
        "Додайте бота в адміністратори каналу, надавши наступні дозволи:\n"
        "• <b>Запрошувальні посилання</b> (щоб бот міг керувати посиланнями)\n"
        "Натисніть <b>«Підключити канал»</b> нижче: вас перенаправить до списку ваших каналів. "
        "Оберіть потрібний канал і підтвердіть надання прав."
    )
    url = f"https://t.me/{bot_username}?startchannel&admin=invite_users"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Підключити канал", url=url)],
            [InlineKeyboardButton(text="« Назад", callback_data="channels")],
        ]
    )
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


def _build_channels_list(
    channels: list[dict],
    page: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    total = len(channels)
    start = page * PAGE_SIZE
    page_channels = channels[start : start + PAGE_SIZE]

    text = "📡 <b>Канали</b>\n\n"
    buttons: list[list[InlineKeyboardButton]] = []

    for ch in page_channels:
        text += f"• <b>{ch['chat_title']}</b>\n"
        buttons.append([
            InlineKeyboardButton(
                text=f"📡 {ch['chat_title']}",
                callback_data=f"ch_detail:{ch['id']}",
            )
        ])

    text += "\n💡 Щоб додати ще — призначте бота адміністратором каналу."

    # ── Пагінація ──
    nav = _pagination_row(page, total, "channels_p")
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="➕ Додати канал", callback_data="add_channel")])
    buttons.append([InlineKeyboardButton(text="« В меню", callback_data="main_menu")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _render_channels_page(callback: CallbackQuery, page: int = 0):
    owner_id = callback.from_user.id
    channels = await get_all_channels(owner_id)

    if not channels:
        text = (
            "📡 <b>Канали</b>\n\n"
            "📭 Немає підключених каналів.\n\n"
            "💡 Додайте бота <b>адміністратором</b> у канал або групу — "
            "він з'явиться тут автоматично."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Додати канал", callback_data="add_channel")],
                [InlineKeyboardButton(text="« В меню", callback_data="main_menu")],
            ]
        )
    else:
        # Clamp page to valid range
        max_page = max(0, math.ceil(len(channels) / PAGE_SIZE) - 1)
        page = min(page, max_page)
        text, kb = _build_channels_list(channels, page)

    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data == "channels")
async def cb_channels(callback: CallbackQuery):
    await _render_channels_page(callback, page=0)
    await callback.answer()


@router.callback_query(F.data.startswith("channels_p:"))
async def cb_channels_page(callback: CallbackQuery):
    """Навігація між сторінками списку каналів."""
    try:
        page = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Некоректна сторінка", show_alert=True)
        return

    await _render_channels_page(callback, page=page)
    await callback.answer()


# ─── Деталі каналу ────────────────────────────────────────

@router.callback_query(F.data.startswith("ch_detail:"))
async def cb_channel_detail(callback: CallbackQuery):
    owner_id = callback.from_user.id
    try:
        (channel_id,) = parse_callback_ids(callback.data, 1)
    except (ValueError, IndexError):
        await callback.answer("❌ Некоректний ID", show_alert=True)
        return

    ch = await get_channel(channel_id, owner_id)
    if not ch:
        await callback.answer("❌ Канал не знайдено.", show_alert=True)
        return

    await callback.message.edit_text(
        f"📡 <b>Канал</b>\n\n"
        f"📌 Назва: <b>{ch['chat_title']}</b>\n"
        f"📅 Додано: {ch['added_at']}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="📝 Створити посилання",
                    callback_data=f"create_link:{channel_id}",
                )],
                [InlineKeyboardButton(
                    text="📊 Посилання каналу",
                    callback_data=f"show_links:{channel_id}",
                )],
                [InlineKeyboardButton(
                    text="🗑 Видалити канал",
                    callback_data=f"ch_confirm_del:{channel_id}",
                )],
                [InlineKeyboardButton(text="« Назад", callback_data="channels")],
            ]
        ),
    )
    await callback.answer()


# ─── Підтвердження видалення каналу ───────────────────────

@router.callback_query(F.data.startswith("ch_confirm_del:"))
async def cb_confirm_delete_channel(callback: CallbackQuery):
    owner_id = callback.from_user.id
    try:
        (channel_id,) = parse_callback_ids(callback.data, 1)
    except (ValueError, IndexError):
        await callback.answer("❌ Некоректний ID", show_alert=True)
        return

    ch = await get_channel(channel_id, owner_id)
    if not ch:
        await callback.answer("❌ Канал не знайдено.", show_alert=True)
        return

    await callback.message.edit_text(
        f"⚠️ <b>Підтвердження видалення</b>\n\n"
        f"Ви впевнені, що хочете видалити канал <b>{ch['chat_title']}</b>?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="✅ Так, видалити",
                    callback_data=f"ch_delete:{channel_id}",
                )],
                [InlineKeyboardButton(
                    text="❌ Скасувати",
                    callback_data=f"ch_detail:{channel_id}",
                )],
            ]
        ),
    )
    await callback.answer()


# ─── Видалення каналу ─────────────────────────────────────

@router.callback_query(F.data.startswith("ch_delete:"))
async def cb_delete_channel(callback: CallbackQuery):
    owner_id = callback.from_user.id
    try:
        (channel_id,) = parse_callback_ids(callback.data, 1)
    except (ValueError, IndexError):
        await callback.answer("❌ Некоректний ID", show_alert=True)
        return

    deleted = await delete_channel(channel_id, owner_id)
    if deleted:
        await callback.answer("✅ Канал видалено", show_alert=True)
    else:
        await callback.answer("❌ Канал не знайдено", show_alert=True)

    await _render_channels_page(callback, page=0)


# ══════════════════════════════════════════════════════════
#   Створення посилання — ввід кампанії
# ══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("create_link:"))
async def cb_create_link(callback: CallbackQuery, state: FSMContext):
    owner_id = callback.from_user.id
    parts = callback.data.split(":")
    try:
        channel_id = int(parts[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Некоректний ID", show_alert=True)
        return

    from_menu = len(parts) > 2 and parts[2] == "from_menu"
    cancel_cb = "quick_create_link" if from_menu else f"ch_detail:{channel_id}"

    ch = await get_channel(channel_id, owner_id)
    if not ch:
        await callback.answer("❌ Канал не знайдено.", show_alert=True)
        return

    await state.update_data(
        chat_id=ch["chat_id"],
        chat_title=ch["chat_title"],
        channel_id=channel_id,
        from_menu=from_menu,
        cancel_cb=cancel_cb,
        bot_msg_id=callback.message.message_id,
        owner_id=owner_id,
    )
    await state.set_state(CreateLinkFSM.waiting_campaign_name)

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Скасувати", callback_data=cancel_cb)],
    ])
    await callback.message.edit_text(
        f"📝 <b>Створити посилання</b>\n\n"
        f"📡 Канал: <b>{ch['chat_title']}</b>\n\n"
        f"Введіть <b>назву для посилання</b>:",
        reply_markup=cancel_kb,
    )
    await callback.answer()


@router.message(CreateLinkFSM.waiting_campaign_name)
async def fsm_campaign_name(message: Message, state: FSMContext):
    data = await state.get_data()
    bot_msg_id = data.get("bot_msg_id")
    channel_id = data.get("channel_id")
    owner_id = data.get("owner_id", message.from_user.id)
    cancel_cb = data.get("cancel_cb", f"ch_detail:{channel_id}" if channel_id else "main_menu")
    chat = message.chat.id

    try:
        await message.delete()
    except Exception:
        pass

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Скасувати", callback_data=cancel_cb)],
    ])

    campaign_name = message.text
    if not campaign_name:
        await message.bot.edit_message_text(
            "⚠️ Надішліть текстове повідомлення.",
            chat_id=chat, message_id=bot_msg_id,
            reply_markup=cancel_kb,
        )
        return

    campaign_name = campaign_name.strip()
    if not campaign_name:
        await message.bot.edit_message_text(
            "⚠️ Назва не може бути порожньою. Введіть назву для посилання:",
            chat_id=chat, message_id=bot_msg_id,
            reply_markup=cancel_kb,
        )
        return

    chat_id = data["chat_id"]
    chat_title = data["chat_title"]
    from_menu = data.get("from_menu", False)
    await state.clear()

    if from_menu:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« В меню", callback_data="main_menu")],
        ])
    elif channel_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data=f"ch_detail:{channel_id}")],
        ])
    else:
        kb = back_menu_kb()

    try:
        invite = await message.bot.create_chat_invite_link(
            chat_id=chat_id,
            name=campaign_name,
            creates_join_request=False,
        )

        link_id = await save_invite_link(
            chat_id=chat_id,
            chat_title=chat_title,
            invite_link=invite.invite_link,
            campaign_name=campaign_name,
            owner_id=owner_id,
        )

        await message.bot.edit_message_text(
            f"✅ <b>Посилання створено!</b>\n\n"
            f"📌 Назва: <b>{campaign_name}</b>\n"
            f"💬 Канал: <b>{chat_title}</b>\n\n"
            f"🔗 Посилання:\n<code>{invite.invite_link}</code>",
            chat_id=chat, message_id=bot_msg_id,
            reply_markup=kb,
        )
        logger.info(
            "Створено посилання #%d для каналу %s (%d), кампанія: %s, власник: %d",
            link_id, chat_title, chat_id, campaign_name, owner_id,
        )

    except Exception as e:
        logger.exception("Помилка при створенні посилання")
        await message.bot.edit_message_text(
            f"❌ <b>Помилка при створенні посилання:</b>\n"
            f"<code>{e}</code>\n\n"
            f"Переконайтесь, що бот є адміністратором каналу.",
            chat_id=chat, message_id=bot_msg_id,
            reply_markup=kb,
        )


# ══════════════════════════════════════════════════════════
#   Список посилань (по каналу) з пагінацією
# ══════════════════════════════════════════════════════════

async def _render_links_page(callback: CallbackQuery, channel_id: int, page: int = 0):
    owner_id = callback.from_user.id

    ch = await get_channel(channel_id, owner_id)
    if not ch:
        await callback.message.edit_text(
            "❌ Канал не знайдено.",
            reply_markup=back_menu_kb(),
        )
        return

    links = await get_links_by_channel(channel_id, owner_id)
    if not links:
        await callback.message.edit_text(
            f"📭 В каналі <b>{ch['chat_title']}</b> поки немає посилань.\n",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="📝 Створити посилання",
                        callback_data=f"create_link:{channel_id}",
                    )],
                    [InlineKeyboardButton(
                        text="« Назад",
                        callback_data=f"ch_detail:{channel_id}",
                    )],
                ]
            ),
        )
        return

    # Clamp page to valid range
    total = len(links)
    max_page = max(0, math.ceil(total / PAGE_SIZE) - 1)
    page = min(page, max_page)

    start = page * PAGE_SIZE
    page_links = links[start : start + PAGE_SIZE]

    text_lines = [f"📊 <b>Посилання каналу «{ch['chat_title']}»:</b>\n"]
    buttons: list[list[InlineKeyboardButton]] = []

    for link in page_links:
        joined = link["joined_count"]
        left = link["left_count"]
        text_lines.append(
            f"{'─' * 27}\n📌 <b>{link['campaign_name']}</b>\n👥 +{joined} / -{left}"
        )
        buttons.append([
            InlineKeyboardButton(
                text=f"📊 {link['campaign_name']}",
                callback_data=f"stats:{link['id']}:{channel_id}",
            )
        ])

    # ── Пагінація ──
    nav = _pagination_row(page, total, f"show_links_p:{channel_id}")
    if nav:
        buttons.append(nav)

    buttons.append([
        InlineKeyboardButton(text="« Назад", callback_data=f"ch_detail:{channel_id}")
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    text = "\n".join(text_lines)
    # Safety trim (should rarely trigger with pagination, but kept as last resort)
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>…список скорочено</i>"

    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("show_links:"))
async def cb_show_links(callback: CallbackQuery):
    try:
        (channel_id,) = parse_callback_ids(callback.data, 1)
    except (ValueError, IndexError):
        await callback.answer("❌ Некоректний ID", show_alert=True)
        return

    await _render_links_page(callback, channel_id, page=0)
    await callback.answer()


@router.callback_query(F.data.startswith("show_links_p:"))
async def cb_show_links_page(callback: CallbackQuery):
    """Навігація між сторінками списку посилань: show_links_p:{channel_id}:{page}"""
    parts = callback.data.split(":")
    try:
        channel_id = int(parts[1])
        page = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Некоректні параметри", show_alert=True)
        return

    await _render_links_page(callback, channel_id, page=page)
    await callback.answer()


# ══════════════════════════════════════════════════════════
#   Детальна статистика по посиланню
# ══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("stats:"))
async def cb_stats(callback: CallbackQuery, state: FSMContext):
    owner_id = callback.from_user.id
    await state.clear()
    try:
        link_id, channel_id = parse_callback_ids(callback.data, 2)
    except (ValueError, IndexError):
        await callback.answer("❌ Некоректний ID", show_alert=True)
        return

    stats = await get_link_stats(link_id, owner_id)
    if not stats:
        await callback.answer("❌ Посилання не знайдено.", show_alert=True)
        return

    joined = stats["joined_count"]
    left = stats["left_count"]
    current = joined - left

    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"📌 Назва посилання: <b>{stats['campaign_name']}</b>\n"
        f"💬 Канал: <b>{stats['chat_title']}</b>\n"
        f"📅 Створено: {stats['created_at']}\n"
        f"🔗 <code>{stats['invite_link']}</code>\n\n"
        f"{'═' * 27}\n"
        f"✅ Підписались: <b>{joined}</b>\n"
        f"❌ Відписались: <b>{left}</b>\n"
        f"👥 Загальна кількість: <b>{current}</b>\n"
    )

    ad_price = stats.get("ad_price")
    if ad_price and joined > 0:
        cpm = ad_price / joined
        text += f"💰 Ціна за підписника: <b>{cpm:.2f}₴</b>\n"

    text += f"{'═' * 27}\n"

    back_btn_cb = f"show_links:{channel_id}" if channel_id else "channels"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="💰 Вартість реклами",
                callback_data=f"post_link:{link_id}:{channel_id}",
            )],
            [InlineKeyboardButton(
                text="🗑 Деактивувати посилання",
                callback_data=f"del_link:{link_id}:{channel_id}",
            )],
            [InlineKeyboardButton(text="« Назад", callback_data=back_btn_cb)],
        ]
    )

    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ══════════════════════════════════════════════════════════
#   Видалення посилання
# ══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("del_link:"))
async def cb_delete_link(callback: CallbackQuery):
    owner_id = callback.from_user.id
    try:
        link_id, channel_id = parse_callback_ids(callback.data, 2)
    except (ValueError, IndexError):
        await callback.answer("❌ Некоректний ID", show_alert=True)
        return

    stats = await get_link_stats(link_id, owner_id)
    if not stats:
        await callback.answer("❌ Посилання вже деактивовано або не знайдено.", show_alert=True)
        return

    chat_id = stats["chat_id"]
    invite_link = stats["invite_link"]

    try:
        await callback.bot.revoke_chat_invite_link(
            chat_id=chat_id, invite_link=invite_link
        )
    except Exception as e:
        logger.warning(
            "Не вдалося деактивувати посилання %s у чаті %d: %s",
            invite_link, chat_id, e,
        )

    deleted = await delete_link_by_id(link_id, owner_id)

    if deleted:
        await callback.answer("✅ Посилання деактивовано.", show_alert=True)
    else:
        await callback.answer("❌ Помилка при деактивації посилання.", show_alert=True)

    if channel_id:
        await _render_links_page(callback, channel_id, page=0)
    else:
        await _render_channels_page(callback, page=0)


# ══════════════════════════════════════════════════════════
#   Вартість реклами (CPM)
# ══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("post_link:"))
async def cb_post_link(callback: CallbackQuery, state: FSMContext):
    owner_id = callback.from_user.id
    try:
        link_id, channel_id = parse_callback_ids(callback.data, 2)
    except (ValueError, IndexError):
        await callback.answer("❌ Некоректний ID", show_alert=True)
        return

    await state.update_data(
        link_id=link_id,
        channel_id=channel_id,
        bot_msg_id=callback.message.message_id,
        owner_id=owner_id,
    )

    stats = await get_link_stats(link_id, owner_id)
    if not stats:
        await callback.answer("❌ Посилання не знайдено.", show_alert=True)
        return

    existing_price = stats.get("ad_price")

    if existing_price is not None:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✏️ Змінити ціну",
                callback_data=f"change_price:{link_id}:{channel_id}",
            )],
            [InlineKeyboardButton(
                text="« Назад",
                callback_data=f"stats:{link_id}:{channel_id}",
            )],
        ])
        await callback.message.edit_text(
            f"💰 <b>Вартість реклами</b>\n\n"
            f"Поточна сума: <b>{existing_price:.2f}₴</b>\n\n"
            f"Бажаєте змінити?",
            reply_markup=kb,
        )
    else:
        await state.set_state(PostLinkFSM.waiting_ad_price)
        await callback.message.edit_text(
            "💰 <b>Вартість реклами</b>\n\n"
            "Введіть <b>суму</b>, яку ви заплатили за рекламу.\n\n"
            "Наприклад: <code>500</code> або <code>1250.50</code>",
            reply_markup=back_to_stats_kb(link_id, channel_id),
        )

    await callback.answer()


@router.message(PostLinkFSM.waiting_ad_price)
async def fsm_ad_price(message: Message, state: FSMContext):
    data = await state.get_data()
    bot_msg_id = data.get("bot_msg_id")
    link_id = data["link_id"]
    channel_id = data.get("channel_id")
    owner_id = data.get("owner_id", message.from_user.id)
    chat = message.chat.id

    try:
        await message.delete()
    except Exception:
        pass

    if not message.text:
        await message.bot.edit_message_text(
            "⚠️ Надішліть текстове повідомлення.",
            chat_id=chat, message_id=bot_msg_id,
            reply_markup=back_to_stats_kb(link_id, channel_id),
        )
        return

    try:
        ad_price = float(message.text.strip().replace(",", "."))
        if ad_price <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.bot.edit_message_text(
            "⚠️ Введіть коректну суму (додатне число).\n"
            "Наприклад: <code>500</code> або <code>1250.50</code>",
            chat_id=chat, message_id=bot_msg_id,
            reply_markup=back_to_stats_kb(link_id, channel_id),
        )
        return

    await state.clear()
    await update_link_post_data(link_id, None, ad_price, None, owner_id)

    stats = await get_link_stats(link_id, owner_id)
    joined = stats["joined_count"] if stats else 0
    _ = ad_price / joined if joined > 0 else 0  # pre-calculated for future use

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📊 До статистики",
            callback_data=f"stats:{link_id}:{channel_id}",
        )],
    ])
    await message.bot.edit_message_text(
        "✅ <b>Дані збережено!</b>\n\n"
        "Інформація про вартість підписника з'явиться у вашій статистиці, "
        "щойно хтось приєднається за вашим посиланням.\n\n",
        chat_id=chat, message_id=bot_msg_id,
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("change_price:"))
async def cb_change_price(callback: CallbackQuery, state: FSMContext):
    owner_id = callback.from_user.id
    try:
        link_id, channel_id = parse_callback_ids(callback.data, 2)
    except (ValueError, IndexError):
        await callback.answer("❌ Некоректний ID", show_alert=True)
        return

    # Ownership check before entering FSM
    stats = await get_link_stats(link_id, owner_id)
    if not stats:
        await callback.answer("❌ Посилання не знайдено.", show_alert=True)
        return

    await state.update_data(
        link_id=link_id,
        channel_id=channel_id,
        bot_msg_id=callback.message.message_id,
        owner_id=owner_id,
    )
    await state.set_state(PostLinkFSM.waiting_ad_price)

    await callback.message.edit_text(
        "💰 <b>Нова вартість реклами</b>\n\n"
        "Введіть нову <b>суму</b>:\n\n"
        "Наприклад: <code>500</code> або <code>1250.50</code>",
        reply_markup=back_to_stats_kb(link_id, channel_id),
    )
    await callback.answer()