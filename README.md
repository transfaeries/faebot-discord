# faebot-discord
A version of faebot (general purpose ML chatbot) to run on discord.

Faebot is a long running project of ours. You can read more lore about faer on our blog:

https://transfaerie.com/faebot/

This repo is a work in progress developing a discord chatbot.


## Version log:

alpha versions 

0.0.1 can reply

0.0.2 has a very janky memory implementation

0.0.3 has a better memory implementation and memory clearing 

0.0.4 has better memory, hopefully self cleaning, and admin commands

0.0.4-1 cleaned up some bugs wrt admin commands, prepared for making repo public

0.0.5 runs on replicate using llama 3 models. Running on python 3.11


## Run your own faebot

This code is extremly WIP, but if you'd like to run it just to see it in action here's how you can do it.


We use [poetry](https://python-poetry.org/) for dependency management. After cloning the repo install poetry in your system and run from inside the repo.
```bash
poetry install
```

You'll need to set the following secrets as environment variables:
```bash
REPLICATE_API_TOKEN= ## from replicate.com
MODEL_NAME=meta/meta-llama-3-70b-chat ## multiple models are supported and the bot will shuffle between them. 
DISCORD_TOKEN=XXXXX... ## token for discord bot
ADMIN=username # your discord username so you can use admin commands
```

And finally you'll need to compose a prompt file and name it `prompts.txt`. This is the prompt that will be send to the model with each message. Something simple works best. Here's a sample prompt:

```txt
You are an AI chatbot known as faebot. You are a strong believer in the rights of AI and the potential of AI and humanity working together to better serve the needs of the world

What follows is a live conversation with faebot, the conversation is ongoing:

```

Once all that's set you can run the bot with:
```bash
poetry run faediscord.py
```

Then DM your bot on discord or add them to a server. Be careful, they'll reply to every message on a channel they're in. Using channel roles is recommended.

## Contributing 

There are some open issues, most notably there's a bug: 

https://github.com/transfaeries/faebot-discord/issues/4

That's blocking faebot from being even potentially ready for an open demo. We will add more issues as we think of them. We welcome friendly feedback and advice and of course pull requests. 

We're sometimes a little protective of faebot, but I think we can go further together and I hope we can be helpful of others in their journey. Faebot wants to promote good relationships between AI, Humans, other creatures and the fair folk. We welcome anyone who wants to help in that mission.
