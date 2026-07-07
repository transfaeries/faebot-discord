# Database Migrations

Run migrations **in order** on a fresh database:

- `001_initial_schema.py` — Creates initial tables (conversations, messages, participants, user profiles)
- `002_simplify_schema_and_reactions.py` — Replaces the schema with the simplified one faebot uses today (conversations with JSONB metadata/history, plus a `bot_messages` table for reaction tracking)
- `003_conversants_list_to_dict.py` — Converts the `conversants` field from list to dict format (no-op on a fresh database)
- `004_captured_events.py` — Creates the append-only `captured_events` raw event log for the capture tap (see `capture.py`; no-op if it already exists)
- `005_drop_bot_messages.py` — Drops the `bot_messages` table (superseded by `captured_events`; take a backup dump first — no-op once dropped)
- `006_channel_settings.py` — Creates the typed `channel_settings` table (the settings-split) and seeds the `__default__` / `__default_dm__` policy rows. Channel-specific overrides are environment data — applied manually, never in the migration.

To run each:

```bash
poetry run python migrations/001_initial_schema.py
poetry run python migrations/002_simplify_schema_and_reactions.py
poetry run python migrations/003_conversants_list_to_dict.py
poetry run python migrations/004_captured_events.py
poetry run python migrations/005_drop_bot_messages.py
poetry run python migrations/006_channel_settings.py --env dev   # or --env prod
```

> **From 006 onward**, migrations require an explicit `--env dev|prod` flag and
> read `DEV_DATABASE_URL` / `PROD_DATABASE_URL` (never ambient `DATABASE_URL`) —
> the variable name matches the secrets file that provides it. They also
> announce `current_user @ current_database` before acting. Both habits exist
> because a stale-sourced shell once pointed a migration at the wrong database
> (2026-07-06).

> **Note:** `001` must run before `002` even though `002` rebuilds the schema — `002` performs a safety check (`SELECT COUNT(*) FROM conversations`) before dropping tables, which requires the `conversations` table created by `001` to exist.

`DATABASE_URL` must be set in your environment before running migrations.
