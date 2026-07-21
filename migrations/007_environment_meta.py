"""007 — meta table: stamp the database's environment ('dev' or 'prod').

The third layer of the stale-shell disarm (see faebot-private
notes/infrastructure.md): after this, the *database itself* knows which
environment it is, and both the bot (at startup) and later migrations assert
a match — so a wrong-database connection is caught no matter HOW the bad URL
got into the environment (stale shell, pasted URL, proxy mixup, an agent
launched from a polluted terminal).

This migration is itself guarded: if the database is already stamped with a
DIFFERENT environment than --env, it refuses (you're pointing at the wrong DB).

Idempotent — safe to re-run.
"""

import argparse
import asyncio
import asyncpg
import os
import logging
import sys

logging.basicConfig(level=logging.INFO)


def resolve() -> tuple[str, str]:
    """Return (requested_env, database_url); require an explicit --env."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env",
        required=True,
        choices=["dev", "prod"],
        help="which database this migration targets + stamps",
    )
    arguments = parser.parse_args()

    variable = "PROD_DATABASE_URL" if arguments.env == "prod" else "DEV_DATABASE_URL"
    database_url = os.getenv(variable, "")
    if not database_url:
        sys.exit(
            f"{variable} is not set — source ../secrets/{arguments.env}_discord_secrets.fish first"
        )
    if "localhost:5432" in database_url:
        database_url += (
            "?sslmode=disable" if "?" not in database_url else "&sslmode=disable"
        )
    return arguments.env, database_url


async def migrate():
    requested_env, database_url = resolve()
    logging.info(f"Requested environment: {requested_env}")

    conn = await asyncpg.connect(database_url)
    try:
        target = await conn.fetchrow(
            "SELECT current_user AS usr, current_database() AS db"
        )
        logging.info(f"Target: {target['usr']} @ {target['db']}")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                id          INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                environment TEXT NOT NULL
            )
            """
        )

        stamped = await conn.fetchval("SELECT environment FROM meta WHERE id = 1")
        if stamped is None:
            await conn.execute(
                "INSERT INTO meta (id, environment) VALUES (1, $1)", requested_env
            )
            logging.info(f"✅ Database stamped as environment '{requested_env}'")
        elif stamped != requested_env:
            sys.exit(
                f"❌ MISMATCH: this database is already stamped '{stamped}', but you "
                f"ran --env {requested_env}. Wrong database?! Refusing to change the stamp."
            )
        else:
            logging.info(f"Already stamped '{stamped}' — matches, nothing to do")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
