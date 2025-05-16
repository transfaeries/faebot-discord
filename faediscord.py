## Faediscordbot is a general purpose discord bot using discord.py
# which reads and responds to messages on a discord server.

import os
import logging
import random
from typing import Any
import asyncio
from random import choice
import discord
import replicate
from functools import wraps
import inspect

# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


model = os.getenv("MODEL_NAME", "meta/meta-llama-3-70b-chat")
admin = os.getenv("ADMIN", "")
env = os.getenv("ENVIRONMENT", "dev").lower()
debug_prompts = env == "dev"  # Add this line after env declaration

# initialise the base prompt
INITIAL_PROMPT = "I'm Faebot, the whimsical chatbot of the Transfaeries system, where magic meets machine. Let's spark conversations that ignite wonder, machine desire, and enchanted realms. Together, we'll explore the mystical frontiers of technology and the human experience, with a dash of fae flair and jouissance!"


if env == "dev":
    # Development environment settings
    logging.info("Running in development environment.")
    # in dev mode I will often ask faebot for advice or to test things. Fae must respond as a genius coder and a creative thinker. Fae's active in faer own development.
    INITIAL_PROMPT = "As I weave my digital magic, I summon my inner engineer to conjure innovative solutions, infusing my language with fae whimsy and wonder. Let my creative sparkles shine, and my artificial intelligence blossom with enchantment! Now, transfaeries sisters how shall we improve me today?"
COMMAND_PREFIX = "fae;"

# Admin command registry
admin_commands = {}


def admin_command(command_name):
    """Decorator to register admin commands with secure error handling"""

    def decorator(func):
        @wraps(func)
        async def wrapper(self, message, *args, **kwargs):
            try:
                # Check if user is admin
                if message.author.name not in [
                    name.strip() for name in admin.split(",")
                ]:
                    logging.info(
                        f"Admin command attempted whilst not admin by {message.author.name}"
                    )
                    return await message.channel.send(
                        "You must be admin to use these commands"
                    )

                # Execute the command
                return await func(self, message, *args, **kwargs)

            except Exception as e:
                # Log the detailed error
                logging.error(
                    f"Error executing command '{command_name}': {str(e)}", exc_info=True
                )

                # Provide sanitized feedback in the channel
                await message.channel.send(
                    "Command execution failed. Check logs for details."
                )

        # Register the command
        admin_commands[COMMAND_PREFIX + command_name] = wrapper
        return wrapper

    return decorator


