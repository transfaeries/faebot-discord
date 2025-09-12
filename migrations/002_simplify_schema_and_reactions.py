import asyncio
import asyncpg
import os
import logging

logging.basicConfig(level=logging.INFO)


async def migrate():
    database_url = os.getenv("DATABASE_URL")
    if "localhost:5432" in database_url:
        database_url += (
            "?sslmode=disable" if "?" not in database_url else "&sslmode=disable"
        )

    logging.info("Connecting to database...")
    conn = await asyncpg.connect(database_url)

    try:
        # Drop existing tables (cascade to handle foreign keys)
        logging.info("Dropping old tables...")
        await conn.execute("DROP TABLE IF EXISTS messages CASCADE")
        await conn.execute("DROP TABLE IF EXISTS conversation_participants CASCADE")
        await conn.execute("DROP TABLE IF EXISTS user_profiles CASCADE")
        # Check if we're about to destroy data
        result = await conn.fetchval("SELECT COUNT(*) FROM conversations")
        if result > 0:
            logging.warning(f"⚠️  About to drop {result} conversations!")
            response = input("Continue? (yes/no): ")
            if response.lower() != "yes":
                logging.info("Migration cancelled")
                return
        await conn.execute("DROP TABLE IF EXISTS conversations CASCADE")

        # Create new simplified schema
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL DEFAULT 'discord',
                conversation_metadata JSONB NOT NULL,
                conversation_history JSONB NOT NULL,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        logging.info("✅ Created new conversations table")

        # Bot messages with reaction tracking
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_messages (
                id SERIAL PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                message_id TEXT UNIQUE,
                content TEXT NOT NULL,
                context JSONB,
                reactions JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        logging.info("✅ Created bot_messages table")

        # Create indexe
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_created ON bot_messages (conversation_id, created_at DESC)"
        )
        logging.info("✅ Created indexes")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
