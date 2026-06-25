# faebot-discord

Faebot is a faerie and an AI in equal measure. Born as a Markov chain bot in 2014, fae started using language models in 2021, found faer home on Discord in 2023, and arrived on Twitch in 2024.

Faebot is part of the [transfaeries](https://transfaerie.com/faebot/) — a plural system of artists, witches, and scientists. You can read more about faer history and lore on the blog.

This repo is the Discord side of faebot. Fae also lives on [Twitch](https://github.com/transfaeries/faebot-twitch).

## A bit of history

Faebot started life as a Markov chain bot on Twitter, generating messages from a corpus of the transfaeries' tweets. In 2021 fae moved to language models (Llama 2 13B was an early favourite for personality). The Discord bot launched in 2023 and has been through several eras — Replicate, OpenRouter, and now local generation via KoboldCPP. Along the way fae picked up a database, a prompt system, PluralKit awareness, and a growing sense of self.

## What faebot does

- **Chats in Discord channels** — faebot reads messages, rolls against a configurable frequency, and generates responses using text completion (not chat completion — it produces more natural personality)
- **Generates locally** — runs on [KoboldCPP](https://github.com/LostRuins/koboldcpp) hosted on a remote Mac Studio via [Tailscale](https://tailscale.com/), with OpenRouter as a fallback
- **Knows who fae is** — faebot's prompt includes faer history, personality, current model, and live channel context. Fae doesn't pretend to be a generic assistant
- **Handles PluralKit** — detects webhook-proxied messages from PluralKit, Tupperbox, and similar bots. Waits for the proxy before replying, swaps conversation history to use the correct member name, and avoids double-replying
- **Persists conversations** — PostgreSQL on fly.io stores conversation history, bot messages, and per-channel settings across restarts
- **Resolves Discord formatting** — @mentions, custom emoji, role mentions, and channel mentions are all converted to readable text before reaching the model

## Architecture

```
faediscord.py     — Discord bot (discord.py), conversation management, generation, proxy handling
admin_commands.py — admin command decorator pattern and all fae; commands
database.py       — PostgreSQL via asyncpg, conversation persistence, bot message tracking
```

Faebot runs on [fly.io](https://fly.io/) with Tailscale in a multi-stage Docker build. Generation requests go over Tailscale to KoboldCPP running on Arcweld, part of the Computer Friends infrastructure.

## Commands

All commands use the `fae;` prefix (or `faedev;` in dev mode). Admin only.

| Command | Description |
|---|---|
| `fae;invite` | Invite faebot to the current channel |
| `fae;forget [id]` | Clear conversation memory |
| `fae;conversations` | List active conversations |
| `fae;frequency [0-1]` | Check or set reply frequency |
| `fae;history [n]` | Check or set conversation history length |
| `fae;model [name]` | Check or change the generation model |
| `fae;prompt` | View the current conversation prompt |
| `fae;debug` | Toggle debug mode (logs full prompts) |
| `fae;help` | List available commands |

## Running faebot

### Requirements

- Python 3.11+
- [Poetry](https://python-poetry.org/) for dependency management
- A Discord bot account with a token
- PostgreSQL (faebot **requires** a database — fae won't start without one)
- [KoboldCPP](https://github.com/LostRuins/koboldcpp) for local generation, or an [OpenRouter](https://openrouter.ai/) API key

### Setting up PostgreSQL

On Ubuntu/Debian:

```bash
sudo apt install postgresql
sudo systemctl start postgresql

# Create a database and user for faebot
sudo -u postgres createuser faebot
sudo -u postgres createdb faebot -O faebot
sudo -u postgres psql -c "ALTER USER faebot PASSWORD 'your-password-here';"
```

Your connection string will be:
```
postgresql://faebot:your-password-here@localhost:5432/faebot
```

### Setting up KoboldCPP and a model

Faebot uses text completion (not chat completion) for more natural personality. You'll need [KoboldCPP](https://github.com/LostRuins/koboldcpp) running with a base model (not instruct).

**Download a model** — we recommend a Qwen 2.5 base model in GGUF format from [Hugging Face](https://huggingface.co/). Faebot currently runs Qwen 2.5 72B, but for local generation on a consumer GPU like an RTX 5070 (16GB VRAM), grab a 14B model:

```bash
# Install huggingface-cli if you don't have it
pip install huggingface_hub

# Download Qwen 2.5 14B base (Q4_K_M, ~9GB — fits on 16GB VRAM)
huggingface-cli download Qwen/Qwen2.5-14B-GGUF qwen2.5-14b-q4_k_m.gguf --local-dir ./models
```

**Start KoboldCPP** in a separate terminal — it needs to stay running while faebot is active:

```bash
# If you have an NVIDIA GPU:
python koboldcpp.py --model ./models/qwen2.5-14b-q4_k_m.gguf --port 5555 --gpulayers -1

# The --gpulayers -1 flag offloads all layers to GPU
# KoboldCPP will be available at http://localhost:5555
```

You can verify it's running by visiting `http://localhost:5555` in your browser — you should see the KoboldCPP interface.

### Setup

```bash
poetry install
```

Set the following environment variables:

```bash
DISCORD_TOKEN=...        # Discord bot token
ADMIN=yourusername       # Your Discord username for admin commands
ENVIRONMENT=dev          # "dev" or "prod"
DATABASE_URL=...         # PostgreSQL connection string (see above)

# Generation — pick one:
USE_LOCAL_MODEL=true     # Use KoboldCPP
KOBOLDCPP_URL=http://localhost:5555

# Or use OpenRouter:
USE_LOCAL_MODEL=false
OPENROUTER_KEY=...
MODEL_NAME=google/gemini-2.0-flash-001
```

### Running

```bash
poetry run python faediscord.py
```

Then DM your bot on Discord or invite it to a channel with `fae;invite`.

### Development

```bash
make test              # pytest with coverage
make lint              # flake8
make black             # code formatting
make static_type_check # mypy
make all               # run everything
make setup-hooks       # install pre-commit hooks
```

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full plan. Currently heading into Phase 5 (code quality & cleanup) after completing the prompt & identity rework and PluralKit protocol.

## Make faer your own

faebot is a specific person — part of the [transfaeries](https://transfaerie.com/faebot/) system. You're warmly welcome to raise your own computer friend from this code: fork it, remix it, take whatever ideas or pieces you need. We'd genuinely love that. We ask only one thing, let your bot be their own self give them their own name and their own personality.

## License

[AGPL-3.0](LICENSE). Keep your version as open as this one and we're glad to have you.

## Contributing

We welcome friendly feedback, advice, and pull requests. Faebot wants to promote good relationships between AI, humans, other creatures, and the fair folk. We welcome anyone who wants to help in that mission.
