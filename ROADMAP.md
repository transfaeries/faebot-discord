# Faebot Discord — Roadmap

## Phase 1: Foundation (Done ✓)
- [x] Discord.py bot with conversation memory
- [x] Per-conversation settings (model, frequency, history length, prompt)
- [x] Admin commands with decorator pattern (`fae;` / `faedev;`)
- [x] Response logic: mentions, name detection, random frequency
- [x] Test suite with pytest + coverage

## Phase 2: Database & Persistence (Done ✓)
- [x] PostgreSQL on fly.io with asyncpg
- [x] Conversation persistence across restarts
- [x] Bot message tracking with reaction data
- [x] Retry logic with connection pool recreation
- [x] Simplified schema (JSONB metadata + history)
- [x] Database migrations

## Phase 3: Local Generation (Done ✓)
- [x] KoboldCPP integration (text completion API)
- [x] Tailscale networking from fly.io to arcweld
- [x] Mistral Small 3.1 24B base model
- [x] Multi-stage Docker build with Tailscale

## Phase 4: Prompt & Identity Rework (Done ✓)
- [x] Rework faebot's Discord prompt (port learnings from Twitch prompt work)
- [x] Use bot's display name in prompt instead of hardcoded "faebot"
- [x] Self-knowledge block — faebot can describe faerself, history, personality
- [x] Character document (parallel to faebot-history.md on Twitch)
- [x] Resolve @mentions in conversation history (replace `<@id>` with display names)
- [x] Resolve custom emoji in conversation history (replace `<:name:id>` with readable form)
- [x] Use display names (not usernames) in conversation history
- [x] Conversants stored as username→display_name dict for future memory/lookup
- [x] PluralKit protocol — handle proxied messages (double-message dedup, wait-before-reply)

## Phase 5: Code Quality & Cleanup
- [ ] Move aiohttp to production dependencies
- [ ] Remove unused replicate dependency
- [ ] Audit log levels (info → debug where appropriate)
- [ ] OpenRouter fallback when KoboldCPP is unreachable
- [ ] Graceful error messages when generation backend is down

## Phase 6: Architecture Refactor
- [ ] Extract core.py — conversation + generation logic with no platform deps
- [ ] Extract commands as clean mixin (already partially done with admin_commands.py)
- [ ] Align with Twitch bot refactor for eventual shared core
- [ ] Event queue for generation status

## Phase 6.5: Cross-Channel Awareness
- [ ] Store guild_id in conversation dict for server grouping
- [ ] At render time, include faebot's recent activity in sibling channels (same server)
- [ ] Use bot_messages table — pull last faebot response + context per sibling channel
- [ ] Stale conversations get a summary, active ones get recent messages
- [ ] Gives faebot something to say in quiet channels by drawing on other conversations

## Phase 7: Memory System (cross-project)
- [ ] Per-user memory in DB (regulars, interests, past interactions)
- [ ] Long-term persistent facts (channel history, faebot's own memories)
- [ ] Shared memory layer with Twitch bot via PostgreSQL
- [ ] Retrieval strategy: RAG vs summarization vs hybrid

## Phase 8: Custom Faebot Model (cross-project)
- [ ] Collect and curate training data from both platforms
- [ ] Fine-tune small base model (Mistral/Llama)
- [ ] Deploy via KoboldCPP for both Twitch and Discord

## Future Ideas
- Web dashboard (port from Twitch)
- Voice input for Discord voice channels
- Reaction-based feedback loop for generation quality
- Emoji tool — let faebot look up and use custom server emoji in responses
- Tagging tool — let faebot @mention users by looking up display names
