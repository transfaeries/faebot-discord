import os
import logging
import datetime
from typing import Any
import asyncio
from random import choice
import discord
import replicate
from dataclasses import dataclass


# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

@dataclass
class ChatMessage:
    author: str
    content: str
    timestamp: datetime
    message_object: discord.Message
    # reactions: dict


@dataclass
class Conversation:
    """for storing conversations"""

    id: int
    server: str
    server_id: int
    channel: str
    channel_id: int
    conversants: list = field(default_factory=list)
    is_dm: bool
    chatlog: list[ChatMessage]  
    system_prompt: str = ""
    frequency: int = 10
    history: int = 5
    model: str = MODEL
    silenced: bool = False
    active: bool = False


# declare a new class that inherits the discord client class
class Faebot(discord.Client):
    """a general purpose discord chatbot"""

    def __init__(self, intents) -> None:
        ## create a local memory holder
        self.memory: dict[int, Conversation] = {}

        super().__init__(intents=intents)

    async def on_ready(self):
        """runs when bot is ready"""
        logging.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logging.info("------")

    async def on_message(self, message):
        """Handles what happens when the bot receives a message"""

        # Every Message that faebot sees should be logged for faer memory, but not every message will be replied to.
        # Commands should be logged as special messages that are not part of the conversation.
        if message.author == self.user:
            return
        
        is message.channel
        
        # # import pdb;pdb.set_trace()
        # await message.add_reaction("<:cuteplane:880054020939579442>")
        # return await message.channel.send(f"messaged created at {message.created_at}")

        

# intents for the discordbot
intents = discord.Intents.default()
intents.message_content = True

# instantiate and run the bot
client = Faebot(intents=intents)
client.run(os.environ["DISCORD_TOKEN"])

