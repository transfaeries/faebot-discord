## Faediscordbot is a general purpose discord bot using discord.py
# which reads and responds to messages on a discord server.

import os
import logging
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
model = os.getenv("MODEL_NAME", "curie")
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
        self.conversation: list[str] = []
        # self.conversants: dict[str,int] = {}
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

        # keep track of who sent the message
        author = str(message.author).split("#", maxsplit=1)[0]

        # admin override memory clear
        if author in admin and message.content.startswith("/forget"):
            self.conversation = []
            logging.info("clearing memory from admin prompt")
            return

        # populate prompt with conversation history
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
        logging.info(
            f"conversation is currently {len(self.conversation)} messages long"
        )

        # sends faebot's message with faer pattented quirk
        reply = f"```{reply}```"
        return await message.channel.send(reply)

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
