## Faediscordbot is a general purpose discord bot using discord.py
# which reads and responds to messages on a discord server.

import os
import logging
from typing import Any
import asyncio
from random import choice
import discord
import replicate

# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


model = os.getenv("MODEL_NAME", "meta/llama-2-70b-chat")
admin = os.getenv("ADMIN", "")




# declare a new class that inherits the discord client class
class Faebot(discord.Client):
    """a general purpose discord chatbot"""

    def __init__(self, intents) -> None:

        # initialise the system prompt from file
        self.system_prompt = ""
        with open("promptsdev.txt", "r", encoding="utf-8") as promptfile:
            self.system_prompt = promptfile.read()

        # initialise conversation logging
        self.conversations: dict[str, dict[str, Any]] = {}
        self.retries: dict[str, int] = {}
        logging.info(f"models available : {str(model)}")
        self.models = model.split(",")
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
            self.conversations[conversation_id] = {
                "id": conversation_id,
                "conversation": self.conversation,
                "conversants": [],
            }
            ## assign name based on dm or text channel
            if message.channel.type[0] == "text":
                self.conversations[conversation_id]["name"] = str(message.channel.name)
            elif message.channel.type[0] == "private":
                self.conversations[conversation_id]["name"] = str(message.author.name)
            else:
                return await message.channel.send(
                    "Unknown channel type. Unable to proceed. Please contact administrator"
                )

        # load conversation from history
        self.conversation = self.conversations[conversation_id]["conversation"]

        # keep track of who sends messages
        author = message.author.name
        if author not in self.conversations[conversation_id]["conversants"]:
            self.conversations[conversation_id]["conversants"].append(author)

        ###### Admin commands ##########

        if message.content.startswith("/"):
            ##tokenize the message
            message_tokens = message.content.split(" ")
            if message.author.name in admin:
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
                    # check if conversation id was provided
                    if len(message_tokens) < 2:
                        to_forget = conversation_id
                        logging.info(
                            f"asked to forget without providing a conversation id, using current conversation {conversation_id}"
                        )
                    # check if provided conversation id is valid
                    elif (
                        isinstance(message_tokens[1], str)
                        and message_tokens[1] in self.conversations
                    ):
                        to_forget = str(message_tokens[1,:])

                    else:
                        logging.info(
                            f"asked to clear memory, but no conversation id was provided. Message was {message.content}"
                        )
                        return await message.channel.send(
                            "you need to provide a conversation ID"
                        )

                    # clear conversation
                    self.conversation = []
                    self.conversations[to_forget]["conversation"] = self.conversation

                    logging.info(
                        f"clearing memory from admin prompt in conversation {to_forget}"
                    )
                    return await message.channel.send(
                        f"cleared conversation {to_forget}"  # - {self.conversations[message_tokens[1]['name']]}"
                    )
                if message_tokens[0]=="/prompt":
                    #allows live editing of the system prompt
                    self.system_prompt = ' '.join(message_tokens[1:])
                    logging.info(f"system prompt updated to: \n{self.system_prompt}")
                    return await message.channel.send(
                        "system prompt edited"
                    )
                else:
                    logging.info(f"command not known {message.content}")
                    return await message.channel.send(
                        f"failed to recognise command {message.content}"
                    )

            logging.info(
                f"Admin command attempted whilst not admin by {message.author.name}"
            )
            return await message.channel.send("you must be admin to use slash commands")

        # trim memory if too full
        if len(self.conversation) > 20:
            logging.info(
                f"conversations has reached maximun length at {len(self.conversation)} messages. Removing the oldest two messages."
            )
            self.conversation = self.conversation[2:]

        # populate prompt with conversation history

        prompt = (
            "\n".join(self.conversation)
            + f"\n{author}: {message.content}"
            + "\nfaebot:"
        )
        # if isinstance(self.models, list):
        self.model = choice(self.models)
        logging.info(f"picked model : {self.model}")

        # when we're ready for the bot to reply, feed the context to OpenAi and return the response
        async with message.channel.typing():
            retries = self.retries.get(conversation_id, 0)
            try:
                reply = await self.generate(prompt, author, self.model)
                self.retries[conversation_id] = 0
            except:
                logging.info(
                    f"could not generate. Reducing prompt size and retrying. Conversation is currently {len(self.conversation)} messages long and prompt size is {len(prompt)} characters long. This is retry #{retries}"
                )
                self.conversations[conversation_id]["conversation"] = self.conversation[
                    2:
                ]
                if retries < 3:
                    await asyncio.sleep(retries * 1000)
                    self.retries[conversation_id] = retries + 1
                    return await self.on_message(message)

                logging.info("max retries reached. Giving up.")
                self.retries[conversation_id] = 0
                return await message.channel.send(
                    "`Something went wrong, please contact an administrator or try again`"
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
        self.conversation.append(f"{author}: {message.content}")
        self.conversation.append(f"faebot: {reply}")
        self.conversations[conversation_id]["conversation"] = self.conversation
        logging.info(
            f"conversation is currently {len(self.conversation)} messages long and the prompt is {len(prompt)}. There are {len(self.conversations[conversation_id]['conversants'])} conversants."
            f"\nthere are currently {len(self.conversations.items())} conversations in memory"
        )

        # # uncomment to enable full conversation logging
        for line in self.conversation:
            logging.info(line)

        # sends faebot's message with faer pattented quirk
        reply = f"```{reply}```"
        return await message.channel.send(reply)

    # async def

    # async def

    async def generate(
        self, prompt: str = "", author="", model="meta/llama-2-70b-chat"
    ) -> str:
        """generates completions with the OpenAI api"""

        output = replicate.run(
            model,
            input={
                "debug": False,
                "top_k": 50,
                "top_p": 1,
                "prompt": prompt,
                "temperature": 0.7,
                "system_prompt": self.system_prompt,
                "max_new_tokens": 250,
                "min_new_tokens": -1,
            },
        )
        response = "".join(output)
        return response

        # response = client.Completion.create(  # type: ignore
        #     engine=engine,
        #     prompt=prompt,
        #     temperature=0.7,
        #     max_tokens=512,
        #     top_p=1,
        #     frequency_penalty=0.99,
        #     presence_penalty=0.3,
        #     stop=["\n", author + ":", "faebot:"],
        # )
        # return response["choices"][0]["text"].strip()


# intents for the discordbot
intents = discord.Intents.default()
intents.message_content = True

# instantiate and run the bot
client = Faebot(intents=intents)
client.run(os.environ["DISCORD_TOKEN"])
