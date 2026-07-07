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
import time


model = os.getenv("MODEL_NAME", "google/gemini-2.0-flash-001")  # Updated default model
admin = os.getenv("ADMIN", "")
env = os.getenv("ENVIRONMENT", "dev").lower()

# set up logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.DEBUG if env == "dev" else logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Prompt templates — keyed by name, rendered at generation time from live Discord context.
# Available placeholders: {server}, {channel}, {topic}, {conversants}, {history_length}, {reply_frequency}
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
        "I'm running on KoboldCPP. I remember the last {history_length} messages and I reply to about {reply_frequency}% of messages in this channel.\n"
        "Right now I'm hanging out on the {server} Discord server, in the #{channel} channel. "
        "The channel topic is: {topic}\n"
        "I keep my replies short. Here's a conversation I had with some friends:\n\n"
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
        "I'm running on KoboldCPP. I remember the last {history_length} messages.\n"
        "I'm chatting privately on Discord with {conversants}. "
        "Here's the conversation we had:\n\n"
    ),
    "dev": (
        "I'm a development bot for testing faebot. "
        "I'm hanging out on the {server} Discord server, in the #{channel} channel. "
        "The channel topic is: {topic}\n"
        "I remember the last {history_length} messages and reply to about {reply_frequency}% of messages.\n"
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
        self.retries: Dict[str, int] = {}
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
        if conversation_id in self.conversations:
            conv = self.conversations[conversation_id]
            conversants = ", ".join(conv.get("conversants", {}).values())
            history_length = conv["history_length"]
            reply_frequency = conv["reply_frequency"]

        return template.format(
            server=server_name,
            channel=channel_name,
            topic=topic,
            conversants=conversants,
            history_length=history_length,
            reply_frequency=int(reply_frequency * 100),
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
        proxy_content = self._resolve_discord_formatting(proxy_msg.content, proxy_msg)
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
            logging.warning("⚠️ capture OFF (CAPTURE_DISABLED is set — faebot is not recording)")
        logging.info("------")

    # --- spike-01 capture delegates -------------------------------------------
    # Thin pass-throughs to capture.py: record raw surface events for offline
    # transduction. Capture-only — none of this feeds the live bot's prompt.

    async def on_raw_message_edit(self, payload):
        capture.record_message_edit(payload)

    async def on_raw_message_delete(self, payload):
        capture.record_message_delete(payload)

    async def on_raw_reaction_add(self, payload):
        capture.record_reaction(payload, "reaction_add")

    async def on_raw_reaction_remove(self, payload):
        capture.record_reaction(payload, "reaction_remove")

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
            resolved_content = self._resolve_discord_formatting(
                message.content, message
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
                ref_content = self._resolve_discord_formatting(ref_msg.content, ref_msg)
                ref_entry = f"[{ref_time}] {ref_msg.author.display_name}: {ref_content}"

                # Only add if not already in conversation
                if ref_entry not in self.conversations[conversation_id]["conversation"]:
                    self.conversations[conversation_id]["conversation"].append(
                        f"[Referenced message] {ref_entry}"
                    )

            # Log the current message with timestamp, resolving Discord formatting
            current_time = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            resolved_content = self._resolve_discord_formatting(
                message.content, message
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

        # Create a typing indicator that will continue until response is ready
        typing_task = asyncio.create_task(self._send_typing_indicator(message.channel))

        # Start generating the reply in the background
        response_task = asyncio.create_task(
            self._generate_reply(prompt, message, conversation_id)
        )

        # Store the task for potential cancellation or monitoring
        self.pending_responses[conversation_id] = response_task

        # Wait for the response while showing typing indicator
        try:
            reply = await response_task
            typing_task.cancel()  # Stop typing indicator when response is ready

            if not reply:
                return

            # Log the bot's reply with timestamp
            self.conversations[conversation_id]["conversation"].append(
                f"[{current_time}] {self.user.display_name}: {reply}"
            )

            logging.info(
                f"conversation is currently {len(self.conversations[conversation_id]['conversation'])} messages long and the prompt is {len(prompt)}."
                f"There are {len(self.conversations[conversation_id]['conversants'])} conversants."
                f"\nthere are currently {len(self.conversations.items())} conversations in memory"
            )

            # Send the reply
            sent_message = await message.channel.send(reply)

            # Get last 5 messages as context (excluding the bot's new reply)
            context = self.conversations[conversation_id]["conversation"][-6:-1]

            # Capture faer own reply WITH internal metadata (prompt/model/context)
            # — the send point is the only place this view exists; the gateway
            # echo of the same message is captured separately in on_message.
            capture.record_faebot_message(
                sent_message,
                conversation_id=conversation_id,
                prompt=prompt,
                model=self.conversations[conversation_id]["model"],
                context=context,
            )

            # Save the updated conversation state
            if not await self.fdb.save_conversation(
                conversation_id, self.conversations[conversation_id]
            ):
                logging.warning(
                    f"Failed to save conversation state for {conversation_id}"
                )
            else:
                logging.info(f"Saved bot response to database for {conversation_id}")
            return sent_message

        except Exception as e:
            typing_task.cancel()
            logging.error(f"Error handling conversation: {e}")
            return await message.channel.send(
                "An error occurred while generating a response."
            )
        finally:
            # Clean up the task reference
            if conversation_id in self.pending_responses:
                del self.pending_responses[conversation_id]

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

    async def _generate_reply(self, prompt, message, conversation_id):
        """Generate a reply using the AI model, with retry logic"""
        retries = self.retries.get(conversation_id, 0)
        model = self.conversations[conversation_id]["model"]
        try:
            reply = await self._generate_ai_response(prompt, model, conversation_id)
            self.retries[conversation_id] = 0
            return reply
        except Exception as e:
            logging.error(
                f"Error generating reply for conversation {conversation_id}: {e}"
            )
            # If there's an error, we log it and retry with a reduced prompt
            conversation_length = len(
                self.conversations.get(conversation_id, {}).get("conversation", [])
            )
            logging.info(
                f"could not generate. Reducing prompt size and retrying."
                f"Conversation is currently {conversation_length} messages long and prompt size is {len(prompt)} characters long. This is retry #{retries}"
            )

            # Manually trim by 2 messages for retries
            if (
                conversation_id in self.conversations
                and len(self.conversations[conversation_id]["conversation"]) >= 2
            ):
                self.conversations[conversation_id][
                    "conversation"
                ] = self.conversations[conversation_id]["conversation"][2:]

            if retries < 1:
                await asyncio.sleep(retries * 10)
                self.retries[conversation_id] = retries + 1
                # Note: We're returning None here as we'll retry with on_message
                return None

            logging.info("max retries reached. Giving up.")
            self.retries[conversation_id] = 0
            await message.channel.send(
                "`Something went wrong, please contact an administrator or try again`"
            )
            return None

    async def _generate_ai_response(
        self,
        prompt: str = "",
        model="google/gemini-2.0-flash-001",
        conversation_id=None,
    ) -> str:
        """Generates AI-powered responses using local KoboldCPP or OpenRouter API with text completion"""

        if not conversation_id or conversation_id not in self.conversations:
            return "Error: Invalid conversation context"

        # Check if we should use local model
        use_local = os.getenv("USE_LOCAL_MODEL", "false").lower() == "true"
        koboldcpp_url = os.getenv("KOBOLDCPP_URL", "http://localhost:6666")

        if self.debug_prompts:
            if use_local:
                logging.info(
                    f"generating reply with local KoboldCPP at {koboldcpp_url}"
                )
            else:
                logging.info(f"generating reply with OpenRouter model: {model}")
            logging.info(f"\n=== PROMPT START ===\n{prompt}\n=== PROMPT END ===\n")

        try:
            # Use aiohttp for async HTTP requests
            if not self.session:
                self.session = aiohttp.ClientSession()

            if use_local:
                # Use local KoboldCPP - native generation endpoint
                url = f"{koboldcpp_url}/api/v1/generate"
                headers = {
                    "Authorization": f"Bearer {os.getenv('KOBOLDCPP_KEY', '')}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "prompt": prompt,
                    "max_context_length": 4096,
                    "max_length": 150,
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "rep_pen": 1.18,
                    "rep_pen_range": 512,
                    "stop_sequence": ["[20", "\n\n"],
                }
                logging.info(f"✨ Using local KoboldCPP at {koboldcpp_url}")
            else:
                # Use OpenRouter
                url = "https://openrouter.ai/api/v1/completions"
                headers = {
                    "Authorization": f"Bearer {os.getenv('OPENROUTER_KEY', '')}",
                    "HTTP-Referer": os.getenv(
                        "SITE_URL", "https://github.com/transfaeries/faebot-discord"
                    ),
                    "X-Title": "Faebot Discord",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": model,
                    "prompt": prompt,
                    "temperature": 0.7,
                    "max_tokens": 250,
                    "stop": ["[20"],
                    "frequency_penalty": 1.5,
                    # Disable provider-side reasoning: some OpenRouter providers
                    # (e.g. Novita for kimi-k2) run a hidden reasoning pass that
                    # eats the whole max_tokens budget and returns empty text —
                    # faebot goes silent. We want plain completion, no reasoning.
                    "reasoning": {"enabled": False},
                }

            async with self.session.post(
                url=url,
                headers=headers,
                json=payload,
            ) as response:
                result = await response.json()

                if self.debug_prompts:
                    logging.info(f"API response: {result}")

                # Extract the completion text
                if use_local:
                    # KoboldCPP returns results in a different format
                    if "results" in result and len(result["results"]) > 0:
                        reply = result["results"][0]["text"]
                        return str(reply.strip())
                else:
                    # OpenRouter format
                    if "choices" in result and len(result["choices"]) > 0:
                        reply = result["choices"][0]["text"]
                        return str(reply.strip())

                logging.error(f"Unexpected response format: {result}")
                return "I couldn't generate a response. Please try again."

        except Exception as e:
            logging.error(f"Error in API call: {e}")
            return "Sorry, I encountered an error while trying to respond."

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

        # Trim to exactly history_length
        if current_length > history_length:
            excess = current_length - history_length
            self.conversations[conversation_id]["conversation"] = self.conversations[
                conversation_id
            ]["conversation"][excess:]
            logging.debug(
                f"Trimmed conversation {conversation_id} from {current_length} to {history_length} messages"
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
