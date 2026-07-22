"""settings_cli.py — the see-and-edit instrument (step 5 of the story).

Read and set per-channel dials without spamming a channel or fighting DM
commands. Talks to Postgres directly — through `fly proxy` for prod — so
there is NO new attack surface on the bot, and the tool survives the move off
fly (faebot's own computer, ~2026-08-06).

    poetry run python settings_cli.py show  --env prod          # needs fly proxy
    poetry run python settings_cli.py set   --env prod --id <id> --property frequency --value 0.2
    poetry run python settings_cli.py unset --env prod --id <id> --property frequency

Run any of these with pieces missing and the error teaches the next step: no
--id lists where to find one, --id alone lists the properties with their
current values, --property alone shows current/inherited/allowed and writes
nothing (a single-channel inspector, on purpose).

set vs unset: `set` writes an override on this channel; `unset` REMOVES the
override so the channel goes back to inheriting — and keeps inheriting when
the policy row later changes. Copying today's default into a row is a
different, usually-wrong thing, so there is no verb for it.

The inheritance rules are NOT reimplemented here: this calls the bot's own
`get_effective_settings()` / `set_channel_setting()`, so the instrument and
the bot cannot drift apart. What the CLI adds is the *overview* — every
channel at once, grouped by server, each value marked as this channel's own
override or inherited from policy. It reads only discord-owned tables;
recovering anything from `captured_events` would couple these settings to
core's raw material.

Environment (the stale-shell disarm, 2026-07-07): --env is required and picks
which explicit variable to read — DEV_DATABASE_URL / PROD_DATABASE_URL, the
names the secrets files provide. Nothing ambient. The connected database is
then checked against its own `meta` stamp (migration 007), so a proxy pointed
at the wrong world fails loudly instead of quietly editing production.

Where a conversation lives (guild id/name, is_dm) is recorded by the bot on
every message and backfilled by backfill_locations.py; DMs therefore resolve
their own inheritance chain. `--dm` only forces it for conversations that
haven't been located yet.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from decimal import Decimal

import asyncpg

from database import (
    DEFAULT_DM_ROW,
    DEFAULT_ROW,
    SETTINGS_COLUMNS,
    FaebotDatabase,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")

POLICY_ROWS = (DEFAULT_ROW, DEFAULT_DM_ROW)


def resolve_database_url(environment: str) -> str:
    """Read the explicit variable for this environment, or exit saying which."""
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


async def open_database(environment: str) -> FaebotDatabase:
    """Connect with the CLI's own env resolution, then verify the DB agrees."""
    database = FaebotDatabase()
    # FaebotDatabase picks DATABASE_URL/DEV_DATABASE_URL from the bot's own
    # ENVIRONMENT; the CLI says which world it means on the command line
    # instead, so the resolved URL is assigned explicitly here.
    database.database_url = resolve_database_url(environment)
    await database.connect()
    if not database.pool:
        sys.exit("could not connect — is the fly proxy open? (pgprobe --fly)")
    await database.assert_environment(environment)
    return database


def pool_of(database: FaebotDatabase):
    """The connection pool, narrowed — open_database guarantees one exists."""
    if database.pool is None:
        sys.exit("database connection lost")
    return database.pool


async def load_channels(database: FaebotDatabase) -> list[dict]:
    """Every conversation with its name, newest activity first."""
    async with pool_of(database).acquire() as connection:
        rows = await connection.fetch(
            "SELECT id, conversation_metadata, last_updated FROM conversations"
        )
    channels = []
    for row in rows:
        metadata = row["conversation_metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        channels.append(
            {
                "id": row["id"],
                "name": metadata.get("name", "?"),
                "last_updated": row["last_updated"],
                "guild_id": metadata.get("guild_id"),
                "guild_name": metadata.get("guild_name"),
                "is_dm": metadata.get("is_dm"),
            }
        )
    return sorted(channels, key=lambda channel: channel["name"].lower())


DM_GROUP = "direct messages"
UNKNOWN_GROUP = (
    "server not recorded yet — heals when faebot next speaks there, "
    "or run backfill_locations.py"
)


async def load_overrides(database: FaebotDatabase) -> dict[str, dict]:
    """Raw channel_settings rows — which fields a channel sets for itself."""
    async with pool_of(database).acquire() as connection:
        rows = await connection.fetch("SELECT * FROM channel_settings")
    return {row["conversation_id"]: dict(row) for row in rows}


def format_value(column: str, value) -> str:
    if value is None:
        return "—"
    if column == "reply_frequency":
        return f"{float(value):.2f}"
    return str(value)


