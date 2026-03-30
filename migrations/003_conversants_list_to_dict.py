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
        # Check how many rows need migration
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM conversations
            WHERE jsonb_typeof(conversation_metadata->'conversants') = 'array'
            """
        )
        logging.info(f"Found {count} conversations with old list-format conversants")

        if count == 0:
            logging.info("Nothing to migrate")
            return

        # Convert conversants from list ["user1", "user2"]
        # to dict {"user1": "user1", "user2": "user2"}
        # Display names aren't known for historical data, so username is used as both.
        # They'll be updated with real display names as users send new messages.
        result = await conn.execute(
            """
            UPDATE conversations
            SET conversation_metadata = jsonb_set(
                conversation_metadata,
                '{conversants}',
                (SELECT jsonb_object_agg(value, value)
                 FROM jsonb_array_elements_text(conversation_metadata->'conversants'))
            )
            WHERE jsonb_typeof(conversation_metadata->'conversants') = 'array'
            """
        )
        logging.info(f"✅ Migrated conversants to dict format: {result}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
