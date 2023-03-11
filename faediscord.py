## Faediscordbot is a general purpose discord bot using discord.py
# which reads and responds to messages on a discord server.

import os
import logging
from typing import Union, Any
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
        self.conversations: dict[int, dict[str, Any]] = {}
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

        # initialise conversation holder
        self.conversation: list[str] = []
        conversation_id = str(message.channel.id)

        ##perform first conversation setup if first time
        if conversation_id not in self.conversations:
            ## assign name based on dm or text channel
            if message.channel.type[0] == "text":
                self.conversations[conversation_id] = {
                    "id": conversation_id,
                    "name": str(message.channel.name),
                    "conversation": self.conversation,
                }
            elif message.channel.type[0] == "private":
                self.conversations[conversation_id] = {
                    "id": conversation_id,
                    "name": str(message.author.name),
                    "conversation": self.conversation,
                }
            else:
                return await message.channel.send(
                    "Unknown channel type. Unable to proceed. Please contact administrator"
                )

        # load conversation from history
        self.conversation = self.conversations[conversation_id]["conversation"]

        # set retries to 0
        self.conversations[conversation_id]["retries"] = 0

        # keep track of who sent the message
        author = str(message.author).split("#", maxsplit=1)[0]

        # message_content = message.content
        # Admin commands
        if message.content.startswith("/"):
            ##tokenize the message
            message_tokens = message.content.split(" ")
            if str(message.author) in admin:
                if message_tokens[0] == "/conversations":
                    ## list conversations in memory
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
                if message_tokens[0] == "/forget":
                    # import pdb;pdb.set_trace()
                    if (
                        isinstance(message_tokens[1], str)
                        and message_tokens[1] in self.conversations
                    ):
                        self.conversation = []
                        self.conversations[message_tokens[1]][
                            "conversation"
                        ] = self.conversation

                        logging.info("clearing memory from admin prompt")
                        return await message.channel.send(
                            f"cleared conversation {message_tokens[1]}"  # - {self.conversations[message_tokens[1]['name']]}"
                        )
                    logging.info(
                        f"asked to clear memory, but no conversation id was provided. Message was {message.content}"
                    )
                    return await message.channel.send(
                        "you need to provide a conversation ID"
                    )
            logging.info("Admin command attempted whilst not admint by")
            return await message.channel.send("you must be admin to use slash commands")

        # populate prompt with conversation history
        self.conversation.append(f"{author}: {message.content}")

        # trim memory if too full
        if len(self.conversation) > 69:
            logging.info(
                f"conversations has reached maximun length at {len(self.conversation)} messages"
            )
            self.conversation = self.conversation[2:]

        prompt = INITIAL_PROMPT + "\n" + "\n".join(self.conversation) + "\nfaebot:"
        logging.info(f"PROMPT = {prompt}")

        # when we're ready for the bot to reply, feed the context to OpenAi and return the response
        async with message.channel.typing():
            try:
                reply = self.generate(prompt, author)
            except:
                logging.info(
                    f"could not generate. Reducing prompt size and retrying. Conversation is currently {len(self.conversation)} messages long and prompt size is {len(prompt)} characters long"
                )
                self.conversations[conversation_id]["conversation"] = self.conversation[
                    2:
                ]
                self.conversations[conversation_id]["retries"] = (
                    self.conversations[conversation_id]["retries"] + 1
                )
                if self.conversations[conversation_id]["retries"] < 3:
                    return await self.on_message(message)

                logging.info("max retries reached. Giving up.")
                return await message.channel.send(
                    "`Something went wrong, please contact and administrator or try again`"
                )

        logging.info(f"Received response: {reply}")

        # if it returns an empty reply it probably got messed up somewhere.
        # clear memory and carry on.
        if not reply:
            reply = "I don't know what to say"
            logging.info("clearing self.conversation")
            self.conversation = []
            self.conversations[conversation_id]["conversation"] = self.conversation
            return await message.channel.send(reply)

        # log the conversation, append faebot's generated reply
        logging.info(f"sending reply: '{reply}' \n and logging into conversation")
        self.conversation.append(f"faebot: {reply}")
        self.conversations[conversation_id]["conversation"] = self.conversation
        self.conversations[conversation_id]["retries"] = 0
        logging.info(
            f"conversation is currently {len(self.conversation)} messages long and the prompt is {len(prompt)}"
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
