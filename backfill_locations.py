"""backfill_locations.py — one-off: record WHERE each conversation lives.

From 2026-07-20 the bot stamps `guild_id` / `guild_name` / `is_dm` into a
conversation's metadata on every message, so active channels heal themselves.
Dormant channels never save, so they would stay unlocated forever — this
script asks Discord once and fills them in.

    poetry run python backfill_locations.py --env prod              # dry run
    poetry run python backfill_locations.py --env prod --write      # do it

Idempotent + diagnostic (house rule): reports counts before and after, only
touches conversations MISSING the fields, and is safe to re-run. Default is a
dry run — nothing is written without --write.

Needs DISCORD_TOKEN (the bot's own, from the secrets file) plus the usual
DEV_/PROD_DATABASE_URL. This is the ONLY tool here that talks to Discord; the
settings CLI stays database-only on purpose.
"""

import argparse
import asyncio
import json
import logging
import os
import sys

import asyncpg
import discord

logging.basicConfig(level=logging.WARNING, format="%(message)s")

LOCATION_KEYS = ("guild_id", "guild_name", "is_dm")


def resolve_database_url(environment: str) -> str:
    variable = "PROD_DATABASE_URL" if environment == "prod" else "DEV_DATABASE_URL"
    database_url = os.getenv(variable, "")
    if not database_url:
        sys.exit(
            f"{variable} is not set — source ../secrets/{environment}"
            f"_discord_secrets.fish first"
        )
    if "localhost:5432" in database_url and "sslmode" not in database_url:
        joiner = "&" if "?" in database_url else "?"
        database_url += f"{joiner}sslmode=disable"
    return database_url


async def guild_name(client: discord.Client, guild_id: int, cache: dict) -> str | None:
    """Fetch a guild's NAME over REST.

    Without a gateway connection there is no guild cache, so the `.guild` on a
    fetched channel is a stub carrying only an id — the name needs its own
    call. Cached here because a handful of channels share a few servers.
    """
    if guild_id in cache:
        return cache[guild_id]
    try:
        cache[guild_id] = (await client.fetch_guild(guild_id)).name
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as error:
        print(f"    (couldn't read server name: {error})")
        cache[guild_id] = None
    return cache[guild_id]


async def locate(client: discord.Client, conversation_id: str, cache: dict) -> dict | None:
    """Ask Discord where this conversation lives. None = couldn't tell."""
    try:
        channel = await client.fetch_channel(int(conversation_id))
    except discord.NotFound:
        print(f"    not found (deleted channel, or bot removed) — leaving unrecorded")
        return None
    except discord.Forbidden:
        print(f"    no access (bot can't see it) — leaving unrecorded")
        return None
    except (discord.HTTPException, ValueError) as error:
        print(f"    lookup failed: {error} — leaving unrecorded")
        return None

    guild = getattr(channel, "guild", None)
    if guild is None:
        return {"guild_id": None, "guild_name": None, "is_dm": True}
    return {
        "guild_id": str(guild.id),
        "guild_name": await guild_name(client, guild.id, cache),
        "is_dm": False,
    }


async def backfill(environment: str, write: bool) -> None:
    # Tokens follow the same disarm as the database URLs: prod's lives under a
    # distinct name so a dev-shaped invocation cannot pick it up by accident.
    token_variable = "DISCORD_TOKEN_PROD" if environment == "prod" else "DISCORD_TOKEN"
    token = os.getenv(token_variable, "")
    if not token:
        sys.exit(
            f"{token_variable} is not set — source ../secrets/{environment}"
            f"_discord_secrets.fish first"
        )

    connection = await asyncpg.connect(resolve_database_url(environment))
    stamped = await connection.fetchval(
        "SELECT count(*) FROM conversations WHERE conversation_metadata ? 'guild_id' "
        "OR conversation_metadata ? 'is_dm'"
    )
    total = await connection.fetchval("SELECT count(*) FROM conversations")
    print(f"Found {total} conversations, {stamped} already located, "
          f"{total - stamped} to look up.")
    if total == stamped:
        print("Nothing to backfill.")
        await connection.close()
        return

    rows = await connection.fetch(
        "SELECT id, conversation_metadata FROM conversations"
    )

    client = discord.Client(intents=discord.Intents.default())
    await client.login(token)

    located = 0
    name_cache: dict[int, str | None] = {}
    try:
        for row in rows:
            metadata = row["conversation_metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            if any(key in metadata for key in LOCATION_KEYS):
                continue  # already located — idempotent

            name = metadata.get("name", "?")
            print(f"  {name} ({row['id']})")
            location = await locate(client, row["id"], name_cache)
            if location is None:
                continue

            where = "DM" if location["is_dm"] else f"{location['guild_name']} ({location['guild_id']})"
            print(f"    → {where}")
            located += 1
            if not write:
                continue

            metadata.update({
                key: value for key, value in location.items() if value is not None
            })
            if location["is_dm"]:
                metadata["is_dm"] = True
            await connection.execute(
                "UPDATE conversations SET conversation_metadata = $1 WHERE id = $2",
                json.dumps(metadata), row["id"],
            )
    finally:
        await client.close()

    verb = "located" if write else "would locate"
    print(f"\n{verb} {located} conversations."
          + ("" if write else "  (dry run — pass --write to apply)"))
    if write:
        now_stamped = await connection.fetchval(
            "SELECT count(*) FROM conversations WHERE conversation_metadata ? 'guild_id' "
            "OR conversation_metadata ? 'is_dm'"
        )
        print(f"Located conversations: {stamped} → {now_stamped}.")
    await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True, choices=["dev", "prod"])
    parser.add_argument("--write", action="store_true",
                        help="actually write (default is a dry run)")
    arguments = parser.parse_args()
    asyncio.run(backfill(arguments.env, arguments.write))


if __name__ == "__main__":
    main()
