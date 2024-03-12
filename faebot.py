from functools import wraps
import os
import logging
import datetime
from typing import Any
import asyncio
from random import choice
import discord
from discord.ext import commands
import replicate
from dataclasses import dataclass, field

ADMIN = os.getenv("ADMIN", "")
ADMIN_LIST = [int(x) for x in ADMIN.split(",")]


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
    reactions: dict
    params: dict 


@dataclass
class Conversation:
    """for storing conversations"""

    id: int
    server: str
    server_id: int
    channel: str
    channel_id: int
    is_dm: bool
    channel_admins: list[int] = field(default_factory=list)
    chatlog: list[ChatMessage] = field(default_factory=list)
    conversants: list = field(default_factory=list) 
    system_prompt: str = ""
    frequency: int = 10
    history: int = 20
    model: str = "meta/llama-13b-chat"
    silenced: bool = False
    active: bool = True


# declare a new class that inherits the discord client class
class Faebot(commands.Bot):
    """a general purpose discord chatbot"""

    def __init__(self, *args, **kwargs) -> None:
        ## create a local memory holder
        self.conversations: dict[int, Conversation] = {}

        super().__init__(*args, **kwargs)

    async def on_ready(self):
        """runs when bot is ready"""
        logging.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logging.info("------")

    async def on_message(self, message: discord.Message):
        """Handles what happens when the bot receives a message"""

        # handle commands 
        await super().on_message(message)

        # don't reply to ourselves
        if message.author == self.user:
            return
        

        if message.channel.id in self.conversations.keys():
            if self.conversations[message.channel.id].active:
                return await self.process_message(message)
            return
        
        if message.channel.type.name == "private":
            return await self.initiate_dm(message)
        return
    
    async def process_message(self,message: discord.Message):
        logging.info(f"processing message {message.id}")
        pass

    async def initiate_dm(self, message:discord.Message):
        logging.info(f"processing dm {message.channel.id}")
        # import pdb;pdb.set_trace()
        self.conversations[message.channel.id] = Conversation(
            id= message.channel.id,
            server=message.author.global_name,
            server_id=message.author.id,
            channel=message.author.global_name,
            channel_id=message.channel.id,
            is_dm=True,
            channel_admins=[message.author.id],
            frequency=1
        )
        logging.info(f"added conversation: {self.conversations[message.channel.id]}")
        return await self.process_message(message)    

        

# set up bot 
description = "A bot who is also a faerie"
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
faebot = Faebot(command_prefix="fae;",description=description, intents=intents)

## commands for everyone:

@faebot.command()
async def hello(ctx: commands.Context):
    """Displays default help text"""
    return await ctx.send("Hello, my name is faebot, I'm an AI chatbot developed by the transfaeries. I'll chime in on the chat and reply every so often, and I'll always reply to messages with my name on them. For mod commands use 'fb;mods'")
    # await ctx.send("Hello, my name is faebot, I'm an AI chatbot developed by the transfaeries. I'll chime in on the chat and reply every so often, and I'll always reply to messages with my name on them. For mod commands use 'fb;mods'")

##commands for admins
def requires_admin(command: commands.command) -> commands.command:
    @wraps(command)
    async def admin_command(ctx: commands.Context):
        if ctx.author.id in ADMIN_LIST:
            return await command(ctx)
        return await ctx.reply("you must be an admin to use this command")

    return admin_command


@faebot.command()
@requires_admin
async def join(ctx: commands.Context):
    return await ctx.send("valid use of join")


faebot.run(os.environ["DISCORD_TOKEN"])