async def show(environment: str, dm: bool) -> None:
    database = await open_database(environment)
    try:
        channels = await load_channels(database)
        overrides = await load_overrides(database)

        header = f"{'channel':<24} {'id':<20} {'model':<22} {'freq':>6} {'hist':>5} {'template':<9} {'last activity':<16}"

        # Group by where the conversation lives: the answer to "which room is
        # this, really" — duplicate channel NAMES across servers are normal
        # (the old embassy's faebots-room sits armed beside the live one).
        # Name AND id, because humans read names and ids disambiguate.
        def group_of(channel: dict) -> str:
            if channel["is_dm"]:
                return DM_GROUP
            if not channel["guild_id"]:
                return UNKNOWN_GROUP
            name = channel["guild_name"] or "(unnamed server)"
            return f"{name}  ({channel['guild_id']})"

        by_group: dict[str, list[dict]] = {}
        for channel in channels:
            by_group.setdefault(group_of(channel), []).append(channel)

        # Rows whose location was never recorded (or was erased by a
        # shutdown-save) get resolved down the guild chain by default. That
        # is a GUESS, and for a DM it is the wrong one — so count them and
        # say so rather than printing a confident wrong number.
        unknown_location = 0

        def group_sort_key(item):
            group, members = item
            unresolved = group in (DM_GROUP, UNKNOWN_GROUP)
            return (unresolved, group == UNKNOWN_GROUP, -len(members), group)

        for group, members in sorted(by_group.items(), key=group_sort_key):
            print()
            print(f"▸ {group}")
            print(header)
            print("─" * len(header))
            for channel in members:
                # Per-channel DM resolution once recorded; --dm forces it for
                # conversations that haven't healed yet. When is_dm is NULL we
                # are guessing: the DM chain and the guild chain give different
                # answers, so a stale DM printed 0.05/default while the live bot
                # was really using 1.00/dm (2026-07-21). The bot is never wrong
                # here — it reads the live channel object every message; only
                # this table can be. Mark the row instead of guessing quietly.
                location_known = channel["is_dm"] is not None
                is_dm = channel["is_dm"] if location_known else dm
                if not location_known:
                    unknown_location += 1
                settings = await database.get_effective_settings(
                    channel["id"], is_dm=is_dm
                )
                own = overrides.get(channel["id"], {})
                cells = []
                for column in SETTINGS_COLUMNS:
                    mark = "*" if own.get(column) is not None else "·"
                    cells.append((mark, format_value(column, settings[column])))
                name_cell = ("?" if not location_known else "") + channel["name"][:23]
                print(
                    f"{name_cell:<24} {channel['id']:<20} "
                    f"{cells[0][0]}{cells[0][1][:21]:<21} "
                    f"{cells[1][0]}{cells[1][1]:>5} "
                    f"{cells[2][0]}{cells[2][1]:>4} "
                    f"{cells[3][0]}{cells[3][1][:8]:<8} "
                    f"{str(channel['last_updated'])[:16]:<16}"
                )

        print()
        print("─" * len(header))
        for policy_id in POLICY_ROWS:
            row = overrides.get(policy_id)
            if not row:
                print(f"{policy_id:<24} (missing! run migration 006)")
                continue
            values = " ".join(
                f"{column.split('_')[0]}={format_value(column, row[column])}"
                for column in SETTINGS_COLUMNS
            )
            print(f"{policy_id:<24} {values}")
        print()
        print("  * = set on this channel   · = inherited   — = unset")
        print(
            f"  resolution: {'DM (own → __default_dm__ → __default__)' if dm else 'guild (own → __default__)'}"
        )
        if unknown_location:
            print()
            print(
                f"  ? = location not recorded ({unknown_location} row(s)) — resolved "
                "as a guild channel."
            )
            print(
                "      If one of these is a DM, its freq/template above are WRONG. "
                "The bot is\n"
                "      right regardless; only this table is guessing. Fix: post one "
                "message there\n"
                "      (it self-heals), or run backfill_locations.py — or re-run with "
                "--dm to\n"
                "      resolve these down the DM chain instead."
            )
        print()
    finally:
        await database.close()


# Friendly names accepted alongside the real column names, so what `show`
# prints and what `set` takes never diverge.
PROPERTY_ALIASES = {
    "model": "model",
    "frequency": "reply_frequency",
    "reply_frequency": "reply_frequency",
    "history": "history_length",
    "history_length": "history_length",
    "template": "prompt_template",
    "prompt_template": "prompt_template",
}


# The short name shown back to the user for each column.
FRIENDLY_NAMES = {
    "model": "model",
    "reply_frequency": "frequency",
    "history_length": "history",
    "prompt_template": "template",
}


def resolve_property(name: str) -> str:
    if name not in PROPERTY_ALIASES:
        sys.exit(
            f"unknown property {name!r} — one of: "
            + ", ".join(sorted(set(PROPERTY_ALIASES)))
        )
    return PROPERTY_ALIASES[name]


