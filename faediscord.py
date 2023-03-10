## Faediscordbot is a general purpose discord bot using discord.py
# which reads and responds to messages on a discord server.

import os
import logging
from typing import Union
import discord
import openai

# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

# load secrets
openai.api_key = os.getenv("OPENAI_API_KEY", "")
model = os.getenv("MODEL_NAME", "davinci")
admin = os.getenv("ADMIN", "")

# initialise the prompt from file
INITIAL_PROMPT = ""
with open("prompts.txt", "r", encoding="utf-8") as promptfile:
    INITIAL_PROMPT = promptfile.read()


# declare a new class that inherits the discord client class
class Faebot(discord.Client):
    """a general purpose discord chatbot"""

    def __init__(self, intents) -> None:
        # initialise conversation logging
        self.conversations: dict[int,dict[str,Union[str,list[str]]]] = {} 
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


        #initialise conversation holder
        self.conversation: list[str] =[]

        ##perform first conversation setup if first time
        if message.channel.id not in self.conversations:
            ## assign name based on dm or text channel
            if message.channel.type[0]=="text":
                self.conversations[message.channel.id]={"id":message.channel.id, "name":message.channel.name, "conversation":self.conversation}
            elif message.channel.type[0]=="private":
                self.conversations[message.channel.id]={"id":message.channel.id, "name":message.author.name, "conversation":self.conversation}
            else:
                return await message.channel.send ("Unknown channel type. Unable to proceed. Please contact administrator")
        
        # import pdb;pdb.set_trace()
        # load conversation from history
        self.conversation = self.conversations[message.channel.id]["conversation"]


        # keep track of who sent the message
        author = str(message.author).split("#", maxsplit=1)[0]

        # # admin override memory clear
        message_content = message.content
        if message_content.startswith("/"):
            if str(message.author) in admin:
                if message_content.startswith("/conversations"):
                    reply = "here are the conversations I have in memory:\n"
                    for x in self.conversations.keys():
                        reply = reply + str(self.conversations[x]["id"]) + " - "+ str(self.conversations[x]["name"]) + "\n"
                    return await message.channel.send(reply)
                self.conversation = []
                logging.info("clearing memory from admin prompt")
                return
            logging.info("not admin")
            return await message.channel.send("not admin")

        # populate prompt with conversation history
        # import pdb;pdb.set_trace()
        self.conversation.append(f"{author}: {message.content}")
        prompt = INITIAL_PROMPT + "\n" + "\n".join(self.conversation) + "\nfaebot:"
        logging.info(f"PROMPT = {prompt}")

        # self.conversants[author+":"] = 1
        # conversant_names = [x for x in self.conversants.keys()]

        # when we're ready for the bot to reply, feed the context to OpenAi and return the response
        async with message.channel.typing():
            reply = self.generate(prompt, author)
        logging.info(f"Received response: {reply}")

        # if it returns an empty reply it probably got messed up somewhere.
        # clear memory and carry on.
        if not reply:
            reply = "I don't know what to say"
            logging.info("clearing self.conversation")
            self.conversation = []

        # log the conversation, append faebot's generated reply
        logging.info(f"sending reply: {reply} and logging into conversation")
        self.conversation.append(f"faebot: {reply}")
        self.conversations[message.channel.id]["conversation"] = self.conversation
        logging.info(
            f"conversation is currently {len(self.conversation)} messages long"
            f"\nthere are currently {len(self.conversations.items())} conversations in memory"
        )

        # sends faebot's message with faer pattented quirk
        reply = f"```{reply}```"
        return await message.channel.send(reply)

    # async def 

    def generate(self, prompt: str = "", author="") -> str:
        """generates completions with the OpenAI api"""
        response = openai.Completion.create(  # type: ignore
            engine=model,
            prompt=prompt,
            temperature=0.7,
            max_tokens=512,
            top_p=1,
            frequency_penalty=0.99,
            presence_penalty=0.3,
            stop=["\n\n", author + ":", "faebot:"],
        )
        return response["choices"][0]["text"].strip()


# intents for the discordbot
intents = discord.Intents.default()
intents.message_content = True

# instantiate and run the bot
client = Faebot(intents=intents)
client.run(os.environ["DISCORD_TOKEN"])
