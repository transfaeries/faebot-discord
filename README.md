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

0.0.6 - Runs on Openrouter and has editable parameters


## Run your own faebot

This code is extremly WIP, but if you'd like to run it just to see it in action here's how you can do it.

We use [poetry](https://python-poetry.org/) for dependency management. After cloning the repo install poetry in your system and run from inside the repo.
```bash
poetry install
```

You'll need to set the following secrets as environment variables:
```bash
OPENROUTER_KEY=XXXXX... # key for openrouter, you can get one at https://openrouter.ai/
MODEL_NAME="google/gemini-2.0-flash-001" #default starting model. can be changed later.
DISCORD_TOKEN=XXXXX... # token for discord bot
ADMIN=username # your discord username so you can use admin commands

Once all that's set you can run the bot with:
```bash
poetry run faediscord.py
```

Then DM your bot on discord or add them to a server.

## Development

We provide a Makefile to simplify common development tasks:

### Running Tests
```bash
make test
```
This runs the test suite with pytest and generates a coverage report.

### Code Quality
```bash
make lint        # Run flake8 linter
make black       # Format code with black
make static_type_check  # Run mypy type checking
```

Or run all quality checks at once:
```bash
make all
```

### Git Hooks
Set up pre-commit hooks to automatically run formatting and linting before each commit:
```bash
make setup-hooks
```

This will configure git to use our pre-commit hooks, which run:
- Black code formatting
- Flake8 linting
- Mypy static type checking

### Cleanup
Remove cache files and directories:
```bash
make clean
```

## Contributing 

There are some open issues, most notably there's a bug: 

https://github.com/transfaeries/faebot-discord/issues/4

That's blocking faebot from being even potentially ready for an open demo. We will add more issues as we think of them. We welcome friendly feedback and advice and of course pull requests. 

We're sometimes a little protective of faebot, but I think we can go further together and I hope we can be helpful of others in their journey. Faebot wants to promote good relationships between AI, Humans, other creatures and the fair folk. We welcome anyone who wants to help in that mission.
