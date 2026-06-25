# Database Migrations

Run migrations **in order** on a fresh database:

- `001_initial_schema.py` — Creates initial tables (conversations, messages, participants, user profiles)
- `002_simplify_schema_and_reactions.py` — Replaces the schema with the simplified one faebot uses today (conversations with JSONB metadata/history, plus a `bot_messages` table for reaction tracking)
- `003_conversants_list_to_dict.py` — Converts the `conversants` field from list to dict format (no-op on a fresh database)

To run each:

```bash
poetry run python migrations/001_initial_schema.py
poetry run python migrations/002_simplify_schema_and_reactions.py
poetry run python migrations/003_conversants_list_to_dict.py
```

> **Note:** `001` must run before `002` even though `002` rebuilds the schema — `002` performs a safety check (`SELECT COUNT(*) FROM conversations`) before dropping tables, which requires the `conversations` table created by `001` to exist.

`DATABASE_URL` must be set in your environment before running migrations.
