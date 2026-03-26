import logging
import os
from contextlib import asynccontextmanager

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(
    os.getenv("DB_DIR", os.path.dirname(os.path.abspath(__file__))),
    "bot.db",
)

_db: aiosqlite.Connection | None = None


@asynccontextmanager
async def get_db():
    """Yield the shared database connection."""
    if _db is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")
    yield _db


async def _cleanup_legacy_soft_delete_column(db: aiosqlite.Connection) -> None:
    """Remove the legacy soft-delete column from old databases."""
    cursor = await db.execute("PRAGMA table_info(invite_links)")
    existing_cols = {row[1] for row in await cursor.fetchall()}

    if "deleted_at" not in existing_cols:
        return

    cursor = await db.execute(
        "DELETE FROM invite_links WHERE deleted_at IS NOT NULL"
    )
    if cursor.rowcount > 0:
        logger.info("Removed %d legacy soft-deleted invite links", cursor.rowcount)

    try:
        await db.execute("ALTER TABLE invite_links DROP COLUMN deleted_at")
        logger.info("Dropped legacy deleted_at column from invite_links")
    except Exception as exc:
        logger.warning("Could not drop deleted_at column: %s", exc)


async def init_db() -> None:
    """Open the shared database connection and create required tables."""
    global _db
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA foreign_keys = ON")

    await _db.executescript(
        """
        CREATE TABLE IF NOT EXISTS channels (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL UNIQUE,
            chat_title TEXT    NOT NULL DEFAULT '',
            added_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS invite_links (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id       INTEGER NOT NULL,
            chat_title    TEXT    NOT NULL DEFAULT '',
            invite_link   TEXT    NOT NULL UNIQUE,
            campaign_name TEXT    NOT NULL,
            created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS link_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id    INTEGER NOT NULL REFERENCES invite_links(id) ON DELETE CASCADE,
            user_id    INTEGER NOT NULL,
            username   TEXT    DEFAULT '',
            full_name  TEXT    DEFAULT '',
            event_type TEXT    NOT NULL CHECK(event_type IN ('joined', 'left')),
            event_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_link_events_link_id ON link_events(link_id);
        CREATE INDEX IF NOT EXISTS idx_link_events_user_id ON link_events(user_id);
        """
    )

    cursor = await _db.execute("PRAGMA table_info(invite_links)")
    existing_cols = {row[1] for row in await cursor.fetchall()}

    migrations = [
        ("post_url", "TEXT DEFAULT NULL"),
        ("ad_price", "REAL DEFAULT NULL"),
        ("post_views", "INTEGER DEFAULT NULL"),
    ]
    for col_name, col_def in migrations:
        if col_name not in existing_cols:
            await _db.execute(
                f"ALTER TABLE invite_links ADD COLUMN {col_name} {col_def}"
            )
            logger.info("Added column %s to invite_links", col_name)

    await _cleanup_legacy_soft_delete_column(_db)
    await _db.commit()