def coerce(column: str, raw: str):
    """Text from the command line -> the type the column actually stores."""
    try:
        if column == "reply_frequency":
            return Decimal(raw)  # NUMERIC: asyncpg wants Decimal, not float
        if column == "history_length":
            return int(raw)
    except (ArithmeticError, ValueError):
        sys.exit(f"{raw!r} is not a valid value for {column}")
    return raw


async def load_constraints(database: FaebotDatabase) -> dict[str, str]:
    """The CHECK constraints, quoted back as hints — the DB is the truth."""
    async with pool_of(database).acquire() as connection:
        rows = await connection.fetch(
            "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
            "WHERE conrelid = 'channel_settings'::regclass AND contype = 'c'"
        )
    hints: dict[str, str] = {}
    for row in rows:
        definition = row["definition"]
        for column in SETTINGS_COLUMNS:
            if column in definition:
                hints[column] = definition
    return hints


async def describe(database: FaebotDatabase, conversation_id: str) -> dict | None:
    """Name + location for a conversation id; None if it isn't one we know."""
    for channel in await load_channels(database):
        if channel["id"] == conversation_id:
            return channel
    return None


async def count_inheritors(
    database: FaebotDatabase, column: str, policy_row: str
) -> int:
    """How many conversations would feel a change to this policy row."""
    channels = await load_channels(database)
    overrides = await load_overrides(database)
    affected = 0
    for channel in channels:
        own = overrides.get(channel["id"], {})
        if own.get(column) is not None:
            continue  # sets its own — unaffected
        if policy_row == DEFAULT_DM_ROW and not channel["is_dm"]:
            continue
        if policy_row == DEFAULT_ROW and channel["is_dm"]:
            dm_policy = overrides.get(DEFAULT_DM_ROW, {})
            if dm_policy.get(column) is not None:
                continue  # the DM policy already answers for them
        affected += 1
    return affected


def label_for(channel: dict | None, conversation_id: str) -> str:
    if conversation_id in POLICY_ROWS:
        return f"{conversation_id} (policy row)"
    if not channel:
        return conversation_id
    if channel["is_dm"]:
        return f"{channel['name']} (DM)"
    where = channel["guild_name"] or channel["guild_id"] or "server unrecorded"
    return f"{channel['name']} ({where})"


