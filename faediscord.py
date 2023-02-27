## Faediscordbot is a general purpose discord bot using discord.py which reads and responds to messages on a discord server.

import discord
import random
import os
import logging
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

# initialise the prompt
initialprompt = ""
with open("prompts.txt") as promptfile:
    initialprompt = promptfile.read()


class Faebot(discord.Client):
    def __init__(self, intents) -> None:
        self.conversation: list[str] = []
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

        author = str(message.author).split("#")[0]

        if author == "transfaeries" and message.content.startswith("/forget"):
            self.conversation = []
            logging.info("clearing memory from admin prompt")
            return

        # when we're ready for the bot to reply, feed the context to OpenAi and return the response

        self.conversation.append(f"{author}: {message.content}")

        prompt = initialprompt + "\n" + "\n".join(self.conversation) + "\nfaebot:"
        logging.info(f"PROMPT = {prompt}")

        async with message.channel.typing():
            reply = self.generate(prompt, author)
        logging.info(f"Received response: {reply}")
        if not reply:
            reply = "I don't know what to say"
            logging.info("clearing self.conversation")
            self.conversation = []

        logging.info(f"sending reply: {reply} and logging into conversation")
        self.conversation.append(f"faebot: {reply}")
        logging.info(
            f"conversation is currently {len(self.conversation)} messages long"
        )

        reply = f"```{reply}```"
        return await message.channel.send(reply)

    def generate(self, prompt: str = "", author: str = "") -> str:
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

client = Faebot(intents=intents)

client.run(os.environ["DISCORD_TOKEN"])
