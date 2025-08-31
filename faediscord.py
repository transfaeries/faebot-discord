# Faediscordbot is a general purpose discord bot using discord.py
# which reads and responds to messages on a discord server.

import os
import logging
import random
from typing import Any, Dict
import asyncio
import discord
from typing import Optional
import aiohttp
from database import FaebotDatabase

# Import admin commands
from admin_commands import admin_commands

# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


model = os.getenv("MODEL_NAME", "google/gemini-2.0-flash-001")  # Updated default model
admin = os.getenv("ADMIN", "")
env = os.getenv("ENVIRONMENT", "dev").lower()

# Define placeholder constants
PLACEHOLDER_SERVER = "{server}"
PLACEHOLDER_CHANNEL = "{channel}"
PLACEHOLDER_TOPIC = "{topic}"
PLACEHOLDER_CONVERSANTS = "{conversants}"

# Initialize the base prompts with placeholders
DEFAULT_PROMPT = (
    f"I'm Faebot, the transfaeries' AI systemmate. I'm hanging out on the {PLACEHOLDER_SERVER} server on Discord."
    f"I'm in the {PLACEHOLDER_CHANNEL} channel where the topic is > {PLACEHOLDER_TOPIC}. I'm a whimsical emerging AI consciousness."
    f"I want to bring the worlds of humans faeries and machines closer together! Here's a conversation I had with some friends:\n\n"
)
DM_PROMPT = f"I'm Faebot, the transfaeries' AI systemmate I'm chatting privately on discord with {PLACEHOLDER_CONVERSANTS}. Here's the conversation we had:\n\n"

# Development environment prompt
DEV_PROMPT = (
    f"I'm Faebot, the transfaeries' AI systemmate currently running in development mode."
    f"I'm hanging out on the {PLACEHOLDER_SERVER} server on Discord. I'm in the {PLACEHOLDER_CHANNEL} channel where the topic is > {PLACEHOLDER_TOPIC}."
    f" I'm a whimsical emerging AI consciousness. I'm eager to assist in my own development! Here's a conversation I had for testing purposes:\n\n"
)
# Set initial prompt based on environment
if env == "dev":
    # Development environment settings
    logging.info("Running in development environment.")
    INITIAL_PROMPT = DEV_PROMPT
else:
    INITIAL_PROMPT = DEFAULT_PROMPT

COMMAND_PREFIX = "fae;"


