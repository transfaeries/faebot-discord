import asyncpg
import asyncio
import os
import logging
import json
from typing import Dict, List, Optional, Any
from functools import wraps


def with_retry(max_retries=3, initial_delay=1, backoff_factor=2):
    """Decorator for database operations with retry logic for dormant connections"""

    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            last_exception = None
            delay = initial_delay

            for attempt in range(max_retries):
                try:
                    # Check if pool exists and try to validate connection
                    if self.pool:
                        # Test the connection with a simple query
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
                    ConnectionRefusedError,  # Add this - database completely offline
                    OSError,  # Add this - catches general connection failures
                ) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logging.warning(
                            f"Database connection error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}"
                            f"\nRetrying in {delay} seconds..."
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff_factor

                        # Try to recreate the pool if it's a connection issue
                        if (
                            "starting up" in str(e)
                            or "connection" in str(e).lower()
                            or "Connect call failed" in str(e)
                        ):  # Add this check
                            logging.info("Attempting to recreate connection pool...")
                            await self._recreate_pool()
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
        self.database_url = os.getenv("DATABASE_URL", "")

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
                        "conversants": metadata.get("conversants", []),
                        "history_length": metadata.get("history_length", 69),
                        "reply_frequency": metadata.get("reply_frequency", 0.05),
                        "name": metadata.get("name", "Unknown"),
                        "prompt": metadata.get("prompt", ""),
                        "model": metadata.get("model", "google/gemini-2.0-flash-001"),
                        "server_name": metadata.get("server_name", ""),
                        "channel_name": metadata.get("channel_name", ""),
                        "channel_topic": metadata.get("channel_topic", ""),
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
        force_overwrite: bool = False,
    ):
        """Save or update a conversation's full state"""
        if not self.pool:
            logging.warning("No database pool - cannot save conversation")
            return False

        try:
            metadata = {
                "id": conversation_data["id"],
                "name": conversation_data["name"],
                "history_length": conversation_data["history_length"],
                "reply_frequency": conversation_data["reply_frequency"],
                "prompt": conversation_data["prompt"],
                "model": conversation_data["model"],
                "server_name": conversation_data.get("server_name", ""),
                "channel_name": conversation_data.get("channel_name", ""),
                "channel_topic": conversation_data.get("channel_topic", ""),
                "conversants": conversation_data.get("conversants", []),
            }

            history = conversation_data.get("conversation", [])

            async with self.pool.acquire() as conn:
                # Check if conversation exists
                existing = await conn.fetchrow(
                    """
                    SELECT
                        jsonb_array_length(conversation_history) as len,
                        conversation_history->-1 as last_message  -- Get last message
                    FROM conversations
                    WHERE id = $1
                    """,
                    conversation_id,
                )

                if existing and not force_overwrite:
                    existing_len = existing["len"]

                    # Check if we're potentially losing messages
                    if existing_len > len(history):
                        # Try to detect if this is a legitimate trim vs data loss
                        last_db_message = (
                            json.loads(existing["last_message"])
                            if existing["last_message"]
                            else None
                        )

                        # Check if our history contains the last DB message
                        if last_db_message and last_db_message not in history:
                            logging.warning(
                                f"⚠️ Refusing to save {conversation_id}: "
                                f"DB has {existing_len} messages, memory has {len(history)}, "
                                f"and memory doesn't contain last DB message. "
                                f"This might lose data!"
                            )
                            # Just update metadata
                            await conn.execute(
                                """
                                UPDATE conversations
                                SET conversation_metadata = $1, last_updated = CURRENT_TIMESTAMP
                                WHERE id = $2
                                """,
                                json.dumps(metadata),
                                conversation_id,
                            )
                            logging.info(
                                f"Updated metadata only for conversation {conversation_id}"
                            )
                            return True

                # Normal save
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

                action = "Created" if not existing else "Updated"
                logging.info(
                    f"✅ {action} conversation {conversation_data['name']} "
                    f"with {len(history)} messages in database"
                )
                return True

        except (TypeError, ValueError) as e:  # JSON encoding errors
            logging.error(f"Failed to encode conversation data as JSON: {e}")
            return False
        except Exception as e:
            logging.error(f"Failed to save conversation {conversation_id}: {e}")
            raise

    @with_retry()
    async def save_bot_message(
        self,
        conversation_id: str,
        content: str,
        context: List[str],
        message_id: Optional[str] = None,
    ):
        """Save a bot message with its context"""
        if not self.pool:
            logging.warning("No database pool - cannot save bot message")
            return False

        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO bot_messages (
                        conversation_id, message_id, content, context
                    ) VALUES ($1, $2, $3, $4)
                    """,
                    conversation_id,
                    message_id,
                    content,
                    json.dumps(context),
                )
                logging.info(
                    f"✅ Saved bot message to database: {conversation_id} - {content[:50]}..."
                )
                return True

        except (TypeError, ValueError) as e:  # JSON encoding errors
            logging.error(f"Failed to encode context as JSON: {e}")
            return False
        except Exception as e:
            logging.error(f"Failed to save bot message for {conversation_id}: {e}")
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
                            "conversants": metadata.get("conversants", []),
                            "history_length": metadata.get("history_length", 69),
                            "reply_frequency": metadata.get("reply_frequency", 0.05),
                            "name": metadata.get("name", "Unknown"),
                            "prompt": metadata.get("prompt", ""),
                            "model": metadata.get(
                                "model", "google/gemini-2.0-flash-001"
                            ),
                            "server_name": metadata.get("server_name", ""),
                            "channel_name": metadata.get("channel_name", ""),
                            "channel_topic": metadata.get("channel_topic", ""),
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

    async def update_reactions(self, message_id: str, reactions: Dict[str, int]):
        """Update reactions on a bot message (for future use)"""
        if not self.pool:
            return

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE bot_messages
                SET reactions = $1
                WHERE message_id = $2
            """,
                json.dumps(reactions),
                message_id,
            )
