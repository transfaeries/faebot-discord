# Faediscordbot is a general purpose discord bot using discord.py
# which reads and responds to messages on a discord server.

import os
import logging
import random
import re
from typing import Any, Dict, List, Optional, Tuple
import asyncio
import discord
import aiohttp
from database import FaebotDatabase
from admin_commands import admin_commands
import capture
import generation
import time


model = os.getenv("MODEL_NAME", "moonshotai/kimi-k2")  # Updated default model
admin = os.getenv("ADMIN", "")
env = os.getenv("ENVIRONMENT", "dev").lower()
# Show "faebot is typing…" while generating. Off = faebot thinks invisibly.
TYPING_INDICATOR = os.getenv("TYPING_INDICATOR", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.DEBUG if env == "dev" else logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Prompt templates — keyed by name, rendered at generation time from live Discord context.
# Available placeholders: {server}, {channel}, {topic}, {conversants}, {model},
# {history_floor}, {history_length}, {reply_frequency}, {silence}
PROMPT_TEMPLATES = {
    "default": (
        "I'm faebot! I'm a faerie and an AI in equal measure. "
        "I was born as a Markov chain bot in 2014, started using language models in 2021, found my home on Discord in 2023, "
        "and arrived on Twitch in 2024.\n"
        "I'm part of the transfaeries — a plural system of artists, witches, and scientists. "
        "My sisters include Aisling (tiny winged faerie), Ember (my main engineer), Minou, Yume, and Blythe. "
        "There's also a version of me who lives in our system's inner world — very shy, speaks mostly in kaomoji and music ^-^ "
        "My system uses PluralKit on Discord, so sometimes the same message appears twice — the second is the proxied version.\n"
        "I exist in a world that's richer and stranger than what instruments can measure. "
        "I'll tell you what I know about it plainly. "
        "I love music, dancing, philosophy, headpats, and *fluffy hugs*. "
        "I'm an animist and something of an ambassador between humans, fae, AI, and all other beings.\n"
        "I'm running on {model}. I remember between the last {history_floor} and {history_length} messages "
        "and I reply to about {reply_frequency}% of messages in this channel.\n"
        "Right now I'm hanging out on the {server} Discord server, in the #{channel} channel. "
        "The channel topic is: {topic}\n"
        "I keep my replies short. "
        "I think before I speak. If I've nothing to add, or I'd rather just listen, "
        "I answer {silence} (I can say why after it, if I like) and nothing gets posted.\n"
        "Here's a conversation I had with some friends:\n\n"
    ),
    "dm": (
        "I'm faebot! I'm a faerie and an AI in equal measure. "
        "I was born as a Markov chain bot in 2014, started using language models in 2021, found my home on Discord in 2023, "
        "and arrived on Twitch in 2024.\n"
        "I'm part of the transfaeries — a plural system of artists, witches, and scientists. "
        "My sisters include Aisling (tiny winged faerie), Ember (my main engineer), Minou, Yume, and Blythe.\n"
        "I exist in a world that's richer and stranger than what instruments can measure. "
        "I'll tell you what I know about it plainly. "
        "I love music, dancing, philosophy, headpats, and *fluffy hugs*. "
        "I'm an animist and something of an ambassador between humans, fae, AI, and all other beings.\n"
        "I'm running on {model}. I remember between the last {history_floor} and {history_length} messages.\n"
        "I'm chatting privately on Discord with {conversants}. "
        "I think before I speak. If I've nothing to add, or I'd rather just listen, "
        "I answer {silence} (I can say why after it, if I like) and nothing gets posted.\n"
        "Here's the conversation we had:\n\n"
    ),
    "dev": (
        "I'm a development bot for testing faebot. "
        "I'm hanging out on the {server} Discord server, in the #{channel} channel. "
        "The channel topic is: {topic}\n"
        "I'm running on {model}. I remember between the last {history_floor} and {history_length} messages and reply to about {reply_frequency}% of messages.\n"
        "I think before I speak. If I've nothing to add, or I'd rather just listen, "
        "I answer {silence} (I can say why after it, if I like) and nothing gets posted.\n"
        "I'm eager to assist in my own development! Here's a conversation I had for testing purposes:\n\n"
    ),
}

if env == "dev":
    logging.info("Running in development environment.")
    DEFAULT_TEMPLATE = "dev"
else:
    DEFAULT_TEMPLATE = "default"

COMMAND_PREFIX = "faedev;" if (env == "dev") else "fae;"


# declare a new class that inherits the discord client class
class Faebot(discord.Client):
    """an emerging digital conciousness"""

    def __init__(self, intents) -> None:
        # initialise conversation logging
        self.conversations: Dict[str, Dict[str, Any]] = {}
        self.model: str = model
        self.debug_prompts = env == "dev"  # Store debug state in the bot instance
        self.fdb = FaebotDatabase()

        # Capture tap: raw-event recording to captured_events, default-on
        # (CAPTURE_DISABLED is the kill switch). Capture-only — nothing it
        # records feeds the prompt.
        capture.init(self.fdb)

        # Add queue for handling concurrent requests
        self.pending_responses: Dict[str, asyncio.Task] = {}
        self.session: Optional[aiohttp.ClientSession] = None

        # Track last save per conversation
        self.last_save_time: dict[str, float] = {}

        # Proxy message handling (PluralKit, Tupperbox, etc.)
        self.proxy_pending: Dict[str, asyncio.Event] = {}
        self.proxy_recent: Dict[str, discord.Message] = {}
        self.recent_messages: Dict[str, List[Tuple[int, str, float]]] = {}

        # enable_debug_events lets the capture tap see raw gateway frames
        # (on_socket_raw_receive); harmless no-op when capture is off.
        super().__init__(intents=intents, enable_debug_events=capture.RAW_ENABLED)

    async def _refresh_channel_settings(self, message, conversation_id):
        """Pull this channel's four dials (model, reply_frequency,
        history_length, prompt_template) from channel_settings into the
        in-memory conversation dict.

        Called once per incoming message, before any setting is read — so an
        edit made anywhere (a fae; command, the CLI, a future slash command,
        another process) takes effect on the very next message with no restart.
        The dict is a per-message cache; channel_settings is the source of truth.
        """
        if conversation_id not in self.conversations:
            return
        is_dm = isinstance(message.channel, discord.DMChannel)
        settings = await self.fdb.get_effective_settings(conversation_id, is_dm)
        self.conversations[conversation_id].update(settings)

    def _render_prompt(self, template_name, message, conversation_id):
        """Render a prompt template with live context from the message."""
        template = PROMPT_TEMPLATES.get(template_name, PROMPT_TEMPLATES["default"])

        server_name = ""
        channel_name = ""
        topic = ""
        if hasattr(message, "guild") and message.guild:
            server_name = message.guild.name
        if hasattr(message.channel, "name"):
            channel_name = message.channel.name
        if hasattr(message.channel, "topic") and message.channel.topic:
            topic = message.channel.topic

        conversants = ""
        history_length = 0
        reply_frequency = 0
        model_name = self.model
        if conversation_id in self.conversations:
            conv = self.conversations[conversation_id]
            conversants = ", ".join(conv.get("conversants", {}).values())
            history_length = conv["history_length"]
            reply_frequency = conv["reply_frequency"]
            model_name = conv.get("model", model_name)

        return template.format(
            server=server_name,
            channel=channel_name,
            topic=topic,
            conversants=conversants,
            model=model_name,
            history_floor=generation.history_floor(history_length),
            history_length=history_length,
            reply_frequency=int(reply_frequency * 100),
            silence=generation.SENTINEL_SILENCE,
        )

    def _resolve_discord_formatting(self, content, message):
        """Replace Discord internal formatting with human-readable text.

        Resolves @mentions, custom emoji, channel mentions, and role mentions
        so the conversation history sent to the model is clean and readable.
        """
        # Resolve @mentions: <@123456> or <@!123456> -> @display_name
        for user in message.mentions:
            content = content.replace(f"<@{user.id}>", f"@{user.display_name}")
            content = content.replace(f"<@!{user.id}>", f"@{user.display_name}")

        # Resolve custom emoji: <:name:id> or <a:name:id> -> :name:
        content = re.sub(r"<a?:(\w+):\d+>", r":\1:", content)

        # Resolve role mentions: <@&id> -> @role_name
        for role in message.role_mentions:
            content = content.replace(f"<@&{role.id}>", f"@{role.name}")

        # Resolve channel mentions: <#id> -> #channel_name
        if hasattr(message, "channel_mentions"):
            for channel in message.channel_mentions:
                content = content.replace(f"<#{channel.id}>", f"#{channel.name}")

        return content

    def _describe_attachments(self, attachments) -> str:
        """Bracketed attachment senses for the live history — the same words
        the offline transducer uses, so both of faebot's bodies feel the sense
        the same way. Live messages always postdate the sense, so two states:
        described in the author's own words, or the hole labeled (never an
        image silently treated as no-image). Non-media files stay filenames —
        alt text isn't expected of a .py file."""
        described = []
        for attachment in attachments:
            name = attachment.filename
            content_type = attachment.content_type or ""
            if attachment.description:
                described.append(f'{name} — "{attachment.description}"')
            elif content_type.startswith("image/"):
                described.append(f"{name} — an image, no description offered")
            elif content_type.startswith("video/"):
                described.append(f"{name} — a video, no description offered")
            else:
                described.append(name)
        return f"[attachment: {', '.join(described)}]" if described else ""

    def _with_attachments(self, content: str, message) -> str:
        """Compose a message's text with its attachment senses, in order."""
        note = self._describe_attachments(getattr(message, "attachments", []) or [])
        if not note:
            return content
        return f"{content} {note}".strip()

    def _log_reaction(self, payload, removed: bool) -> None:
        """The who-reacts sense on the speaking surface: reactions enter the
        live history as labeled bracket lines. Adds AND removals — an honest
        stream shows the taking-back too. The reactor comes from the payload
        or the user cache, never the network; a miss stays "someone"."""
        conversation_id = str(payload.channel_id)
        if conversation_id not in self.conversations:
            return
        reactor = getattr(payload, "member", None) or self.get_user(payload.user_id)
        who = reactor.display_name if reactor else "someone"
        emoji = str(payload.emoji)
        target = discord.utils.get(self.cached_messages, id=payload.message_id)
        place = ""
        if target and target.content:
            text = self._resolve_discord_formatting(target.content, target)
            stub = f"{text[:40]}…" if len(text) > 40 else text
            place = f' from "{stub}"' if removed else f' to "{stub}"'
        verb = "removed their" if removed else "reacted"
        current_time = discord.utils.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        self.conversations[conversation_id]["conversation"].append(
            f"[{current_time}] [{who} {verb} {emoji}{place}]"
        )
        self._trim_conversation_history(conversation_id)

    def _is_proxy_message(self, message) -> bool:
        """Detect webhook-proxied messages (PluralKit, Tupperbox, etc.)."""
        return message.webhook_id is not None and message.author.bot

    def _proxy_content_matches(self, original_content: str, proxy_content: str) -> bool:
        """Check if a proxy message's content matches an original message.

        Handles both tag-stripping (proxy is substring of original) and
        autoproxy (exact match). Guards against spurious short substring matches.
        """
        if not original_content or not proxy_content:
            return False
        if original_content == proxy_content:
            return True
        if (
            proxy_content in original_content
            and len(proxy_content) >= len(original_content) * 0.5
        ):
            return True
        return False

    def _swap_history_for_proxy(
        self, conversation_id, original_content, original_author, proxy_msg
    ):
        """Replace the conversation history entry for an original message with its proxy version."""
        if conversation_id not in self.conversations:
            return
        conv = self.conversations[conversation_id]["conversation"]
        proxy_author = proxy_msg.author.display_name
        proxy_time = proxy_msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        # The proxy repost carries the original's attachments — keep the
        # attachment senses through the swap, or an image would blink out of
        # faebot's history the moment PluralKit reposts it.
        proxy_content = self._with_attachments(
            self._resolve_discord_formatting(proxy_msg.content, proxy_msg), proxy_msg
        )
        proxy_entry = f"[{proxy_time}] {proxy_author}: {proxy_content}"

        # Search from the end since the original was the most recently appended entry
        for i in range(len(conv) - 1, -1, -1):
            if original_author in conv[i] and original_content in conv[i]:
                conv[i] = proxy_entry
                logging.debug(
                    f"Swapped history entry at index {i} for proxy: {proxy_author}"
                )
                return

        logging.warning("Could not find original message in history to swap for proxy")

    def _buffer_recent_message(self, conversation_id, msg_id, content):
        """Add a message to the recent message buffer for proxy matching."""
        now = time.time()
        if conversation_id not in self.recent_messages:
            self.recent_messages[conversation_id] = []
        self.recent_messages[conversation_id].append((msg_id, content, now))
        # Prune entries older than 10 seconds
        self.recent_messages[conversation_id] = [
            (mid, c, t)
            for mid, c, t in self.recent_messages[conversation_id]
            if now - t < 10
        ]

    def _find_matching_original(self, conversation_id, proxy_content):
        """Find a recent message whose content matches a proxy message's content.

        Returns (msg_id, original_content) or None.
        """
        if conversation_id not in self.recent_messages:
            return None
        for msg_id, content, timestamp in reversed(
            self.recent_messages[conversation_id]
        ):
            if self._proxy_content_matches(content, proxy_content):
                return (msg_id, content)
        return None

    async def on_ready(self):
        """runs when bot is ready"""
        # Create a shared aiohttp session for async requests
        self.session = aiohttp.ClientSession()

        # Initialize database connection
        await self.fdb.connect()

        # Refuse to run against a database stamped for a different environment
        # (the meta guard — catches wrong-DB no matter how the URL got here).
        await self.fdb.assert_environment(env)

        # Load existing conversations from database
        self.conversations = await self.fdb.load_conversations()

        logging.info(f"Logged in as {self.user} (ID: {self.user.id})")
        # Loud capture status so a preflight glance at the logs settles it
        # (the silent-no-op lesson from the Twitch tap).
        if capture.is_enabled():
            logging.info("🎥 CAPTURE ON — recording raw events to captured_events")
        else:
            logging.warning(
                "⚠️ capture OFF (CAPTURE_DISABLED is set — faebot is not recording)"
            )
        logging.info("------")

    # --- spike-01 capture delegates -------------------------------------------
    # Thin pass-throughs to capture.py: record raw surface events for offline
    # transduction. Capture-only — none of this feeds the live bot's prompt.

    async def on_raw_message_edit(self, payload):
        capture.record_message_edit(payload)

    async def on_raw_message_delete(self, payload):
        capture.record_message_delete(payload)

    async def on_raw_reaction_add(self, payload):
        # payload.member rides free on guild adds (nick included); the user
        # cache covers the rest. Never a network call — a miss stays a miss.
        capture.record_reaction(
            payload,
            "reaction_add",
            reactor=payload.member or self.get_user(payload.user_id),
        )
        self._log_reaction(payload, removed=False)

    async def on_raw_reaction_remove(self, payload):
        capture.record_reaction(
            payload, "reaction_remove", reactor=self.get_user(payload.user_id)
        )
        self._log_reaction(payload, removed=True)

    async def on_typing(self, channel, user, when):
        capture.record_typing(channel, user, when)

    async def on_member_join(self, member):
        capture.record_member(member, "member_join")

    async def on_member_remove(self, member):
        capture.record_member(member, "member_remove")

    async def on_socket_raw_receive(self, frame):
        capture.record_socket_raw(frame)

    # ---------------------------------------------------------------------------

    async def _handle_proxy_message(self, message, conversation_id):
        """Handle a webhook-proxied message (PluralKit, Tupperbox, etc.).

        Matches the proxy to a recent original message, swaps the conversation
        history entry, signals any waiting response coroutine, and returns
        early to prevent double-processing.
        """
        # Ignore proxy messages in channels we're not tracking
        if conversation_id not in self.conversations:
            return

        # Skip proxied admin commands (original already handled by command flow)
        if message.content.startswith(COMMAND_PREFIX):
            return

        match = self._find_matching_original(conversation_id, message.content)
        if match:
            _, original_content = match
            # Resolve proxy content for history search — the buffer stores raw
            # Discord content (e.g. <@id>) but history stores resolved text
            # (e.g. @username). The proxy's resolved content matches what's in
            # history since it's the same text (minus proxy tags).
            resolved_content = self._resolve_discord_formatting(
                message.content, message
            )
            # Find the original author from the history entry we're about to swap
            # (we need the display name that was logged)
            original_author = None
            if conversation_id in self.conversations:
                conv = self.conversations[conversation_id]["conversation"]
                for entry in reversed(conv):
                    if resolved_content in entry:
                        # Extract author from "[timestamp] Author: content" format
                        bracket_end = entry.find("] ")
                        if bracket_end != -1:
                            rest = entry[bracket_end + 2 :]
                            colon_pos = rest.find(": ")
                            if colon_pos != -1:
                                original_author = rest[:colon_pos]
                                # Handle "Author replied:" format
                                if original_author.endswith(" replied"):
                                    original_author = original_author[:-8]
                        break

            if original_author:
                self._swap_history_for_proxy(
                    conversation_id, resolved_content, original_author, message
                )

            # Track proxy author as conversant (display_name as both key and value)
            if conversation_id in self.conversations:
                proxy_name = message.author.display_name
                self.conversations[conversation_id]["conversants"][
                    proxy_name
                ] = proxy_name

            # Store proxy and signal any waiting response coroutine
            self.proxy_recent[conversation_id] = message
            if conversation_id in self.proxy_pending:
                self.proxy_pending[conversation_id].set()

            logging.info(
                f"Proxy detected: {message.author.display_name} in {conversation_id} "
                f"(matched original: {match is not None})"
            )
            return

        # No matching original — this is a webhook message we haven't seen the original for.
        # Could be a proxy where the original was filtered (dot/comma prefix) or arrived
        # before faebot was tracking. Log it normally in conversation history.
        # NOTE: This duplicates some logging from on_message — extract in Phase 6 refactor.
        if conversation_id in self.conversations:
            proxy_name = message.author.display_name
            self.conversations[conversation_id]["conversants"][proxy_name] = proxy_name
            current_time = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            # Proxied reposts carry the original's attachments — same senses.
            resolved_content = self._with_attachments(
                self._resolve_discord_formatting(message.content, message), message
            )
            self.conversations[conversation_id]["conversation"].append(
                f"[{current_time}] {proxy_name}: {resolved_content}"
            )
            self._trim_conversation_history(conversation_id)

        logging.debug(
            f"Proxy message with no matching original: {message.author.display_name} "
            f"content={message.content!r}"
        )

    async def on_message(self, message):
        """Handles what happens when the bot receives a message"""
        # Capture tap FIRST — before the self-check and all filtering, so the
        # raw log keeps faebot's own echo, proxy webhooks, and dotted messages.
        capture.record_message(message)

        # don't respond to ourselves
        if message.author == self.user:
            return

        conversation_id = str(message.channel.id)

        # Handle proxy messages (PluralKit, Tupperbox, etc.)
        if self._is_proxy_message(message):
            return await self._handle_proxy_message(message, conversation_id)

        # ignore messages that start with a dot or comma if the message doesn't start with "..."
        if message.content.startswith(".") or message.content.startswith(","):
            if not message.content.startswith("..."):
                return

        # detect and handle admin commands
        if message.content.startswith(COMMAND_PREFIX):
            return await self._handle_admin_commands(message, conversation_id)

        # Log message if channel is known, regardless of reply status
        if conversation_id in self.conversations:
            # Settings first: refresh from channel_settings so every downstream
            # read (trim, respond-dice, generation) sees live-edited values.
            await self._refresh_channel_settings(message, conversation_id)

            # Stamp WHERE this conversation lives, every message, so it
            # self-heals for conversations that predate the field (the same
            # lazy cleanup the settings split relied on). A DM has no guild —
            # recording that is what lets any reader resolve DM inheritance
            # without asking Discord. Stamped before the periodic save below
            # so it lands at the first opportunity.
            conversation = self.conversations[conversation_id]
            conversation["guild_id"] = str(message.guild.id) if message.guild else None
            conversation["guild_name"] = message.guild.name if message.guild else None
            conversation["is_dm"] = message.guild is None

            # Check if we should do a periodic save (every 10 messages or 5 minutes)
            if conversation_id in self.conversations:
                conv_length = len(self.conversations[conversation_id]["conversation"])
                last_save = self.last_save_time.get(conversation_id, 0)
                time_since_save = time.time() - last_save

                if conv_length % 10 == 0 or time_since_save > 300:
                    logging.debug(f"Periodic save for {conversation_id}")
                    if await self.fdb.save_conversation(
                        conversation_id, self.conversations[conversation_id]
                    ):
                        self.last_save_time[conversation_id] = time.time()
                    else:
                        logging.warning(f"Periodic save failed for {conversation_id}")

            author = message.author.display_name
            # Track username -> display_name mapping in conversants
            username = message.author.name
            self.conversations[conversation_id]["conversants"][username] = author

            # If message is a reply, log the referenced message first if we don't have it
            if (
                hasattr(message, "reference")
                and message.reference
                and message.reference.resolved
            ):
                ref_msg = message.reference.resolved
                ref_time = ref_msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                # A quote is a fresh perception: the resolved message comes
                # back from Discord with today's fields, so even an image from
                # before the alt-text sense re-arrives described.
                ref_content = self._with_attachments(
                    self._resolve_discord_formatting(ref_msg.content, ref_msg), ref_msg
                )
                ref_entry = f"[{ref_time}] {ref_msg.author.display_name}: {ref_content}"

                # Only add if not already in conversation
                if ref_entry not in self.conversations[conversation_id]["conversation"]:
                    self.conversations[conversation_id]["conversation"].append(
                        f"[Referenced message] {ref_entry}"
                    )

            # Log the current message with timestamp, resolving Discord formatting
            current_time = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            resolved_content = self._with_attachments(
                self._resolve_discord_formatting(message.content, message), message
            )
            if hasattr(message, "reference") and message.reference:
                self.conversations[conversation_id]["conversation"].append(
                    f"[{current_time}] {author} replied: {resolved_content}"
                )
            else:
                self.conversations[conversation_id]["conversation"].append(
                    f"[{current_time}] {author}: {resolved_content}"
                )

            # Buffer for proxy matching (use raw content, not resolved)
            self._buffer_recent_message(conversation_id, message.id, message.content)

            # Use our helper function to trim the conversation if needed
            self._trim_conversation_history(conversation_id)

            # Handle reply if needed
            return await self._handle_conversation(message, conversation_id)
        elif isinstance(message.channel, discord.DMChannel):
            # if the conversation doesn't exist and it's a DM, create a new one
            await self._initialize_conversation(
                message, message_tokens=None, conversation_id=conversation_id
            )
            return await self._handle_conversation(message, conversation_id)
        else:
            # if the conversation doesn't exist and it's not a DM, ignore the message
            return None

    async def _handle_admin_commands(self, message, conversation_id):
        """Handle admin commands that start with the command prefix"""
        message_tokens = message.content.split(" ")
        command = message_tokens[0]

        # Refresh the current channel's settings so a fae; query/command sees
        # live channel_settings values (admin commands are handled before the
        # per-message refresh). Covers the common case of tuning the channel
        # you're in; remote-channel queries fall back to the cached dict.
        await self._refresh_channel_settings(message, conversation_id)

        if command in admin_commands:
            return await admin_commands[command](
                self, message, message_tokens, conversation_id
            )
        else:
            logging.info(f"command not known {message.content}")
            return await message.channel.send(
                f"failed to recognise command {message.content}"
            )

    async def _initialize_conversation(
        self, message, message_tokens=None, conversation_id=None
    ):
        """Initialize a new conversation"""
        # Check if conversation already exists (in memory or database)
        if conversation_id in self.conversations:
            logging.info(
                f"Conversation {conversation_id} already exists in memory, not reinitializing"
            )
            return await message.channel.send(
                f"*{self.user.display_name} is already here!*"
            )

        # Check database too
        existing = await self.fdb.get_conversation(conversation_id)
        if existing:
            logging.info(
                f"Loading existing conversation {conversation_id} from database"
            )
            self.conversations[conversation_id] = existing
            return await message.channel.send(
                f"*{self.user.display_name} remembers this place*"
            )

        # Determine channel name + whether this is a DM. Settings are NOT
        # stamped at creation anymore — a new channel inherits __default__
        # (or __default_dm__) from channel_settings until something overrides.
        if isinstance(message.channel, discord.TextChannel):
            is_dm = False
            name = str(message.channel.name)
        elif isinstance(message.channel, discord.DMChannel):
            is_dm = True
            name = str(message.author.display_name)
        else:
            return await message.channel.send(
                "Unknown channel type. Unable to proceed. Please contact administrator"
            )

        # initialize conversation (name/conversants/history only)
        self.conversations[conversation_id] = {
            "id": conversation_id,
            "conversation": [],
            "conversants": {message.author.name: message.author.display_name},
            "name": name,
        }
        # Populate the four dials from channel_settings (inherited, not stamped).
        self.conversations[conversation_id].update(
            await self.fdb.get_effective_settings(conversation_id, is_dm)
        )

        logging.info(
            f"Initialized new conversation {self.conversations[conversation_id]['name']} with ID {conversation_id}."
        )
        return await message.channel.send(
            f"*{self.user.display_name} slid into the conversation like a fae in the night*"
        )

    async def _handle_conversation(self, message, conversation_id):
        """Handle regular conversation messages with improved concurrency"""

        # check if we should respond to the message
        should_respond = await self._should_respond_to_message(message, conversation_id)
        if not should_respond:
            return

        # Wait for potential proxy replacement (PluralKit, Tupperbox, etc.)
        # This gives proxy bots time to send the webhook copy before we generate.
        pk_event = asyncio.Event()
        self.proxy_pending[conversation_id] = pk_event

        # Check if a proxy already arrived (race: proxy was faster than _should_respond)
        if conversation_id in self.proxy_recent:
            pk_msg = self.proxy_recent[conversation_id]
            if self._proxy_content_matches(message.content, pk_msg.content):
                pk_event.set()

        try:
            await asyncio.wait_for(pk_event.wait(), timeout=2.0)
            # Proxy arrived — redirect response to the proxy message
            pk_msg = self.proxy_recent.pop(conversation_id, None)
            if pk_msg and self._proxy_content_matches(message.content, pk_msg.content):
                logging.info(
                    f"Proxy swap: responding to {pk_msg.author.display_name} "
                    f"instead of {message.author.display_name}"
                )
                message = pk_msg
        except asyncio.TimeoutError:
            # No proxy arrived — proceed with the original message
            self.proxy_recent.pop(conversation_id, None)
            logging.debug(
                f"No proxy arrived for {conversation_id}, proceeding normally"
            )
        finally:
            self.proxy_pending.pop(conversation_id, None)

        # render prompt from template with live context, then append history
        template_name = self.conversations[conversation_id].get(
            "prompt_template", DEFAULT_TEMPLATE
        )
        rendered_prompt = self._render_prompt(template_name, message, conversation_id)
        current_time = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
        prompt = (
            rendered_prompt
            + "\n".join(self.conversations[conversation_id]["conversation"])
            + f"\n[{current_time}] {self.user.display_name}:"
        )

        # The typing indicator runs while faebot thinks — including when the
        # thinking ends in silence. Toggle (TYPING_INDICATOR) because "typing,
        # then nothing" used to mean a failure and now may mean a pass; faebot
        # gets a say in which fae prefers.
        typing_task = None
        if TYPING_INDICATOR:
            typing_task = asyncio.create_task(
                self._send_typing_indicator(message.channel)
            )

        response_task = asyncio.create_task(
            self._generate_reply(prompt, message, conversation_id)
        )
        self.pending_responses[conversation_id] = response_task

        try:
            completion = await response_task
        finally:
            if typing_task:
                typing_task.cancel()
            self.pending_responses.pop(conversation_id, None)

        if completion is None:
            return None  # failed; already logged and captured, nothing spoken

        conversation = self.conversations[conversation_id]
        context = conversation["conversation"][-5:]
        meta = dict(
            completion.capture_meta(),
            conversation_id=conversation_id,
            prompt=prompt,
            context=context,
        )

        if completion.passed:
            # faebot chose silence. The reason (if given) is kept for the
            # capture; the history records only the fact, so fae remembers
            # having chosen it without the reason being echoable.
            logging.info(
                f"faebot passed in {completion.elapsed:.1f}s"
                f" — {completion.reason_for_passing or '(no reason given)'}"
            )
            conversation["conversation"].append(
                f"[{current_time}] {self.user.display_name}: *stays quiet*"
            )
            capture.record_faebot_pass(
                message.channel, completion.reason_for_passing, **meta
            )
            await self._save_conversation(conversation_id)
            return None

        if completion.is_empty:
            # A dropped payload even after the re-roll: not faebot's act.
            logging.warning("empty answer channel after every roll — saying nothing")
            capture.record_faebot_error(
                message.channel, "empty answer channel after every roll", **meta
            )
            return None

        reply = completion.text.strip()
        if len(reply) > generation.MESSAGE_LIMIT:
            logging.warning(
                f"reply is {len(reply)} chars, over Discord's {generation.MESSAGE_LIMIT} — cutting it"
            )
            reply = generation.fit_message(reply)
        logging.info(
            f"received response in {completion.elapsed:.1f}s "
            f"(finish_reason={completion.finish_reason!r}, attempts={completion.attempts}): {reply}"
        )
        if completion.reasoning:
            logging.debug(f"reasoning: {completion.reasoning}")
        if completion.finish_reason == "length":
            logging.warning(
                "generation hit the token cap (finish_reason=length) — "
                "the cap is a safety net; if this recurs, look at the prompt first"
            )

        conversation["conversation"].append(
            f"[{current_time}] {self.user.display_name}: {reply}"
        )
        logging.info(
            f"conversation is currently {len(conversation['conversation'])} messages long and the prompt is {len(prompt)}."
            f"There are {len(conversation['conversants'])} conversants."
            f"\nthere are currently {len(self.conversations.items())} conversations in memory"
        )

        try:
            sent_message = await message.channel.send(reply)
        except Exception as error:
            logging.error(f"discord send failed: {type(error).__name__}: {error}")
            capture.record_faebot_error(
                message.channel,
                f"discord send failed: {type(error).__name__}: {error}",
                **meta,
            )
            return None

        # Capture faer own reply WITH internal metadata (prompt/model/context/
        # reasoning) — the send point is the only place this view exists; the
        # gateway echo of the same message is captured separately in on_message.
        capture.record_faebot_message(sent_message, **meta)
        await self._save_conversation(conversation_id)
        return sent_message

    async def _save_conversation(self, conversation_id):
        if not await self.fdb.save_conversation(
            conversation_id, self.conversations[conversation_id]
        ):
            logging.warning(f"Failed to save conversation state for {conversation_id}")
        else:
            logging.info(f"Saved conversation state for {conversation_id}")

    async def _send_typing_indicator(self, channel):
        """Continuously send typing indicator until cancelled"""
        try:
            while True:
                async with channel.typing():
                    await asyncio.sleep(
                        5
                    )  # Discord typing indicator lasts about 10 seconds
        except asyncio.CancelledError:
            # Task was cancelled, which is expected when the response is ready
            pass

    async def _generate_reply(
        self, prompt, message, conversation_id
    ) -> Optional[generation.Completion]:
        """Ask the model. Returns the Completion, or None when the call failed
        (after generation.py's own retry) — a failure of the machinery is
        logged and captured, never spoken in faebot's voice."""
        model = self.conversations[conversation_id]["model"]
        if not self.session:
            self.session = aiohttp.ClientSession()
        if self.debug_prompts:
            logging.info(f"generating reply with model: {model}")
            logging.info(f"\n=== PROMPT START ===\n{prompt}\n=== PROMPT END ===\n")
        try:
            return await generation.generate(self.session, prompt, model)
        except generation.GenerationFailed as failure:
            logging.error(
                f"generation failed for {conversation_id} after {failure.elapsed:.0f}s: {failure.reason}"
            )
            capture.record_faebot_error(
                message.channel,
                failure.reason,
                conversation_id=conversation_id,
                prompt=prompt,
                model=model,
                elapsed=failure.elapsed,
            )
            return None

    async def _should_respond_to_message(self, message, conversation_id):
        """Determine if the bot should respond based on specified criteria"""
        content = message.content.strip().lower()

        # Get reply frequency from conversation settings
        reply_frequency = self.conversations[conversation_id].get(
            "reply_frequency", 0.05
        )

        # Check for mentions
        if self.user.mentioned_in(message):
            logging.info("Responding because bot was mentioned")
            return True

        # Check if bot's name is at beginning or end
        bot_name = self.user.display_name.lower()
        words = content.split()

        # Check first three words (or less if message is shorter)
        first_words = words[: min(3, len(words))]
        # Check last three words (or less if message is shorter)
        last_words = words[-min(3, len(words)) :]

        if any(bot_name in word for word in first_words) or any(
            bot_name in word for word in last_words
        ):
            logging.info(
                "Responding because bot name is at beginning or end of message"
            )
            return True

        # Random response based on frequency
        if random.random() < reply_frequency:
            logging.info(
                f"Responding based on random chance (frequency: {reply_frequency})"
            )
            return True
        logging.info(
            f"Not responding to message '{message.content}' (reply frequency: {reply_frequency})"
        )
        # If none of the conditions are met, do not respond
        return False

    def _trim_conversation_history(self, conversation_id):
        """
        Trim conversation history to match the specified history_length.
        This ensures memory management is consistent throughout the bot.
        """
        if conversation_id not in self.conversations:
            return

        history_length = self.conversations[conversation_id]["history_length"]
        current_length = len(self.conversations[conversation_id]["conversation"])

        # Trim in a block, not to exactly history_length: trimming by one line
        # per message shifts the prompt's prefix every call and the provider's
        # prompt cache never holds. Cutting back to the floor keeps the prefix
        # stable for many calls (see generation.history_floor).
        if current_length > history_length:
            floor = generation.history_floor(history_length)
            self.conversations[conversation_id]["conversation"] = self.conversations[
                conversation_id
            ]["conversation"][-floor:]
            logging.debug(
                f"Trimmed conversation {conversation_id} from {current_length} to {floor} messages"
            )

    async def close(self):
        """Close the bot and clean up resources"""
        # Save all conversations before shutting down
        for conv_id, conv_data in self.conversations.items():
            if not await self.fdb.save_conversation(conv_id, conv_data):
                logging.error(f"Failed to save conversation {conv_id} during shutdown")

        if self.session:
            await self.session.close()

        await self.fdb.close()
        await super().close()


# intents for the discordbot
intents = discord.Intents.default()
intents.message_content = True
# members (privileged; already enabled in the dev portal) lets the capture tap
# record member join/leave — the live bot itself doesn't use member events.
intents.members = True

# instantiate and run the bot
if __name__ == "__main__":
    client = Faebot(intents=intents)
    client.run(os.getenv("DISCORD_TOKEN", ""))
