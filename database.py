import asyncpg
import asyncio
import os
import logging
import json
from typing import Dict, Optional, Any
from functools import wraps

env = os.getenv("ENVIRONMENT", "dev").lower()
DEFAULT_TEMPLATE = "dev" if env == "dev" else "default"

# The four per-channel dials, now sourced from the channel_settings table
# (the settings split). Whitelist — set_channel_setting refuses anything else,
# so the column name can be safely interpolated into SQL.
SETTINGS_COLUMNS = ("model", "reply_frequency", "history_length", "prompt_template")

# Special rows in channel_settings: policy defaults, not conversations.
DEFAULT_ROW = "__default__"
DEFAULT_DM_ROW = "__default_dm__"

# Last-resort floor if channel_settings has no usable value at all (e.g. the
# __default__ row is somehow missing). Never-break-the-bot; a use is logged
# loudly because it means the defaults row needs fixing.
SETTINGS_EMERGENCY_DEFAULTS = {
    "model": "moonshotai/kimi-k2",
    "reply_frequency": 0.05,
    "history_length": 69,
    "prompt_template": "default",
}


def with_retry(max_retries=3, initial_delay=1, backoff_factor=2):
    """Decorator for database operations with retry logic for dormant connections"""

    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            last_exception = None
            delay = initial_delay

            for attempt in range(max_retries):
                try:
                    # If no pool exists, try to create one
                    if not self.pool:
                        logging.info(
                            f"No pool available, attempting to create one (attempt {attempt + 1})"
                        )
                        await self.connect()

                    # Now validate the connection
                    async with self.pool.acquire() as conn:
                        await conn.fetchval("SELECT 1")

                    # If validation succeeds, execute the actual function
                    result = await func(self, *args, **kwargs)

                    # Log success if this was a retry
                    if attempt > 0:
                        logging.info(
                            f"✅ {func.__name__} succeeded after {attempt + 1} attempts"
                        )

                    return result

                except (
                    asyncpg.exceptions.CannotConnectNowError,
                    asyncpg.exceptions.PostgresConnectionError,
                    asyncpg.exceptions.InterfaceError,
                    ConnectionResetError,
                    ConnectionRefusedError,
                    OSError,
                ) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logging.warning(
                            f"Database connection error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}"
                            f"\nRetrying in {delay} seconds..."
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff_factor

                        logging.info("Attempting to recreate connection pool...")
                        try:
                            if self.pool:
                                await self.pool.close()
                            self.pool = None
                            await self.connect()
                        except Exception as pool_error:
                            logging.warning(f"Failed to recreate pool: {pool_error}")
                            self.pool = None
                    else:
                        logging.error(
                            f"❌ {func.__name__} failed after {max_retries} attempts: {e}"
                        )

                except Exception as e:
                    # For non-connection errors, log and raise immediately
                    logging.error(f"Unexpected error in {func.__name__}: {e}")
                    raise

            # If we've exhausted retries, raise the last exception
            if last_exception:
                raise last_exception

        return wrapper

    return decorator


class FaebotDatabase:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
        # The environment picks WHICH variable to read (the stale-shell
        # disarm, 2026-07-07): prod reads DATABASE_URL as fly injects it;
        # dev reads DEV_DATABASE_URL, so a shell polluted with prod secrets
        # cannot silently hand the dev bot the production database.
        url_variable = "DATABASE_URL" if env == "prod" else "DEV_DATABASE_URL"
        self.database_url = os.getenv(url_variable, "")

        # Add sslmode=disable for local proxy
        if self.database_url and "localhost:5432" in self.database_url:
            if "?" not in self.database_url:
                self.database_url += "?sslmode=disable"
            else:
                self.database_url += "&sslmode=disable"

    async def connect(self):
        """Create a connection pool for the database"""
        if not self.database_url:
            logging.error("DATABASE_URL not found!")
            return

        try:
            self.pool = await asyncpg.create_pool(
                self.database_url, min_size=1, max_size=10, command_timeout=60
            )
            logging.info("✅ Database connection pool created")
        except Exception as e:
            logging.error(f"Failed to create database pool: {e}")
            raise

    async def _recreate_pool(self):
        """Recreate the connection pool (useful when connections go stale)"""
        try:
            if self.pool:
                await self.pool.close()
                self.pool = None

            await self.connect()
            logging.info("✅ Connection pool recreated successfully")
        except Exception as e:
            logging.error(f"Failed to recreate connection pool: {e}")
            self.pool = None

    async def close(self):
        """Close the connection pool"""
        if self.pool:
            await self.pool.close()
            logging.info("Database connection pool closed")

    @with_retry()
    async def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get a single conversation from database"""
        if not self.pool:
            logging.warning("No database pool available - cannot get conversation")
            return None

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT conversation_metadata, conversation_history
                    FROM conversations
                    WHERE id = $1 AND platform = 'discord'
                    """,
                    conversation_id,
                )

                if row:
                    metadata = json.loads(row["conversation_metadata"])
                    history = json.loads(row["conversation_history"])

                    # Reconstruct the conversation dict as expected by the bot
                    return {
                        "id": conversation_id,
                        "conversation": history,
                        "conversants": metadata.get("conversants", {}),
                        "history_length": metadata.get("history_length", 69),
                        "reply_frequency": metadata.get("reply_frequency", 0.05),
                        "name": metadata.get("name", "Unknown"),
                        "prompt_template": metadata.get(
                            "prompt_template", DEFAULT_TEMPLATE
                        ),
                        "model": metadata.get("model", "google/gemini-2.0-flash-001"),
                    }
                else:
                    logging.debug(
                        f"No conversation found in database for ID: {conversation_id}"
                    )
                    return None

        except json.JSONDecodeError as e:
            logging.error(
                f"Failed to parse JSON for conversation {conversation_id}: {e}"
            )
            return None
        except Exception as e:
            logging.error(
                f"Unexpected error getting conversation {conversation_id}: {e}"
            )
            raise

    @with_retry()
    async def save_conversation(
        self,
        conversation_id: str,
        conversation_data: Dict[str, Any],
    ):
        """Save a conversation's identity + history.

        The metadata blob now holds only identity (name, conversants) — the
        four dials live in channel_settings (the settings split), so this write
        never touches them. The old shrink-guard is gone with them: history
        trimming and fae;forget are legitimate, so a shorter history is saved
        faithfully by the plain upsert. (Single-instance bot — no concurrent
        writer to guard against; revisit if that ever changes.)
        """
        if not self.pool:
            logging.warning("No database pool - cannot save conversation")
            return False

        try:
            metadata = {
                "id": conversation_data["id"],
                "name": conversation_data["name"],
                "conversants": conversation_data.get("conversants", {}),
            }
            history = conversation_data.get("conversation", [])

            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO conversations (
                        id, platform, conversation_metadata, conversation_history
                    ) VALUES ($1, $2, $3, $4)
                    ON CONFLICT (id) DO UPDATE SET
                        conversation_metadata = EXCLUDED.conversation_metadata,
                        conversation_history = EXCLUDED.conversation_history,
                        last_updated = CURRENT_TIMESTAMP
                    """,
                    conversation_id,
                    "discord",
                    json.dumps(metadata),
                    json.dumps(history),
                )
            logging.info(
                f"✅ Saved conversation {conversation_data['name']} "
                f"with {len(history)} messages"
            )
            return True

        except (TypeError, ValueError) as e:  # JSON encoding errors
            logging.error(f"Failed to encode conversation data as JSON: {e}")
            return False
        except Exception as e:
            logging.error(f"Failed to save conversation {conversation_id}: {e}")
            raise

    @with_retry()
    async def load_conversations(self) -> Dict[str, Dict[str, Any]]:
        """Load all conversations from the database"""
        if not self.pool:
            logging.warning("No database pool - returning empty conversations dict")
            # This is the ONLY case where we return empty - no pool at all
            return {}

        conversations = {}

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, conversation_metadata, conversation_history
                    FROM conversations
                    WHERE platform = 'discord'
                    ORDER BY last_updated DESC
                    """
                )

                if not rows:
                    logging.info(
                        "No conversations found in database (this is normal for first run)"
                    )
                    return {}  # Empty database is valid

                # Track load statistics
                successful = 0
                failed = 0

                for row in rows:
                    try:
                        conversation_id = row["id"]
                        metadata = json.loads(row["conversation_metadata"])
                        history = json.loads(row["conversation_history"])

                        conversations[conversation_id] = {
                            "id": conversation_id,
                            "conversation": history,
                            "conversants": metadata.get("conversants", {}),
                            "history_length": metadata.get("history_length", 69),
                            "reply_frequency": metadata.get("reply_frequency", 0.05),
                            "name": metadata.get("name", "Unknown"),
                            "prompt_template": metadata.get(
                                "prompt_template", DEFAULT_TEMPLATE
                            ),
                            "model": metadata.get(
                                "model", "google/gemini-2.0-flash-001"
                            ),
                        }
                        successful += 1
                        logging.debug(
                            f"Loaded conversation {metadata.get('name')} with {len(history)} messages"
                        )

                    except json.JSONDecodeError as e:
                        failed += 1
                        logging.error(
                            f"Failed to parse JSON for conversation {row['id']}: {e}, skipping..."
                        )
                    except Exception as e:
                        failed += 1
                        logging.error(
                            f"Failed to load conversation {row['id']}: {e}, skipping..."
                        )

                logging.info(
                    f"✅ Loaded {successful}/{successful + failed} conversations from database"
                    + (f" ({failed} failed)" if failed > 0 else "")
                )

                # If we failed to load everything, that's concerning
                if successful == 0 and failed > 0:
                    logging.error(
                        "❌ Failed to load ANY conversations despite having data in database!"
                    )
                    # But still return empty rather than crash - bot can create new conversations

            return conversations

        except Exception as e:
            # This means we couldn't even query the database after retries
            logging.error(
                f"❌ Critical failure loading conversations from database: {e}"
            )
            # Return empty dict - bot will start fresh but won't crash
            return {}

    @with_retry()
    async def get_effective_settings(
        self, conversation_id: str, is_dm: bool = False
    ) -> Dict[str, Any]:
        """Resolve a channel's effective settings via NULL-inheritance.

        Precedence (first non-NULL wins per field):
            guild: own row -> __default__
            dm:    own row -> __default_dm__ -> __default__
        Falls back to SETTINGS_EMERGENCY_DEFAULTS (loudly) if even the default
        row can't supply a value.
        """
        if not self.pool:
            logging.warning(
                "No database pool — serving emergency default settings for %s",
                conversation_id,
            )
            return dict(SETTINGS_EMERGENCY_DEFAULTS)

        wanted_ids = [conversation_id]
        if is_dm:
            wanted_ids.append(DEFAULT_DM_ROW)
        wanted_ids.append(DEFAULT_ROW)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM channel_settings WHERE conversation_id = ANY($1)",
                wanted_ids,
            )

        by_id = {row["conversation_id"]: row for row in rows}
        # Precedence order matches wanted_ids (own first, __default__ last).
        precedence = [by_id[row_id] for row_id in wanted_ids if row_id in by_id]

        resolved: Dict[str, Any] = {}
        for column in SETTINGS_COLUMNS:
            value = next(
                (row[column] for row in precedence if row[column] is not None), None
            )
            if value is None:
                value = SETTINGS_EMERGENCY_DEFAULTS[column]
                logging.warning(
                    "⚠️ No value for '%s' in channel_settings (channel %s) — "
                    "using emergency default %r. The %s row may need fixing.",
                    column, conversation_id, value, DEFAULT_ROW,
                )
            resolved[column] = value

        # reply_frequency is NUMERIC -> Decimal; the bot's dice want a float.
        resolved["reply_frequency"] = float(resolved["reply_frequency"])
        return resolved

    @with_retry()
    async def set_channel_setting(self, conversation_id: str, key: str, value: Any):
        """Write-through a single setting (UPSERT). NULL value = reset to inherit.

        `key` is whitelisted against SETTINGS_COLUMNS, so it is safe to
        interpolate into the SQL; values are always parameterised.
        """
        if key not in SETTINGS_COLUMNS:
            raise ValueError(f"unknown setting {key!r}")
        if not self.pool:
            logging.warning("No database pool — cannot set %s for %s", key, conversation_id)
            return False

        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO channel_settings (conversation_id, {key})
                VALUES ($1, $2)
                ON CONFLICT (conversation_id)
                DO UPDATE SET {key} = EXCLUDED.{key}
                """,
                conversation_id,
                value,
            )
        logging.info(
            "✅ channel_settings: %s.%s = %r", conversation_id, key, value
        )
        return True

    @with_retry()
    async def save_captured_event(self, kind: str, captured_at, payload_json: str):
        """Append one raw event to captured_events (the spike-01 capture tap).

        Append-only, never updated; capture.py swallows any failure so this can
        never disturb the bot. payload_json is already-serialized JSON.
        """
        if not self.pool:
            logging.debug("No database pool - cannot save captured event")
            return False

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO captured_events (captured_at, kind, payload)
                VALUES ($1, $2, $3::jsonb)
                """,
                captured_at,
                kind,
                payload_json,
            )
            return True
