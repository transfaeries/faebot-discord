import asyncio
import asyncpg
import os
import logging

logging.basicConfig(level=logging.INFO)


async def create_tables():
    # Get database URL and add sslmode=disable for local proxy
    database_url = os.getenv("DATABASE_URL")
    if "localhost:5432" in database_url:
        database_url += (
            "?sslmode=disable" if "?" not in database_url else "&sslmode=disable"
        )

    logging.info("Connecting to database...")
    conn = await asyncpg.connect(database_url)

    try:
        # Create conversations table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL DEFAULT 'discord',
                conversation_type TEXT NOT NULL,
                name TEXT NOT NULL,

                server_id TEXT,
                server_name TEXT,
                channel_name TEXT,
                channel_topic TEXT,

                history_length INTEGER DEFAULT 69,
                reply_frequency REAL DEFAULT 0.05,
                model TEXT DEFAULT 'google/gemini-2.0-flash-001',
                prompt_template TEXT,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_message_at TIMESTAMP
            )
        """
        )
        logging.info("✅ Created conversations table")

        # Create messages table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id),
                author TEXT NOT NULL,
                content TEXT NOT NULL,

                is_bot_message BOOLEAN DEFAULT FALSE,
                discord_message_id TEXT,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        logging.info("✅ Created messages table")

        # Create index for fast message retrieval
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_time
            ON messages (conversation_id, created_at DESC)
        """
        )
        logging.info("✅ Created message index")

        # Create conversation participants table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_participants (
                conversation_id TEXT REFERENCES conversations(id),
                username TEXT NOT NULL,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0,

                PRIMARY KEY (conversation_id, username)
            )
        """
        )
        logging.info("✅ Created conversation_participants table")

        # Create user profiles table for future use
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                username TEXT PRIMARY KEY,
                platform TEXT NOT NULL DEFAULT 'discord',

                notes TEXT,
                preferences JSONB,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        logging.info("✅ Created user_profiles table")

        logging.info("All tables created successfully!")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(create_tables())
