"""005 — drop the bot_messages table (superseded by captured_events).

bot_messages stored each bot post with the context that fed its prompt — send-
point provenance. Since the capture tap (004 + PR #26), captured_events records
the same send point with MORE metadata (prompt, model, context) as
kind='faebot_message', plus the gateway echo — so bot_messages is a legacy
organ. Its planned reactions feature was never used and reaction provenance
now lives in captured_events too (reaction_add / reaction_remove events).

Verdict: fae, 2026-07-06 — "completely superseded; can go today."
Take a backup dump BEFORE running this (backup_db.py); the dropped rows
remain recoverable from any prior dump forever.

Idempotent — safe to re-run; reports state before/after.
"""

import asyncio
import asyncpg
import os
import logging

logging.basicConfig(level=logging.INFO)


async def migrate():
    database_url = os.getenv("DATABASE_URL")
    if "localhost:5432" in database_url:
        database_url += (
            "?sslmode=disable" if "?" not in database_url else "&sslmode=disable"
        )

    logging.info("Connecting to database...")
    conn = await asyncpg.connect(database_url)

    try:
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'bot_messages'
            )
            """
        )
        if exists:
            count = await conn.fetchval("SELECT COUNT(*) FROM bot_messages")
            logging.info(f"bot_messages exists with {count} rows — dropping")
            await conn.execute("DROP TABLE bot_messages")
            logging.info(f"✅ bot_messages dropped ({count} rows retired; recoverable from dumps)")
        else:
            logging.info("bot_messages does not exist — nothing to migrate")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