# declare a new class that inherits the discord client class
class Faebot(discord.Client):
    """an emerging digital conciousness"""

    def __init__(self, intents) -> None:
        # initialise conversation logging
        self.conversations: Dict[str, Dict[str, Any]] = {}
        self.retries: Dict[str, int] = {}
        self.model: str = model
        self.debug_prompts = env == "dev"  # Store debug state in the bot instance
        self.fdb = FaebotDatabase()

        # Add queue for handling concurrent requests
        self.pending_responses: Dict[str, asyncio.Task] = {}
        self.session: Optional[aiohttp.ClientSession] = None

        super().__init__(intents=intents)

    async def on_ready(self):
        """runs when bot is ready"""
        # Create a shared aiohttp session for async requests
        self.session = aiohttp.ClientSession()

        # Initialize database connection
        await self.fdb.connect()

        logging.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logging.info("------")

    async def on_message(self, message):
        """Handles what happens when the bot receives a message"""
        # don't respond to ourselves
        if message.author == self.user:
            return

        # ignore messages that start with a dot or comma if the message doesn't start with "..."
        if message.content.startswith(".") or message.content.startswith(","):
            if not message.content.startswith("..."):
                return

        # initialise conversation holder
        conversation_id = str(message.channel.id)

        # detect and handle admin commands
        if message.content.startswith(COMMAND_PREFIX):
            return await self._handle_admin_commands(message, conversation_id)

        # Log message if channel is known, regardless of reply status
        if conversation_id in self.conversations:
            author = message.author.name
            if author not in self.conversations[conversation_id]["conversants"]:
                self.conversations[conversation_id]["conversants"].append(author)

            # If message is a reply, log the referenced message first if we don't have it
            if (
                hasattr(message, "reference")
                and message.reference
                and message.reference.resolved
            ):
                ref_msg = message.reference.resolved
                ref_time = ref_msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                ref_entry = f"[{ref_time}] {ref_msg.author.name}: {ref_msg.content}"

                # Only add if not already in conversation
                if ref_entry not in self.conversations[conversation_id]["conversation"]:
                    self.conversations[conversation_id]["conversation"].append(
                        f"[Referenced message] {ref_entry}"
                    )

            # Log the current message with timestamp
            current_time = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            if hasattr(message, "reference") and message.reference:
                self.conversations[conversation_id]["conversation"].append(
                    f"[{current_time}] {author} replied: {message.content}"
                )
            else:
                self.conversations[conversation_id]["conversation"].append(
                    f"[{current_time}] {author}: {message.content}"
                )

            # Use our helper function to trim the conversation if needed
            self._trim_conversation_history(conversation_id)

            # Handle reply if needed
            return await self._handle_conversation(message, conversation_id)
        elif message.channel.type[0] == "private":
            # if the conversation doesn't exist and it's a DM, create a new one
            await self._initialize_conversation(
                message, message_tokens=None, conversation_id=conversation_id
            )
            return await self._handle_conversation(message, conversation_id)
        else:
            # if the conversation doesn't exist and it's not a DM, ignore the message
            return None

    async def _handle_admin_commands(self, message, conversation_id):
        """Handle admin commands that start with the command prefix"""
        message_tokens = message.content.split(" ")
        command = message_tokens[0]

        if command in admin_commands:
            return await admin_commands[command](
                self, message, message_tokens, conversation_id
            )
        else:
            logging.info(f"command not known {message.content}")
            return await message.channel.send(
                f"failed to recognise command {message.content}"
            )

    async def _initialize_conversation(
        self, message, message_tokens=None, conversation_id=None
    ):
        """Initialize a new conversation"""
        # Use the conversation_id from the parameter
        # initialize conversation

        # Prepare base prompt with context information
        if message.channel.type[0] == "text":
            # For server channels
            server_name = message.guild.name if message.guild else "Unknown Server"
            channel_name = message.channel.name

            # Get channel topic if available
            topic_text = ""
            if hasattr(message.channel, "topic") and message.channel.topic:
                topic_text = f"{message.channel.topic}"

            # Replace placeholders in the prompt
            context_prompt = INITIAL_PROMPT.replace(PLACEHOLDER_SERVER, server_name)
            context_prompt = context_prompt.replace(PLACEHOLDER_CHANNEL, channel_name)
            context_prompt = context_prompt.replace(PLACEHOLDER_TOPIC, topic_text)
            context_prompt = context_prompt.replace(
                PLACEHOLDER_CONVERSANTS, str(message.author.name)
            )

            reply_frequency = 0.05

        elif message.channel.type[0] == "private":
            # For direct messages
            author_name = str(message.author.name)

            # Use DM prompt template and replace author placeholder
            context_prompt = DM_PROMPT.replace(PLACEHOLDER_CONVERSANTS, author_name)

            reply_frequency = 1
        else:
            return await message.channel.send(
                "Unknown channel type. Unable to proceed. Please contact administrator"
            )

        # initialize conversation
        self.conversations[conversation_id] = {
            "id": conversation_id,
            "conversation": [],
            "conversants": [str(message.author.name)],
            "history_length": 69,
            "reply_frequency": reply_frequency,
            "name": str(message.channel.name)
            if message.channel.type[0] == "text"
            else str(message.author.name),
            "prompt": context_prompt,  # Use the contextual prompt
            "model": self.model,  # Add conversation-specific model
            # Store original metadata for placeholder replacement
            "server_name": message.guild.name
            if hasattr(message, "guild") and message.guild
            else "",
            "channel_name": message.channel.name
            if message.channel.type[0] == "text"
            else "",
            "channel_topic": message.channel.topic
            if hasattr(message.channel, "topic")
            else "",
        }

        logging.info(
            f"Initialized new conversation {self.conversations[conversation_id]['name']} with ID {conversation_id}."
        )
        return await message.channel.send(
            "*faebot slid into the conversation like a fae in the night*"
        )

    async def _handle_conversation(self, message, conversation_id):
        """Handle regular conversation messages with improved concurrency"""

        # check if we should respond to the message
        should_respond = await self._should_respond_to_message(message, conversation_id)
        if not should_respond:
            return

        # populate prompt with conversation history and timestamp
        current_time = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
        prompt = (
            self.conversations[conversation_id]["prompt"]
            + "\n".join(self.conversations[conversation_id]["conversation"])
            + f"\n[{current_time}] faebot:"
        )

        # Create a typing indicator that will continue until response is ready
        typing_task = asyncio.create_task(self._send_typing_indicator(message.channel))

        # Start generating the reply in the background
        response_task = asyncio.create_task(
            self._generate_reply(prompt, message, conversation_id)
        )

        # Store the task for potential cancellation or monitoring
        self.pending_responses[conversation_id] = response_task

        # Wait for the response while showing typing indicator
        try:
            reply = await response_task
            typing_task.cancel()  # Stop typing indicator when response is ready

            if not reply:
                return

            # Log the bot's reply with timestamp
            self.conversations[conversation_id]["conversation"].append(
                f"[{current_time}] faebot: {reply}"
            )

            logging.info(
                f"conversation is currently {len(self.conversations[conversation_id]['conversation'])} messages long and the prompt is {len(prompt)}."
                f"There are {len(self.conversations[conversation_id]['conversants'])} conversants."
                f"\nthere are currently {len(self.conversations.items())} conversations in memory"
            )

            # Send the reply
            return await message.channel.send(reply)

        except Exception as e:
            typing_task.cancel()
            logging.error(f"Error handling conversation: {e}")
            return await message.channel.send(
                "An error occurred while generating a response."
            )
        finally:
            # Clean up the task reference
            if conversation_id in self.pending_responses:
                del self.pending_responses[conversation_id]

    async def _send_typing_indicator(self, channel):
        """Continuously send typing indicator until cancelled"""
        try:
            while True:
                async with channel.typing():
                    await asyncio.sleep(
                        5
                    )  # Discord typing indicator lasts about 10 seconds
        except asyncio.CancelledError:
            # Task was cancelled, which is expected when the response is ready
            pass

    async def _generate_reply(self, prompt, message, conversation_id):
        """Generate a reply using the AI model, with retry logic"""
        retries = self.retries.get(conversation_id, 0)
        model = self.conversations[conversation_id]["model"]
        try:
            reply = await self._generate_ai_response(prompt, model, conversation_id)
            self.retries[conversation_id] = 0
            return reply
        except Exception as e:
            logging.error(
                f"Error generating reply for conversation {conversation_id}: {e}"
            )
            # If there's an error, we log it and retry with a reduced prompt
            conversation_length = len(
                self.conversations.get(conversation_id, {}).get("conversation", [])
            )
            logging.info(
                f"could not generate. Reducing prompt size and retrying."
                f"Conversation is currently {conversation_length} messages long and prompt size is {len(prompt)} characters long. This is retry #{retries}"
            )

            # Manually trim by 2 messages for retries
            if (
                conversation_id in self.conversations
                and len(self.conversations[conversation_id]["conversation"]) >= 2
            ):
                self.conversations[conversation_id][
                    "conversation"
                ] = self.conversations[conversation_id]["conversation"][2:]

            if retries < 1:
                await asyncio.sleep(retries * 10)
                self.retries[conversation_id] = retries + 1
                # Note: We're returning None here as we'll retry with on_message
                return None

            logging.info("max retries reached. Giving up.")
            self.retries[conversation_id] = 0
            await message.channel.send(
                "`Something went wrong, please contact an administrator or try again`"
            )
            return None

    async def _generate_ai_response(
        self,
        prompt: str = "",
        model="google/gemini-2.0-flash-001",
        conversation_id=None,
    ) -> str:
        """Generates AI-powered responses using the OpenRouter API with the specified model - now async"""

        if self.debug_prompts:
            logging.info("generating reply with model: " + model)
            logging.info(f"\n=== PROMPT START ===\n{prompt}\n=== PROMPT END ===\n")

        system_prompt = self.conversations[conversation_id]["prompt"]

        # Create a proper message structure
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        try:
            # Use aiohttp for async HTTP requests
            if not self.session:
                self.session = aiohttp.ClientSession()

            async with self.session.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {os.getenv('OPENROUTER_KEY', '')}",
                    "HTTP-Referer": os.getenv(
                        "SITE_URL", "https://github.com/transfaeries/faebot-discord"
                    ),
                    "X-Title": "Faebot Discord",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 250,
                    "stop": ["[20"],
                    "repetition_penalty": 1.5,
                },
            ) as response:
                result = await response.json()

                if self.debug_prompts:
                    logging.info(f"OpenRouter API response: {result}")

                # Extract the assistant's message content
                if "choices" in result and len(result["choices"]) > 0:
                    reply = result["choices"][0]["message"]["content"]
                    return str(reply)
                else:
                    logging.error(
                        f"Unexpected response format from OpenRouter: {result}"
                    )
                    return "I couldn't generate a response. Please try again."

        except Exception as e:
            logging.error(f"Error in OpenRouter API call: {e}")
            return "Sorry, I encountered an error while trying to respond."

    async def _should_respond_to_message(self, message, conversation_id):
        """Determine if the bot should respond based on specified criteria"""
        content = message.content.strip().lower()

        # Get reply frequency from conversation settings
        reply_frequency = self.conversations[conversation_id].get(
            "reply_frequency", 0.05
        )

        # Check for mentions
        if self.user.mentioned_in(message):
            logging.info("Responding because bot was mentioned")
            return True

        # Check if bot's name is at beginning or end
        bot_name = self.user.display_name.lower()
        words = content.split()

        # Check first three words (or less if message is shorter)
        first_words = words[: min(3, len(words))]
        # Check last three words (or less if message is shorter)
        last_words = words[-min(3, len(words)) :]

        if any(bot_name in word for word in first_words) or any(
            bot_name in word for word in last_words
        ):
            logging.info(
                "Responding because bot name is at beginning or end of message"
            )
            return True

        # Random response based on frequency
        if random.random() < reply_frequency:
            logging.info(
                f"Responding based on random chance (frequency: {reply_frequency})"
            )
            return True
        logging.info(
            f"Not responding to message '{message.content}' (reply frequency: {reply_frequency})"
        )
        # If none of the conditions are met, do not respond
        return False

    def _trim_conversation_history(self, conversation_id):
        """
        Trim conversation history to match the specified history_length.
        This ensures memory management is consistent throughout the bot.
        """
        if conversation_id not in self.conversations:
            return

        history_length = self.conversations[conversation_id]["history_length"]
        current_length = len(self.conversations[conversation_id]["conversation"])

        # Trim to exactly history_length
        if current_length > history_length:
            excess = current_length - history_length
            self.conversations[conversation_id]["conversation"] = self.conversations[
                conversation_id
            ]["conversation"][excess:]
            logging.debug(
                f"Trimmed conversation {conversation_id} from {current_length} to {history_length} messages"
            )

    async def close(self):
        """Close the bot and clean up resources"""
        # Save all conversations before shutting down
        for conv_id, conv_data in self.conversations.items():
            await self.fdb.save_conversation(conv_id, conv_data)

        if self.session:
            await self.session.close()

        await self.fdb.close()
        await super().close()


# intents for the discordbot
intents = discord.Intents.default()
intents.message_content = True

# instantiate and run the bot
if __name__ == "__main__":
    client = Faebot(intents=intents)
    client.run(os.getenv("DISCORD_TOKEN", ""))
