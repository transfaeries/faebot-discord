import asyncpg
import asyncio
import os
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime


class FaebotDatabase:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
        self.database_url = os.getenv("DATABASE_URL")

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

    async def save_conversation(
        self, conversation_id: str, conversation_data: Dict[str, Any]
    ):
        """Save or update a conversation in the database"""
        if not self.pool:
            return

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO conversations (
                    id, platform, conversation_type, name,
                    server_id, server_name, channel_name, channel_topic,
                    history_length, reply_frequency, model, prompt_template,
                    last_message_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    server_name = EXCLUDED.server_name,
                    channel_topic = EXCLUDED.channel_topic,
                    history_length = EXCLUDED.history_length,
                    reply_frequency = EXCLUDED.reply_frequency,
                    model = EXCLUDED.model,
                    prompt_template = EXCLUDED.prompt_template,
                    last_message_at = EXCLUDED.last_message_at,
                    updated_at = CURRENT_TIMESTAMP
            """,
                conversation_id,
                "discord",  # platform
                "dm" if "DM" in conversation_data.get("name", "") else "channel",
                conversation_data.get("name", "Unknown"),
                conversation_data.get("server_id"),
                conversation_data.get("server_name"),
                conversation_data.get("channel_name"),
                conversation_data.get("channel_topic"),
                conversation_data.get("history_length", 69),
                conversation_data.get("reply_frequency", 0.05),
                conversation_data.get("model", "google/gemini-2.0-flash-001"),
                conversation_data.get("prompt"),
                datetime.utcnow(),
            )

    async def save_message(
        self,
        conversation_id: str,
        author: str,
        content: str,
        is_bot: bool = False,
        discord_message_id: str = None,
    ):
        """Save a message to the database"""
        if not self.pool:
            return

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO messages (
                    conversation_id, author, content, is_bot_message, discord_message_id
                ) VALUES ($1, $2, $3, $4, $5)
            """,
                conversation_id,
                author,
                content,
                is_bot,
                discord_message_id,
            )

            # Update participant tracking
            await conn.execute(
                """
                INSERT INTO conversation_participants (
                    conversation_id, username, message_count
                ) VALUES ($1, $2, 1)
                ON CONFLICT (conversation_id, username) DO UPDATE SET
                    last_seen_at = CURRENT_TIMESTAMP,
                    message_count = conversation_participants.message_count + 1
            """,
                conversation_id,
                author,
            )

    async def load_conversations(self) -> Dict[str, Dict[str, Any]]:
        """Load all active conversations from the database"""
        if not self.pool:
            return {}

        conversations = {}

        async with self.pool.acquire() as conn:
            # Load conversation metadata
            rows = await conn.fetch(
                """
                SELECT id, name, server_id, server_name, channel_name, channel_topic,
                       history_length, reply_frequency, model, prompt_template
                FROM conversations
                WHERE platform = 'discord'
                ORDER BY last_message_at DESC
            """
            )

            for row in rows:
                conversation_id = row["id"]

                # Load recent conversation history
                messages = await conn.fetch(
                    """
                    SELECT author, content, created_at, is_bot_message
                    FROM messages
                    WHERE conversation_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                """,
                    conversation_id,
                    row["history_length"],
                )

                # Format messages in the expected format
                conversation_history = []
                for msg in reversed(messages):  # Reverse to get chronological order
                    timestamp = msg["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                    author = "faebot" if msg["is_bot_message"] else msg["author"]
                    conversation_history.append(
                        f"[{timestamp}] {author}: {msg['content']}"
                    )

                # Load conversants
                participants = await conn.fetch(
                    """
                    SELECT username FROM conversation_participants
                    WHERE conversation_id = $1
                    ORDER BY last_seen_at DESC
                """,
                    conversation_id,
                )

                conversants = [
                    p["username"] for p in participants if p["username"] != "faebot"
                ]

                # Reconstruct conversation dict
                conversations[conversation_id] = {
                    "id": conversation_id,
                    "conversation": conversation_history,
                    "conversants": conversants,
                    "history_length": row["history_length"],
                    "reply_frequency": row["reply_frequency"],
                    "name": row["name"],
                    "prompt": row["prompt_template"],
                    "model": row["model"],
                    "server_name": row["server_name"] or "",
                    "channel_name": row["channel_name"] or "",
                    "channel_topic": row["channel_topic"] or "",
                }

                logging.info(
                    f"Loaded conversation {row['name']} with {len(conversation_history)} messages"
                )

        return conversations

    async def get_recent_messages(
        self, conversation_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get recent messages for a conversation"""
        if not self.pool:
            return []

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT author, content, created_at, is_bot_message
                FROM messages
                WHERE conversation_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            """,
                conversation_id,
                limit,
            )

            return [dict(row) for row in rows]


# Create a singleton instance
db = FaebotDatabase()
