import asyncpg
import os
import logging
import json
from typing import Dict, List, Optional, Any


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

    async def close(self):
        """Close the connection pool"""
        if self.pool:
            await self.pool.close()
            logging.info("Database connection pool closed")

    async def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get a single conversation from database"""
        if not self.pool:
            return None

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

            return None

    async def save_conversation(
        self,
        conversation_id: str,
        conversation_data: Dict[str, Any],
        merge_history: bool = True,
    ):
        """Save or update a conversation's full state"""
        if not self.pool:
            logging.warning("No database pool - cannot save bot message")
            return

        # Split conversation data into metadata and history
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
            if merge_history:
                # Check existing history length
                existing = await conn.fetchrow(
                    "SELECT jsonb_array_length(conversation_history) as len FROM conversations WHERE id = $1",
                    conversation_id,
                )

            if existing and existing["len"] > len(history):
                logging.warning(
                    f"Not overwriting conversation {conversation_id}: DB has {existing['len']} messages, memory has {len(history)}"
                )
                # Just update metadata, keep history
                await conn.execute(
                    """
                    UPDATE conversations
                    SET conversation_metadata = $1, last_updated = CURRENT_TIMESTAMP
                    WHERE id = $2
                """,
                    json.dumps(metadata),
                    conversation_id,
                )
                return

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
                f"Saved conversation {conversation_data['name']} with {len(history)} messages"
            )

    async def save_bot_message(
        self,
        conversation_id: str,
        content: str,
        context: List[str],
        message_id: Optional[str] = None,
    ):
        """Save a bot message with its context"""
        if not self.pool:
            return

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
                json.dumps(context),  # Last ~5 messages that prompted this response
            )
            logging.debug(
                f"Saved bot message to DB: {conversation_id} - {content[:50]}..."
            )

    async def load_conversations(self) -> Dict[str, Dict[str, Any]]:
        """Load all conversations from the database"""
        if not self.pool:
            return {}

        conversations = {}

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, conversation_metadata, conversation_history
                FROM conversations
                WHERE platform = 'discord'
                ORDER BY last_updated DESC
            """
            )

            for row in rows:
                conversation_id = row["id"]
                metadata = json.loads(row["conversation_metadata"])
                history = json.loads(row["conversation_history"])

                # Reconstruct the conversation dict as expected by the bot
                conversations[conversation_id] = {
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

                logging.info(
                    f"Loaded conversation {metadata.get('name')} with {len(history)} messages"
                )

        return conversations

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
