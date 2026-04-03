import logging

from aiogram import Router
from aiogram.types import ChatMemberUpdated

from config import ADMIN_IDS
from database import (
    get_link_by_url,
    save_event,
    find_user_join_link,
    add_channel,
    get_channel_by_chat_id,
    delete_channel_by_chat_id,
)

logger = logging.getLogger(__name__)

router = Router()


def _is_new_member(update: ChatMemberUpdated) -> bool:
    old = update.old_chat_member.status if update.old_chat_member else "left"
    new = update.new_chat_member.status
    return old in ("left", "kicked", "restricted") and new in (
        "member", "administrator", "creator",
    )


def _is_left_member(update: ChatMemberUpdated) -> bool:
    old = update.old_chat_member.status if update.old_chat_member else "left"
    new = update.new_chat_member.status
    return old in ("member", "administrator", "creator") and new in ("left", "kicked")


def _get_user_info(update: ChatMemberUpdated) -> tuple[int, str, str]:
    user = update.new_chat_member.user
    return user.id, user.username or "", user.full_name or ""


# ─── Користувач приєднався / покинув чат ──────────────────

@router.chat_member()
async def on_chat_member_updated(update: ChatMemberUpdated):
    if _is_new_member(update):
        await _handle_join(update)
    elif _is_left_member(update):
        await _handle_leave(update)


async def _handle_join(update: ChatMemberUpdated):
    if not update.invite_link:
        logger.debug(
            "Користувач %d приєднався без invite-посилання (chat %d)",
            update.new_chat_member.user.id, update.chat.id,
        )
        return

    invite_url = update.invite_link.invite_link
    link_data = await get_link_by_url(invite_url)

    if not link_data:
        logger.debug("Invite-посилання %s не знайдено в БД (не відстежується)", invite_url)
        return

    user_id, username, full_name = _get_user_info(update)
    await save_event(
        link_id=link_data["id"],
        user_id=user_id,
        username=username,
        full_name=full_name,
        event_type="joined",
    )
    logger.info(
        "✅ JOIN: %s (@%s) → кампанія '%s' (chat %d)",
        full_name, username, link_data["campaign_name"], update.chat.id,
    )


async def _handle_leave(update: ChatMemberUpdated):
    user_id, username, full_name = _get_user_info(update)
    chat_id = update.chat.id

    join_info = await find_user_join_link(chat_id, user_id)
    if not join_info:
        logger.debug(
            "Користувач %d покинув чат %d, але не пов'язаний з відстежуваним посиланням",
            user_id, chat_id,
        )
        return

    await save_event(
        link_id=join_info["link_id"],
        user_id=user_id,
        username=username,
        full_name=full_name,
        event_type="left",
    )
    logger.info(
        "❌ LEFT: %s (@%s) → кампанія '%s' (chat %d)",
        full_name, username, join_info["campaign_name"], chat_id,
    )


# ══════════════════════════════════════════════════════════
#   Автододавання / видалення каналу (my_chat_member)
# ══════════════════════════════════════════════════════════

@router.my_chat_member()
async def on_my_chat_member_updated(update: ChatMemberUpdated):
    old_status = update.old_chat_member.status if update.old_chat_member else "left"
    new_status = update.new_chat_member.status
    chat = update.chat

    # Бот став адміністратором ─────────────────────────────
    if new_status == "administrator" and old_status in ("left", "kicked", "member"):
        chat_title = chat.title or f"Chat {chat.id}"
        if update.from_user:
            owner_id = update.from_user.id
        else:
            owner_id = 0
            logger.warning("my_chat_member: from_user is None for chat %d", chat.id)

        existing = await get_channel_by_chat_id(chat.id)

        if not existing:
            try:
                channel_id = await add_channel(chat.id, chat_title, owner_id)
                logger.info(
                    "📡 Канал додано автоматично: %s (%d), id: %d, власник: %d",
                    chat_title, chat.id, channel_id, owner_id,
                )
                await _notify_channel_added(update, chat_title)
            except Exception as e:
                logger.exception("Помилка при автододаванні каналу %d: %s", chat.id, e)
        else:
            logger.debug("Канал %d вже в базі, пропуск", chat.id)

    # Бот видалений або понижений ──────────────────────────
    elif new_status in ("left", "kicked", "member") and old_status == "administrator":
        chat_title = chat.title or f"Chat {chat.id}"
        deleted = await delete_channel_by_chat_id(chat.id)

        if deleted:
            logger.info("📡 Канал видалено автоматично: %s (%d)", chat_title, chat.id)
            await _notify_channel_removed(update, chat_title)


async def _notify_channel_added(update: ChatMemberUpdated, chat_title: str) -> None:
    text = (
        f"📡 <b>Канал підключено!</b>\n\n"
        f"Бот доданий як адміністратор до:\n"
        f"<b>{chat_title}</b>\n\n"
        f"Канал доступний для створення посилань."
    )
    for admin_id in ADMIN_IDS:
        try:
            await update.bot.send_message(admin_id, text)
        except Exception:
            pass

    if update.from_user and update.from_user.id not in ADMIN_IDS:
        try:
            await update.bot.send_message(update.from_user.id, text)
        except Exception:
            pass


async def _notify_channel_removed(update: ChatMemberUpdated, chat_title: str) -> None:
    text = (
        f"📡 <b>Канал відключено</b>\n\n"
        f"Бот більше не адміністратор у:\n"
        f"<b>{chat_title}</b>\n\n"
        f"Канал видалено зі списку."
    )
    for admin_id in ADMIN_IDS:
        try:
            await update.bot.send_message(admin_id, text)
        except Exception:
            pass

    if update.from_user and update.from_user.id not in ADMIN_IDS:
        try:
            await update.bot.send_message(update.from_user.id, text)
        except Exception:
            pass