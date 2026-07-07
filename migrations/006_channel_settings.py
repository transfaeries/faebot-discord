"""006 — channel_settings: per-channel dials split out of the metadata blob.

The settings-split (see-and-edit story): settings move from the JSONB
metadata blob (glued to conversation history, stamped at creation, editable
only via fragile DM commands) into a typed table the database can defend
with CHECK constraints and any interface can read/write.

Inheritance model (NULL = inherit):
  - one row per conversation THAT OVERRIDES something (no row = all-inherit)
  - '__default__' policy row: the defaults for guild channels
  - '__default_dm__' policy row: what's DIFFERENT about DMs (its NULLs fall
    through to '__default__')
  Effective setting: guild = COALESCE(override, default)
                     dm    = COALESCE(override, dm_default, default)

The policy rows are seeded here (universal — every environment wants them);
channel-specific overrides are environment data and are applied manually,
NOT in this migration (they reference snowflake ids that only exist in one
database).

conversation_id matches conversations.id by convention — deliberately no
FOREIGN KEY, because the policy rows aren't conversations.

Additive and idempotent — safe before the code that reads it deploys;
ON CONFLICT DO NOTHING so re-runs never clobber edited defaults.
"""

import argparse
import asyncio
import asyncpg
import os
import logging
import sys

logging.basicConfig(level=logging.INFO)


def resolve_database_url() -> str:
    """Require an explicit --env; read the matching explicit variable.

    The stale-shell disarm (2026-07-07): migrations no longer read ambient
    DATABASE_URL — you must SAY which world you mean, and the variable name
    matches the secrets file that provides it (dev_discord_secrets.fish /
    prod_discord_secrets.fish). Pattern for all migrations from 006 onward.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True, choices=["dev", "prod"],
                        help="which database this migration targets")
    arguments = parser.parse_args()

    variable = "PROD_DATABASE_URL" if arguments.env == "prod" else "DEV_DATABASE_URL"
    database_url = os.getenv(variable, "")
    if not database_url:
        sys.exit(f"{variable} is not set — source ../secrets/{arguments.env}_discord_secrets.fish first")
    logging.info(f"Requested environment: {arguments.env} (via {variable})")

    if "localhost:5432" in database_url:
        database_url += (
            "?sslmode=disable" if "?" not in database_url else "&sslmode=disable"
        )
    return database_url


async def migrate():
    database_url = resolve_database_url()

    logging.info("Connecting to database...")
    conn = await asyncpg.connect(database_url)

    try:
        # Announce the target BEFORE acting (the 005 lesson).
        target = await conn.fetchrow(
            "SELECT current_user AS usr, current_database() AS db"
        )
        logging.info(f"Target: {target['usr']} @ {target['db']}")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_settings (
                conversation_id TEXT PRIMARY KEY,
                model           TEXT,
                reply_frequency NUMERIC CHECK (reply_frequency BETWEEN 0 AND 1),
                history_length  INT  CHECK (history_length > 0),
                prompt_template TEXT
            )
            """
        )
        logging.info("✅ channel_settings table ready")

        seeded = await conn.execute(
            """
            INSERT INTO channel_settings
                (conversation_id, model, reply_frequency, history_length, prompt_template)
            VALUES
                ('__default__',    'moonshotai/kimi-k2', 0.05, 69,   'default'),
                ('__default_dm__', NULL,                 1.0,  NULL, 'dm')
            ON CONFLICT (conversation_id) DO NOTHING
            """
        )
        logging.info(f"Policy rows: {seeded} (0 = already present, untouched)")

        rows = await conn.fetch(
            "SELECT * FROM channel_settings ORDER BY conversation_id"
        )
        for row in rows:
            logging.info(f"  {dict(row)}")
        logging.info(f"✅ channel_settings holds {len(rows)} row(s)")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
