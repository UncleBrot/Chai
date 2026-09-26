import aiosqlite

DB_PATH = "confessions.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                confess_channel_id INTEGER,
                log_channel_id INTEGER,
                next_number INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS confessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                number INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                message_id INTEGER,
                thread_id INTEGER
            )
            """
        )
        await conn.commit()


async def _ensure_guild_row(conn, guild_id: int):
    await conn.execute(
        "INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,)
    )


async def get_settings(guild_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def set_confess_channel(guild_id: int, channel_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await _ensure_guild_row(conn, guild_id)
        await conn.execute(
            "UPDATE guild_settings SET confess_channel_id = ? WHERE guild_id = ?",
            (channel_id, guild_id),
        )
        await conn.commit()


async def set_log_channel(guild_id: int, channel_id):
    async with aiosqlite.connect(DB_PATH) as conn:
        await _ensure_guild_row(conn, guild_id)
        await conn.execute(
            "UPDATE guild_settings SET log_channel_id = ? WHERE guild_id = ?",
            (channel_id, guild_id),
        )
        await conn.commit()


async def next_number(guild_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        await _ensure_guild_row(conn, guild_id)
        cursor = await conn.execute(
            "SELECT next_number FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        row = await cursor.fetchone()
        number = row[0]
        await conn.execute(
            "UPDATE guild_settings SET next_number = ? WHERE guild_id = ?",
            (number + 1, guild_id),
        )
        await conn.commit()
        return number


async def create_confession(guild_id: int, number: int, author_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute(
            "INSERT INTO confessions (guild_id, number, author_id) VALUES (?, ?, ?)",
            (guild_id, number, author_id),
        )
        await conn.commit()
        return cursor.lastrowid


async def set_message_id(confession_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE confessions SET message_id = ? WHERE id = ?",
            (message_id, confession_id),
        )
        await conn.commit()


async def set_thread_id(confession_id: int, thread_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE confessions SET thread_id = ? WHERE id = ?",
            (thread_id, confession_id),
        )
        await conn.commit()


async def get_confession(confession_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM confessions WHERE id = ?", (confession_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