async def write_setting(
    environment: str,
    conversation_id: str | None,
    property_name: str | None,
    value: str | None,
    clearing: bool,
) -> None:
    """set/unset, with a ladder of errors that each teach the next step."""
    database = await open_database(environment)
    try:
        if not conversation_id:
            sys.exit(
                "which channel? pass --id <conversation id>\n"
                f"  run:  settings_cli.py show --env {environment}    to list them\n"
                f"  policy rows are ids too: {DEFAULT_ROW}, {DEFAULT_DM_ROW}"
            )

        channel = await describe(database, conversation_id)
        if not channel and conversation_id not in POLICY_ROWS:
            sys.exit(
                f"no conversation {conversation_id!r} — check the id against "
                f"`show --env {environment}`"
            )
        heading = label_for(channel, conversation_id)

        overrides = await load_overrides(database)
        own = overrides.get(conversation_id, {})
        # bool(None) is False, so an unrecorded location silently resolves down
        # the guild chain. Harmless for the WRITE (a column is a column), but
        # the current/inherited values we quote back would be from the wrong
        # chain — say so before the reader trusts them.
        if channel and channel["is_dm"] is None:
            print(
                f"  ! location not recorded for {heading} — resolving as a guild "
                "channel.\n"
                "    If this is a DM, the current/inherited values below are wrong.\n"
                "    Fix: post one message there, or run backfill_locations.py",
                file=sys.stderr,
            )
        is_dm = bool(channel["is_dm"]) if channel else conversation_id == DEFAULT_DM_ROW
        effective = await database.get_effective_settings(conversation_id, is_dm=is_dm)

        if not property_name:
            lines = [f"{heading} — name a property:"]
            for column in SETTINGS_COLUMNS:
                mark = "*" if own.get(column) is not None else "·"
                origin = "override" if own.get(column) is not None else "inherited"
                friendly = FRIENDLY_NAMES[column]
                lines.append(
                    f"  --property {friendly:<10} currently {mark}"
                    f"{format_value(column, effective[column]):<22} ({origin})"
                )
            sys.exit("\n".join(lines))

        column = resolve_property(property_name)

        if not clearing and value is None:
            hints = await load_constraints(database)
            inherited = "—"
            if conversation_id not in POLICY_ROWS:
                policy = overrides.get(DEFAULT_DM_ROW if is_dm else DEFAULT_ROW, {})
                fallback = policy.get(column)
                if fallback is None:
                    fallback = overrides.get(DEFAULT_ROW, {}).get(column)
                inherited = format_value(column, fallback)
            message = [
                f"{heading} · {column}",
                f"  current:   {format_value(column, effective[column])}"
                f"   ({'override on this channel' if own.get(column) is not None else 'inherited'})",
            ]
            if conversation_id not in POLICY_ROWS:
                message.append(f"  inherited: {inherited}")
            if column in hints:
                message.append(f"  allowed:   {hints[column]}")
            if conversation_id in POLICY_ROWS:
                affected = await count_inheritors(database, column, conversation_id)
                message.append(f"  inherited by: {affected} conversation(s)")
            if conversation_id == DEFAULT_ROW:
                # The base policy has nothing above it: clearing it drops the
                # bot onto its emergency defaults, loudly. Not an unset target.
                message.append(
                    "  pass --value <v>   (this row is the bottom of "
                    "the chain — clearing it falls back to the bot's "
                    "emergency defaults)"
                )
            else:
                message.append(
                    f"  pass --value <v>, or:  settings_cli.py unset --env {environment} "
                    f"--id {conversation_id} --property {property_name}   to resume inheriting"
                )
            sys.exit("\n".join(message))

        before = format_value(column, own.get(column))
        if clearing:
            new_value = None
        else:
            # The ladder above exits when a set has no --value, so by here
            # it is present; spelled out so the type checker agrees.
            new_value = coerce(column, value or "")

        if conversation_id in POLICY_ROWS:
            affected = await count_inheritors(database, column, conversation_id)
            print(
                f"⚠ {conversation_id} is a POLICY row — {affected} conversation(s) "
                f"inherit {column} from it"
            )

        try:
            # A CHECK refusal is an EXPECTED outcome here (the user typed a bad
            # value), and it's translated below — so the bot's own "unexpected
            # error" log line would be noise. Muted for this one call only.
            logging.disable(logging.ERROR)
            await database.set_channel_setting(conversation_id, column, new_value)
        except asyncpg.exceptions.CheckViolationError:
            # The database is the real guard; this just translates its refusal
            # into the same hint the ladder shows.
            hints = await load_constraints(database)
            sys.exit(
                f"refused: {format_value(column, new_value)} is not allowed for "
                f"{column}\n  allowed: {hints.get(column, '(see the table CHECKs)')}"
                f"\n  nothing was written."
            )
        finally:
            logging.disable(logging.NOTSET)

        after = format_value(column, new_value)
        arrow = f"{before} → {after}"
        note = " (now inheriting)" if clearing else " (override)"
        print(f"{heading} · {column}: {arrow}{note}")

        # Show what the channel now actually resolves to — an unset only shows
        # its effect through the inheritance chain.
        resolved = await database.get_effective_settings(conversation_id, is_dm=is_dm)
        print(f"  effective now: {format_value(column, resolved[column])}")
    finally:
        await database.close()


def main() -> None:
    # --env lives on a shared parent so it reads naturally AFTER the
    # subcommand: `settings_cli.py show --env dev`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--env",
        required=True,
        choices=["dev", "prod"],
        help="which database to talk to (reads DEV_/PROD_DATABASE_URL)",
    )

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    show_parser = subparsers.add_parser(
        "show", parents=[common], help="table of every channel's settings"
    )
    show_parser.add_argument(
        "--dm",
        action="store_true",
        help="resolve with DM rules for conversations whose kind isn't recorded",
    )

    # set/unset share --id and --property; each argument is optional so a
    # missing one can teach the next step instead of erroring blankly.
    write_common = argparse.ArgumentParser(add_help=False, parents=[common])
    write_common.add_argument(
        "--id",
        help="conversation id (never the name — " "names duplicate across servers)",
    )
    write_common.add_argument(
        "--property", help="model | frequency | history | template"
    )

    set_parser = subparsers.add_parser(
        "set", parents=[write_common], help="set one dial on one channel"
    )
    set_parser.add_argument("--value", help="the new value")
    subparsers.add_parser(
        "unset",
        parents=[write_common],
        help="remove an override so the channel inherits again (and keeps "
        "inheriting when the policy changes)",
    )

    arguments = parser.parse_args()

    if arguments.command == "show":
        asyncio.run(show(arguments.env, arguments.dm))
    elif arguments.command in ("set", "unset"):
        asyncio.run(
            write_setting(
                arguments.env,
                arguments.id,
                arguments.property,
                getattr(arguments, "value", None),
                clearing=arguments.command == "unset",
            )
        )


if __name__ == "__main__":
    main()
