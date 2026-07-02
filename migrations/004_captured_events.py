"""004 — captured_events table for the spike-01 capture tap.

An append-only raw event log (see capture.py): every Discord surface event the
live bot witnesses, stored verbatim as JSONB for offline transduction into
faebot-core Observations. The BIGSERIAL id is the watermark the offline pull
script (faebot-private/snippets/discord/pull_captures.py) pages through.

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
                WHERE table_name = 'captured_events'
            )
            """
        )
        if exists:
            count = await conn.fetchval("SELECT COUNT(*) FROM captured_events")
            logging.info(
                f"captured_events already exists with {count} rows — nothing to migrate"
            )
        else:
            logging.info("captured_events does not exist yet — creating")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS captured_events (
                id          BIGSERIAL PRIMARY KEY,
                captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                kind        TEXT NOT NULL,
                payload     JSONB NOT NULL
            )
            """
        )
        logging.info("✅ captured_events table ready")

        # kind is the cheap filter axis (e.g. skip typing/socket_raw when pulling)
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_captured_events_kind
            ON captured_events (kind)
            """
        )
        logging.info("✅ kind index ready")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
