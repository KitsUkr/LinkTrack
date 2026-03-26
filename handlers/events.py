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
    """Перевіряє, що користувач став учасником чату."""
    old = update.old_chat_member.status if update.old_chat_member else "left"
    new = update.new_chat_member.status

    return old in ("left", "kicked", "restricted") and new in (
        "member",
        "administrator",
        "creator",
    )


def _is_left_member(update: ChatMemberUpdated) -> bool:
    """Перевіряє, що користувач покинув чат."""
    old = update.old_chat_member.status if update.old_chat_member else "left"
    new = update.new_chat_member.status

    return old in ("member", "administrator", "creator") and new in (
        "left",
        "kicked",
    )


def _get_user_info(update: ChatMemberUpdated) -> tuple[int, str, str]:
    """Витягує дані користувача з оновлення."""
    user = update.new_chat_member.user
    user_id = user.id
    username = user.username or ""
    full_name = user.full_name or ""
    return user_id, username, full_name


# ─── Користувач приєднався ─────────────────────────────────
@router.chat_member()
async def on_chat_member_updated(update: ChatMemberUpdated):
    """Обробляє всі оновлення статусу учасника чату."""

    if _is_new_member(update):
        await _handle_join(update)
    elif _is_left_member(update):
        await _handle_leave(update)


async def _handle_join(update: ChatMemberUpdated):
    """Обробка приєднання користувача."""
    # Перевіряємо, чи є invite_link в оновленні
    if not update.invite_link:
        logger.debug(
            "Користувач %d приєднався без invite-посилання (chat %d)",
            update.new_chat_member.user.id,
            update.chat.id,
        )
        return

    invite_url = update.invite_link.invite_link
    link_data = await get_link_by_url(invite_url)

    if not link_data:
        logger.debug(
            "Invite-посилання %s не знайдено в БД (не відстежується)", invite_url
        )
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
    """Обробка виходу користувача."""
    user_id, username, full_name = _get_user_info(update)
    chat_id = update.chat.id

    # Шукаємо, за яким посиланням він приєднувався
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


# ═══════════════════════════════════════════════════════════
#   Автододавання каналу (my_chat_member)
# ═══════════════════════════════════════════════════════════

@router.my_chat_member()
async def on_my_chat_member_updated(update: ChatMemberUpdated):
    """Бот доданий/видалений з каналу або групи."""
    old_status = update.old_chat_member.status if update.old_chat_member else "left"
    new_status = update.new_chat_member.status
    chat = update.chat

    # Бот став адміністратором
    if new_status == "administrator" and old_status in ("left", "kicked", "member"):
        chat_title = chat.title or f"Chat {chat.id}"
        existing = await get_channel_by_chat_id(chat.id)

        if not existing:
            try:
                channel_id = await add_channel(chat.id, chat_title)
                logger.info(
                    "📡 Канал додано автоматично: %s (%d), id: %d",
                    chat_title, chat.id, channel_id,
                )
                # Повідомити адмінів
                for admin_id in ADMIN_IDS:
                    try:
                        await update.bot.send_message(
                            admin_id,
                            f"📡 <b>Канал підключено!</b>\n\n"
                            f"Бот доданий як адміністратор до:\n"
                            f"<b>{chat_title}</b>\n\n"
                            f"Канал доступний для створення посилань.",
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.exception("Помилка при автододаванні каналу %d: %s", chat.id, e)
        else:
            logger.debug("Канал %d вже в базі, пропуск", chat.id)

    # Бот видалений або понижений
    elif new_status in ("left", "kicked", "member") and old_status == "administrator":
        chat_title = chat.title or f"Chat {chat.id}"
        deleted = await delete_channel_by_chat_id(chat.id)

        if deleted:
            logger.info(
                "📡 Канал видалено автоматично: %s (%d)",
                chat_title, chat.id,
            )
            for admin_id in ADMIN_IDS:
                try:
                    await update.bot.send_message(
                        admin_id,
                        f"📡 <b>Канал відключено</b>\n\n"
                        f"Бот більше не адміністратор у:\n"
                        f"<b>{chat_title}</b>\n\n"
                        f"Канал видалено зі списку.",
                    )
                except Exception:
                    pass