async def close_db() -> None:
    """Close the shared database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def add_channel(chat_id: int, chat_title: str) -> int:
    """Add a channel and return its row id."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO channels (chat_id, chat_title)
            VALUES (?, ?)
            """,
            (chat_id, chat_title),
        )
        await db.commit()
        return cursor.lastrowid


async def get_all_channels() -> list[dict]:
    """Return all connected channels."""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM channels ORDER BY added_at DESC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_channel(channel_id: int) -> dict | None:
    """Return a channel by its local row id."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM channels WHERE id = ?",
            (channel_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def _delete_links_for_chat(db: aiosqlite.Connection, chat_id: int) -> None:
    """Delete invite links for a chat. link_events are removed by cascade."""
    await db.execute("DELETE FROM invite_links WHERE chat_id = ?", (chat_id,))


async def delete_channel(channel_id: int) -> bool:
    """Delete a channel and all of its invite links."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT chat_id FROM channels WHERE id = ?",
            (channel_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False

        await _delete_links_for_chat(db, row["chat_id"])
        cursor = await db.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
        await db.commit()
        return cursor.rowcount > 0


async def get_channel_by_chat_id(chat_id: int) -> dict | None:
    """Return a channel by Telegram chat id."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM channels WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_channel_by_chat_id(chat_id: int) -> bool:
    """Delete a channel by Telegram chat id and remove its invite links."""
    async with get_db() as db:
        await _delete_links_for_chat(db, chat_id)
        cursor = await db.execute(
            "DELETE FROM channels WHERE chat_id = ?",
            (chat_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def save_invite_link(
    chat_id: int, chat_title: str, invite_link: str, campaign_name: str
) -> int:
    """Save an invite link and return its row id."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO invite_links (chat_id, chat_title, invite_link, campaign_name)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, chat_title, invite_link, campaign_name),
        )
        await db.commit()
        return cursor.lastrowid


async def get_link_by_url(invite_link: str) -> dict | None:
    """Find an invite link by URL."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM invite_links WHERE invite_link = ?",
            (invite_link,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_link_by_id(link_id: int) -> bool:
    """Delete an invite link by id."""
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM invite_links WHERE id = ?",
            (link_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def update_link_post_data(
    link_id: int, post_url: str | None, ad_price: float | None, post_views: int | None
) -> bool:
    """Save ad post metadata for an invite link."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            UPDATE invite_links
            SET post_url = ?, ad_price = ?, post_views = ?
            WHERE id = ?
            """,
            (post_url, ad_price, post_views, link_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def save_event(
    link_id: int,
    user_id: int,
    username: str,
    full_name: str,
    event_type: str,
) -> None:
    """Save a joined or left event."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO link_events (link_id, user_id, username, full_name, event_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            (link_id, user_id, username, full_name, event_type),
        )
        await db.commit()


async def get_all_links() -> list[dict]:
    """Return all invite links with aggregated stats."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT
                il.*,
                COALESCE(SUM(CASE WHEN le.event_type = 'joined' THEN 1 ELSE 0 END), 0) AS joined_count,
                COALESCE(SUM(CASE WHEN le.event_type = 'left' THEN 1 ELSE 0 END), 0) AS left_count
            FROM invite_links il
            LEFT JOIN link_events le ON le.link_id = il.id
            GROUP BY il.id
            ORDER BY il.created_at DESC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_links_by_channel(channel_id: int) -> list[dict]:
    """Return invite links for a specific channel with aggregated stats."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT
                il.*,
                COALESCE(SUM(CASE WHEN le.event_type = 'joined' THEN 1 ELSE 0 END), 0) AS joined_count,
                COALESCE(SUM(CASE WHEN le.event_type = 'left' THEN 1 ELSE 0 END), 0) AS left_count
            FROM invite_links il
            JOIN channels c ON c.chat_id = il.chat_id
            LEFT JOIN link_events le ON le.link_id = il.id
            WHERE c.id = ?
            GROUP BY il.id
            ORDER BY il.created_at DESC
            """,
            (channel_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_link_stats(link_id: int) -> dict | None:
    """Return detailed stats for one invite link."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT
                il.*,
                COALESCE(SUM(CASE WHEN le.event_type = 'joined' THEN 1 ELSE 0 END), 0) AS joined_count,
                COALESCE(SUM(CASE WHEN le.event_type = 'left' THEN 1 ELSE 0 END), 0) AS left_count
            FROM invite_links il
            LEFT JOIN link_events le ON le.link_id = il.id
            WHERE il.id = ?
            GROUP BY il.id
            """,
            (link_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_link_events(link_id: int, limit: int = 20) -> list[dict]:
    """Return recent events for an invite link."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT *
            FROM link_events
            WHERE link_id = ?
            ORDER BY event_date DESC
            LIMIT ?
            """,
            (link_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def find_user_join_link(chat_id: int, user_id: int) -> dict | None:
    """Find the latest invite link the user joined a chat with."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT il.id AS link_id, il.invite_link, il.campaign_name
            FROM link_events le
            JOIN invite_links il ON il.id = le.link_id
            WHERE il.chat_id = ? AND le.user_id = ? AND le.event_type = 'joined'
            ORDER BY le.event_date DESC
            LIMIT 1
            """,
            (chat_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