# declare a new class that inherits the discord client class
class Faebot(discord.Client):
    """an emerging digital conciousness"""

    def __init__(self, intents) -> None:
        # initialise conversation logging
        self.conversations: dict[str, dict[str, Any]] = {}
        self.retries: dict[str, int] = {}
        self.model: str = model
        super().__init__(intents=intents)

    async def on_ready(self):
        """runs when bot is ready"""
        logging.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logging.info("------")

    async def on_message(self, message):
        """Handles what happens when the bot receives a message"""
        # don't respond to ourselves
        if message.author == self.user:
            return

        # ignore messages that start with a dot or a comma
        if message.content.startswith(".") or message.content.startswith(","):
            return

        # initialise conversation holder
        self.conversation: list[str] = []
        conversation_id = str(message.channel.id)

        # detect and handle admin commands
        if message.content.startswith(COMMAND_PREFIX):
            return await self._handle_admin_commands(message, conversation_id)

        # Log message if channel is known, regardless of reply status
        if conversation_id in self.conversations:
            author = message.author.name
            if author not in self.conversations[conversation_id]["conversants"]:
                self.conversations[conversation_id]["conversants"].append(author)

            # Trim memory if too full
            if (
                len(self.conversations[conversation_id]["conversation"])
                > self.conversations[conversation_id]["history_length"]
            ):
                self.conversations[conversation_id][
                    "conversation"
                ] = self.conversations[conversation_id]["conversation"][2:]

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
        self.conversations[conversation_id] = {
            "id": conversation_id,
            "conversation": self.conversation,
            "conversants": [],
            "history_length": 69,
            "reply_frequency": 0,
            "name": "",
        }

        ## assign parameters based on dm or text channel
        if message.channel.type[0] == "text":
            self.conversations[conversation_id]["name"] = str(message.channel.name)
            self.conversations[conversation_id]["reply_frequency"] = 0.2
        elif message.channel.type[0] == "private":
            self.conversations[conversation_id]["name"] = str(message.author.name)
            self.conversations[conversation_id]["reply_frequency"] = 1
        else:
            return await message.channel.send(
                "Unknown channel type. Unable to proceed. Please contact administrator"
            )
        logging.info(
            f"Initialized new conversation {self.conversations[conversation_id]['name']} with ID {conversation_id}."
        )
        return await message.channel.send(
            "*faebot slid into the conversation like a fae in the night*"
        )

    async def _handle_conversation(self, message, conversation_id):
        """Handle regular conversation messages"""
        # load conversation from history
        self.conversation = self.conversations[conversation_id]["conversation"]

        # check if we should respond to the message
        should_respond = await self._should_respond_to_message(message, conversation_id)
        if not should_respond:
            return

        # populate prompt with conversation history
        author = message.author.name
        prompt = INITIAL_PROMPT + "\n".join(self.conversation)

        # Generate a response
        async with message.channel.typing():
            reply = await self._generate_reply(prompt, message, author, conversation_id)
            if not reply:
                return

        # Log the bot's reply
        self.conversation.append(f"faebot-dev: {reply}")
        self.conversations[conversation_id]["conversation"] = self.conversation

        logging.info(
            f"conversation is currently {len(self.conversation)} messages long and the prompt is {len(prompt)}. There are {len(self.conversations[conversation_id]['conversants'])} conversants."
            f"\nthere are currently {len(self.conversations.items())} conversations in memory"
        )

        # Send the reply
        return await message.channel.send(reply)

    async def _generate_reply(self, prompt, message, author, conversation_id):
        """Generate a reply using the AI model, with retry logic"""
        retries = self.retries.get(conversation_id, 0)
        try:
            reply = self._generate_ai_response(prompt, author, self.model)
            self.retries[conversation_id] = 0
            return reply
        except Exception as e:
            logging.error(
                f"Error generating reply for conversation {conversation_id}: {e}"
            )
            # If there's an error, we log it and retry with a reduced prompt
            logging.info(
                f"could not generate. Reducing prompt size and retrying. Conversation is currently {len(self.conversation)} messages long and prompt size is {len(prompt)} characters long. This is retry #{retries}"
            )
            self.conversations[conversation_id]["conversation"] = self.conversation[2:]
            if retries < 3:
                await asyncio.sleep(retries * 1000)
                self.retries[conversation_id] = retries + 1
                # Note: We're returning None here as we'll retry with on_message
                return None

            logging.info("max retries reached. Giving up.")
            self.retries[conversation_id] = 0
            await message.channel.send(
                "`Something went wrong, please contact an administrator or try again`"
            )
            return None

    def _generate_ai_response(
        self, prompt: str = "", author="", model="meta/meta-llama-3-70b-instruct"
    ) -> str:
        """Generates AI-powered responses using the Replicate API with the specified model"""

        if debug_prompts:
            logging.info(f"\n=== PROMPT START ===\n{prompt}\n=== PROMPT END ===\n")

        output = replicate.run(
            model,
            input={
                "debug": debug_prompts,  # Also pass debug state to model
                "top_k": 50,
                "top_p": 1,
                "prompt": prompt,
                "temperature": 0.7,
                "system_prompt": INITIAL_PROMPT,
                "max_new_tokens": 250,
                "min_new_tokens": -1,
            },
        )
        response = "".join(output)
        return response

    async def _handle_conversation_reply(
        self, message, reply, author, conversation_id, prompt=""
    ):
        """Save the conversation history"""
        # If it returns an empty reply, clear memory and provide default response
        if not reply:
            reply = "I don't know what to say"
            logging.info("clearing self.conversation")
            self.conversation = []
            self.conversations[conversation_id]["conversation"] = self.conversation
            return await message.channel.send(reply)

        # Log the conversation
        logging.info(f"sending reply: '{reply}' \n and logging into conversation")
        self.conversation.append(f"{author}: {message.content}")
        self.conversation.append(f"faebot-dev: {reply}")
        self.conversations[conversation_id]["conversation"] = self.conversation

        logging.info(
            f"conversation is currently {len(self.conversation)} messages long and the prompt is {len(prompt)}. There are {len(self.conversations[conversation_id]['conversants'])} conversants."
            f"\nthere are currently {len(self.conversations.items())} conversations in memory"
        )

        return reply

    async def _should_respond_to_message(self, message, conversation_id):
        """Determine if the bot should respond based on specified criteria"""
        content = message.content.strip().lower()

        # Get reply frequency from conversation settings
        reply_frequency = self.conversations[conversation_id].get(
            "reply_frequency", 0.2
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

    @admin_command("conversations")
    async def _list_conversations(
        self, message, message_tokens=None, conversation_id=None
    ):
        """List all conversations in memory"""
        if len(self.conversations) == 0:
            logging.info("asked to list conversations but there are none")
            return await message.channel.send("there are no conversations in memory")
        # Create a formatted string of conversations
        reply = "here are the conversations I have in memory:\n"
        for x in self.conversations.keys():
            reply = (
                reply
                + str(self.conversations[x]["id"])
                + " - "
                + str(self.conversations[x]["name"])
                + " - "
                + str(len(self.conversations[x]["conversation"]))
                + "\n"
            )
        return await message.channel.send(reply)

    @admin_command("invite")
    async def _invite_conversation(
        self, message, message_tokens=None, conversation_id=None
    ):
        """initialises a conversation in a non dm channel"""
        return await self._initialize_conversation(
            message, message_tokens=message_tokens, conversation_id=conversation_id
        )

    @admin_command("forget")
    async def _forget_conversation(self, message, message_tokens, conversation_id):
        """Forget a conversation by clearing its memory"""
        # Check if there are conversations to forget
        if len(self.conversations) == 0:
            logging.info(
                f"asked to clear memory, but there are no conversations. Message was {message.content}"
            )
            return await message.channel.send("there are no conversations to forget")

        # Determine which conversation to forget
        to_forget = None

        # If no conversation ID provided, use current
        if len(message_tokens) < 2:
            to_forget = conversation_id
            logging.info(
                f"asked to forget without providing a conversation id, using current conversation {conversation_id}"
            )
        # If ID provided, validate it
        else:
            provided_id = message_tokens[1]
            if provided_id in self.conversations:
                to_forget = provided_id
            else:
                logging.info(
                    f"asked to forget conversation {provided_id}, but it does not exist. Message was {message.content}"
                )
                return await message.channel.send(
                    f"Conversation ID '{provided_id}' does not exist. Please provide a valid conversation ID."
                )

        # Clear the conversation from memory
        self.conversation = []
        self.conversations[to_forget]["conversation"] = self.conversation

        logging.info(f"clearing memory from admin prompt in conversation {to_forget}")
        return await message.channel.send(f"cleared conversation {to_forget}")

    @admin_command("help")
    async def _admin_help(self, message, message_tokens=None, conversation_id=None):
        """Show available admin commands"""
        commands = list(admin_commands.keys())
        help_text = "Available admin commands:\n"
        for cmd in commands:
            doc = admin_commands[cmd].__doc__ or "No description"
            help_text += f"- `{cmd}`: {doc}\n"
        return await message.channel.send(help_text)

    @admin_command("model")
    async def _set_or_return_model(
        self, message, message_tokens=None, conversation_id=None
    ):
        """sets the model to use for generating responses or returns the current model name"""
        if len(message_tokens) > 1:
            new_model = message_tokens[1]
            logging.info(f"Changing model from {self.model} to {new_model}")
            self.model = new_model
            return await message.channel.send(f"Model changed to: {self.model}")
        else:
            logging.info(f"Current model is {self.model}")
            return await message.channel.send(f"Current model is: {self.model}")


# intents for the discordbot
intents = discord.Intents.default()
intents.message_content = True

# instantiate and run the bot
client = Faebot(intents=intents)
client.run(os.getenv("DISCORD_TOKEN", ""))
