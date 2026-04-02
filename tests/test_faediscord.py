import asyncio
import pytest
import os
from unittest.mock import AsyncMock, Mock, patch
from faediscord import Faebot, COMMAND_PREFIX, PROMPT_TEMPLATES, DEFAULT_TEMPLATE
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFaebot:
    @pytest.fixture
    def mock_discord_intents(self):
        """Mock Discord intents"""
        intents = Mock()
        intents.message_content = True
        return intents

    @pytest.fixture
    def faebot(self, mock_discord_intents):
        """Create a Faebot instance for testing"""
        with patch("discord.Client.__init__", return_value=None):
            bot = Faebot(mock_discord_intents)
            # Mock _connection attribute which is required by discord.py
            bot._connection = Mock()
            # Mock the database to avoid actual database calls
            bot.fdb = Mock()
            bot.fdb.save_conversation = AsyncMock(return_value=True)
            bot.fdb.save_bot_message = AsyncMock(return_value=True)
            bot.fdb.get_conversation = AsyncMock(return_value=None)
            bot.fdb.connect = AsyncMock(return_value=True)
            bot.fdb.close = AsyncMock(return_value=True)
            bot.fdb.load_conversations = AsyncMock(return_value={})
            # Use patch to override the user property
            user_mock = Mock()
            user_mock.id = 12345
            user_mock.display_name = "faebot"
            user_mock.mentioned_in = Mock(return_value=False)
            with patch.object(type(bot), "user", new_callable=lambda: user_mock):
                bot._user_mock = user_mock  # Store for easy access in tests
                return bot

    @pytest.fixture
    def mock_message(self):
        """Mock Discord message"""
        message = Mock()
        message.author = Mock()
        message.author.name = "test_user"
        message.author.display_name = "Test User"
        message.content = "test message"
        message.channel = Mock()
        message.channel.id = 123456789
        message.channel.name = "test-channel"
        message.channel.type = ["text"]
        message.channel.send = AsyncMock()
        message.created_at = Mock()
        message.created_at.strftime.return_value = "2024-01-01 12:00:00"
        message.guild = Mock()
        message.guild.name = "Test Server"
        message.reference = None
        message.mentions = []
        message.role_mentions = []
        message.channel_mentions = []
        message.webhook_id = None
        message.id = 111111111111111111
        return message

    def test_init(self, faebot):
        """Test Faebot initialization"""
        assert isinstance(faebot.conversations, dict)
        assert isinstance(faebot.retries, dict)
        assert isinstance(faebot.pending_responses, dict)
        assert isinstance(faebot.proxy_pending, dict)
        assert isinstance(faebot.proxy_recent, dict)
        assert isinstance(faebot.recent_messages, dict)
        assert faebot.session is None
        assert faebot.model == os.getenv("MODEL_NAME", "google/gemini-2.0-flash-001")

    @pytest.mark.asyncio
    async def test_on_ready(self, faebot):
        """Test on_ready method"""
        mock_session = AsyncMock()
        with patch("aiohttp.ClientSession", return_value=mock_session):
            await faebot.on_ready()
            assert faebot.session == mock_session

    @pytest.mark.asyncio
    async def test_close(self, faebot):
        """Test close method"""
        faebot.session = AsyncMock()

        with patch("discord.Client.close", new_callable=AsyncMock) as mock_super_close:
            await faebot.close()
            faebot.session.close.assert_called_once()
            mock_super_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_message_ignore_self(self, faebot, mock_message):
        """Test that bot ignores its own messages"""
        mock_message.author = faebot.user
        result = await faebot.on_message(mock_message)
        assert result is None

    @pytest.mark.asyncio
    async def test_on_message_ignore_dot_comma(self, faebot, mock_message):
        """Test that bot ignores messages starting with . or , (except ...)"""
        # Test dot
        mock_message.content = ".test"
        result = await faebot.on_message(mock_message)
        assert result is None

        # Test comma
        mock_message.content = ",test"
        result = await faebot.on_message(mock_message)
        assert result is None

    @pytest.mark.asyncio
    async def test_initialize_conversation_text_channel(self, faebot, mock_message):
        """Test conversation initialization for text channel"""
        conversation_id = str(mock_message.channel.id)
        mock_message.channel.topic = "Test topic"

        await faebot._initialize_conversation(
            mock_message, conversation_id=conversation_id
        )

        assert conversation_id in faebot.conversations
        conv = faebot.conversations[conversation_id]
        assert conv["id"] == conversation_id
        assert conv["conversants"] == {
            mock_message.author.name: mock_message.author.display_name
        }
        assert conv["reply_frequency"] == 0.05
        assert conv["prompt_template"] == DEFAULT_TEMPLATE
        mock_message.channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_conversation_dm(self, faebot):
        """Test conversation initialization for DM"""
        # Create DM message
        dm_message = Mock()
        dm_message.author = Mock()
        dm_message.author.name = "test_user"
        dm_message.channel = Mock()
        dm_message.channel.id = 987654321
        dm_message.channel.type = ["private"]
        dm_message.channel.send = AsyncMock()

        conversation_id = str(dm_message.channel.id)

        await faebot._initialize_conversation(
            dm_message, conversation_id=conversation_id
        )

        assert conversation_id in faebot.conversations
        conv = faebot.conversations[conversation_id]
        assert conv["reply_frequency"] == 1
        assert conv["prompt_template"] == "dm"
        dm_message.channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_respond_to_message_mention(self, faebot, mock_message):
        """Test response logic for mentions"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {"reply_frequency": 0.05}
        faebot.user.mentioned_in.return_value = True

        result = await faebot._should_respond_to_message(mock_message, conversation_id)
        assert result is True

    @pytest.mark.asyncio
    async def test_should_respond_to_message_name_in_content(
        self, faebot, mock_message
    ):
        """Test response logic for bot name in message"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {"reply_frequency": 0.05}
        faebot.user.mentioned_in.return_value = False
        faebot.user.display_name = "faebot"
        mock_message.content = "hey faebot how are you"

        result = await faebot._should_respond_to_message(mock_message, conversation_id)
        assert result is True

    @pytest.mark.asyncio
    async def test_should_respond_random_frequency(self, faebot, mock_message):
        """Test random response frequency"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "reply_frequency": 1.0
        }  # Always respond
        faebot.user.mentioned_in.return_value = False
        faebot.user.display_name = "faebot"
        mock_message.content = "random message"

        with patch("faediscord.random.random", return_value=0.5):
            result = await faebot._should_respond_to_message(
                mock_message, conversation_id
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_generate_ai_response_error(self, faebot):
        """Test AI response generation error handling"""
        conversation_id = "test_conv"
        faebot.conversations[conversation_id] = {"prompt_template": "default"}
        faebot.session = AsyncMock()
        faebot.session.post.side_effect = Exception("API Error")

        result = await faebot._generate_ai_response(
            "test prompt", "test-model", conversation_id
        )
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_generate_reply_success(self, faebot, mock_message):
        """Test successful reply generation"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "prompt_template": "default",
            "model": "test-model",
            "conversation": [],
        }

        with patch.object(faebot, "_generate_ai_response", return_value="test reply"):
            result = await faebot._generate_reply(
                "test prompt", mock_message, conversation_id
            )
            assert result == "test reply"
            assert faebot.retries.get(conversation_id, 0) == 0

    @pytest.mark.asyncio
    async def test_generate_reply_error_retry(self, faebot, mock_message):
        """Test reply generation error and retry logic"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "prompt_template": "default",
            "model": "test-model",
            "conversation": ["msg1", "msg2", "msg3", "msg4"],
        }
        mock_message.channel.send = AsyncMock()

        with patch.object(
            faebot, "_generate_ai_response", side_effect=Exception("Test error")
        ):
            result = await faebot._generate_reply(
                "test prompt", mock_message, conversation_id
            )
            assert result is None
            assert (
                len(faebot.conversations[conversation_id]["conversation"]) == 2
            )  # Reduced by 2

    @pytest.mark.asyncio
    async def test_handle_reply_message(self, faebot, mock_message):
        """Test handling of reply messages"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "conversants": {mock_message.author.name: mock_message.author.display_name},
            "conversation": [],
            "history_length": 69,
            "reply_frequency": 0,
        }

        # Create a referenced message
        ref_msg = Mock()
        ref_msg.author.name = "other_user"
        ref_msg.author.display_name = "Other User"
        ref_msg.content = "original message"
        ref_msg.created_at.strftime.return_value = "2024-01-01 11:59:00"
        ref_msg.mentions = []
        ref_msg.role_mentions = []
        ref_msg.channel_mentions = []

        mock_message.reference = Mock()
        mock_message.reference.resolved = ref_msg
        mock_message.content = "this is a reply"

        with patch.object(faebot, "_should_respond_to_message", return_value=False):
            await faebot.on_message(mock_message)

            # Check that both referenced and current messages are logged
            conversation = faebot.conversations[conversation_id]["conversation"]
            assert any("original message" in msg for msg in conversation)
            assert any("replied:" in msg for msg in conversation)

    def test_prompt_templates(self):
        """Test that prompt templates contain expected placeholders"""
        assert "{server}" in PROMPT_TEMPLATES["default"]
        assert "{history_length}" in PROMPT_TEMPLATES["default"]
        assert "{conversants}" in PROMPT_TEMPLATES["dm"]
        assert "development bot" in PROMPT_TEMPLATES["dev"]
        assert "{reply_frequency}" in PROMPT_TEMPLATES["dev"]

    @pytest.mark.asyncio
    async def test_conversation_logging(self, faebot, mock_message):
        """Test that conversations are properly logged"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "conversants": {},
            "conversation": [],
            "history_length": 69,
            "reply_frequency": 0,
        }

        with patch.object(faebot, "_should_respond_to_message", return_value=False):
            await faebot.on_message(mock_message)

            # Check that message was logged (username as key in conversants dict)
            assert (
                mock_message.author.name
                in faebot.conversations[conversation_id]["conversants"]
            )
            assert any(
                mock_message.content in msg
                for msg in faebot.conversations[conversation_id]["conversation"]
            )

    @pytest.mark.asyncio
    async def test_memory_trimming_when_over_limit(self, faebot, mock_message):
        """Test that conversation memory is trimmed when over limit"""
        conversation_id = str(mock_message.channel.id)
        # Create a conversation that's over the limit
        long_conversation = ["msg" + str(i) for i in range(100)]
        faebot.conversations[conversation_id] = {
            "conversants": {mock_message.author.name: mock_message.author.display_name},
            "conversation": long_conversation,
            "history_length": 69,
            "reply_frequency": 0,
        }

        with patch.object(faebot, "_should_respond_to_message", return_value=False):
            await faebot.on_message(mock_message)

            # Should have trimmed conversation and added new message
            conv_length = len(faebot.conversations[conversation_id]["conversation"])
            assert conv_length <= 70  # 69 + 1 new message, then trimmed

    @pytest.mark.asyncio
    async def test_admin_command_prefix_detection(self, faebot, mock_message):
        """Test admin command prefix detection"""
        mock_message.content = f"{COMMAND_PREFIX}test"

        with patch.object(
            faebot, "_handle_admin_commands", new_callable=AsyncMock
        ) as mock_handle:
            await faebot.on_message(mock_message)
            mock_handle.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_response_handling(self, faebot, mock_message):
        """Test that concurrent responses are handled properly"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "conversants": {mock_message.author.name: mock_message.author.display_name},
            "conversation": [],
            "history_length": 69,
            "reply_frequency": 1.0,  # Always respond
            "prompt_template": "default",
            "model": "test-model",
        }

        async def mock_wait_for_timeout(coro, timeout):
            coro.close()  # Clean up the unawaited coroutine
            raise asyncio.TimeoutError

        with patch.object(faebot, "_should_respond_to_message", return_value=True):
            with patch.object(faebot, "_generate_reply", return_value="test response"):
                with patch.object(
                    faebot, "_send_typing_indicator", new_callable=AsyncMock
                ):
                    with patch("asyncio.wait_for", side_effect=mock_wait_for_timeout):
                        await faebot._handle_conversation(mock_message, conversation_id)

                        # Check that response task was cleaned up
                        assert conversation_id not in faebot.pending_responses
                        mock_message.channel.send.assert_called_once_with(
                            "test response"
                        )

    def test_render_prompt_replaces_placeholders(self, faebot, mock_message):
        """Test that _render_prompt replaces placeholders with live context"""
        conversation_id = str(mock_message.channel.id)
        mock_message.channel.topic = "Cool test topic"
        faebot.conversations[conversation_id] = {
            "conversants": {"test_user": "Test User"},
            "history_length": 20,
            "reply_frequency": 0.05,
        }

        rendered = faebot._render_prompt("default", mock_message, conversation_id)

        assert "{server}" not in rendered
        assert "{channel}" not in rendered
        assert "{topic}" not in rendered
        assert "{history_length}" not in rendered
        assert "{reply_frequency}" not in rendered
        assert "Test Server" in rendered
        assert "test-channel" in rendered
        assert "Cool test topic" in rendered
        assert "20" in rendered
        assert "5%" in rendered

    def test_render_prompt_dm_template(self, faebot, mock_message):
        """Test that DM template renders conversants"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "conversants": {"alice": "Alice", "bob": "Bob"},
            "history_length": 50,
            "reply_frequency": 1.0,
        }

        rendered = faebot._render_prompt("dm", mock_message, conversation_id)

        assert "Alice, Bob" in rendered
        assert "50" in rendered

    def test_resolve_discord_formatting_mentions(self, faebot):
        """Test that @mentions are resolved to display names"""
        message = Mock()
        user1 = Mock()
        user1.id = 882358999830364212
        user1.display_name = "Ember"
        user2 = Mock()
        user2.id = 123456789012345678
        user2.display_name = "Aisling"
        message.mentions = [user1, user2]
        message.role_mentions = []
        message.channel_mentions = []

        content = "hey <@882358999830364212> and <@!123456789012345678> what's up"
        result = faebot._resolve_discord_formatting(content, message)

        assert result == "hey @Ember and @Aisling what's up"

    def test_resolve_discord_formatting_emoji(self, faebot):
        """Test that custom emoji are resolved to :name: form"""
        message = Mock()
        message.mentions = []
        message.role_mentions = []
        message.channel_mentions = []

        content = "love this <:faebotyay:1465010932068519999> so much <a:danceparty:9876543210>"
        result = faebot._resolve_discord_formatting(content, message)

        assert result == "love this :faebotyay: so much :danceparty:"

    def test_resolve_discord_formatting_channels_and_roles(self, faebot):
        """Test that channel and role mentions are resolved"""
        message = Mock()
        message.mentions = []
        role = Mock()
        role.id = 111222333444555666
        role.name = "Moderators"
        message.role_mentions = [role]
        channel = Mock()
        channel.id = 999888777666555444
        channel.name = "general"
        message.channel_mentions = [channel]

        content = "ping <@&111222333444555666> check <#999888777666555444>"
        result = faebot._resolve_discord_formatting(content, message)

        assert result == "ping @Moderators check #general"

    def test_resolve_discord_formatting_mixed(self, faebot):
        """Test resolving a message with mentions, emoji, and channels together"""
        message = Mock()
        user = Mock()
        user.id = 882358999830364212
        user.display_name = "Ember"
        message.mentions = [user]
        message.role_mentions = []
        message.channel_mentions = []

        content = "<@882358999830364212> look at this <:sparkle:123456> in the chat"
        result = faebot._resolve_discord_formatting(content, message)

        assert result == "@Ember look at this :sparkle: in the chat"

    # --- Proxy message detection tests ---

    def test_is_proxy_message_webhook(self, faebot):
        """Test that webhook messages with bot=True are detected as proxies"""
        message = Mock()
        message.webhook_id = 123456789
        message.author = Mock()
        message.author.bot = True
        assert faebot._is_proxy_message(message) is True

    def test_is_proxy_message_normal(self, faebot):
        """Test that normal user messages are not detected as proxies"""
        message = Mock()
        message.webhook_id = None
        message.author = Mock()
        message.author.bot = False
        assert faebot._is_proxy_message(message) is False

    def test_is_proxy_message_bot_no_webhook(self, faebot):
        """Test that bot messages without webhook_id are not detected as proxies"""
        message = Mock()
        message.webhook_id = None
        message.author = Mock()
        message.author.bot = True
        assert faebot._is_proxy_message(message) is False

    # --- Content matching tests ---

    def test_proxy_content_matches_exact(self, faebot):
        """Test exact match (autoproxy case)"""
        assert faebot._proxy_content_matches("hello world", "hello world") is True

    def test_proxy_content_matches_substring(self, faebot):
        """Test substring match (proxy tags stripped)"""
        # Original has proxy tag "<" at end, proxy has it stripped
        assert (
            faebot._proxy_content_matches(
                "how bout this brownie-dev! nyaa! <", "how bout this brownie-dev! nyaa!"
            )
            is True
        )

    def test_proxy_content_matches_prefix_tags(self, faebot):
        """Test substring match with prefix proxy tags"""
        assert faebot._proxy_content_matches("[hello friends]", "hello friends") is True

    def test_proxy_content_matches_too_short(self, faebot):
        """Test that tiny substrings don't match (false positive guard)"""
        assert (
            faebot._proxy_content_matches(
                "this is a long message about many things", "this"
            )
            is False
        )

    def test_proxy_content_matches_unrelated(self, faebot):
        """Test that unrelated content doesn't match"""
        assert faebot._proxy_content_matches("hello world", "goodbye moon") is False

    def test_proxy_content_matches_empty(self, faebot):
        """Test that empty content doesn't match"""
        assert faebot._proxy_content_matches("", "hello") is False
        assert faebot._proxy_content_matches("hello", "") is False

    # --- Recent message buffer tests ---

    def test_buffer_recent_message(self, faebot):
        """Test that messages are buffered and old ones pruned"""
        faebot._buffer_recent_message("chan1", 111, "hello")
        assert len(faebot.recent_messages["chan1"]) == 1
        assert faebot.recent_messages["chan1"][0][1] == "hello"

    def test_find_matching_original(self, faebot):
        """Test finding a matching original for a proxy message"""
        faebot._buffer_recent_message("chan1", 111, "hello world <")
        result = faebot._find_matching_original("chan1", "hello world")
        assert result is not None
        assert result == (111, "hello world <")

    def test_find_matching_original_no_match(self, faebot):
        """Test that no match is returned for unrelated content"""
        faebot._buffer_recent_message("chan1", 111, "hello world")
        result = faebot._find_matching_original("chan1", "goodbye moon")
        assert result is None

    def test_find_matching_original_empty_buffer(self, faebot):
        """Test that no match is returned for empty buffer"""
        result = faebot._find_matching_original("chan1", "hello")
        assert result is None

    # --- History swap tests ---

    def test_swap_history_for_proxy(self, faebot):
        """Test that history entry is replaced with proxy version"""
        conversation_id = "chan1"
        faebot.conversations[conversation_id] = {
            "conversation": [
                "[2024-01-01 12:00:00] ambassador faeries: hello brownie-dev! <",
            ],
            "conversants": {},
            "history_length": 20,
        }
        proxy_msg = Mock()
        proxy_msg.author = Mock()
        proxy_msg.author.display_name = "Ember | transfaeries"
        proxy_msg.created_at = Mock()
        proxy_msg.created_at.strftime.return_value = "2024-01-01 12:00:01"
        proxy_msg.content = "hello brownie-dev!"
        proxy_msg.mentions = []
        proxy_msg.role_mentions = []
        proxy_msg.channel_mentions = []

        faebot._swap_history_for_proxy(
            conversation_id,
            "hello brownie-dev! <",
            "ambassador faeries",
            proxy_msg,
        )

        assert len(faebot.conversations[conversation_id]["conversation"]) == 1
        assert (
            "Ember | transfaeries"
            in faebot.conversations[conversation_id]["conversation"][0]
        )
        assert (
            "hello brownie-dev!"
            in faebot.conversations[conversation_id]["conversation"][0]
        )

    # --- on_message proxy filter tests ---

    @pytest.mark.asyncio
    async def test_on_message_proxy_returns_early(self, faebot, mock_message):
        """Test that proxy messages are caught by the early filter"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "conversants": {},
            "conversation": [],
            "history_length": 20,
            "reply_frequency": 1.0,
            "prompt_template": "default",
            "model": "test-model",
        }
        # Set up as proxy message
        mock_message.webhook_id = 999888777
        mock_message.author.bot = True
        mock_message.author.display_name = "Ember | transfaeries"
        mock_message.content = "hello brownie-dev!"

        # Buffer a matching original
        faebot._buffer_recent_message(conversation_id, 111, "hello brownie-dev! <")
        # Add matching history entry
        faebot.conversations[conversation_id]["conversation"].append(
            "[2024-01-01 12:00:00] ambassador faeries: hello brownie-dev! <"
        )

        with patch.object(
            faebot, "_handle_conversation", new_callable=AsyncMock
        ) as mock_handle:
            await faebot.on_message(mock_message)
            # Proxy should NOT reach _handle_conversation
            mock_handle.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_message_proxy_signals_event(self, faebot, mock_message):
        """Test that proxy message signals the waiting event"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "conversants": {},
            "conversation": [],
            "history_length": 20,
            "reply_frequency": 1.0,
            "prompt_template": "default",
            "model": "test-model",
        }
        # Set up pending event
        import asyncio

        event = asyncio.Event()
        faebot.proxy_pending[conversation_id] = event

        # Set up as proxy message with matching original
        mock_message.webhook_id = 999888777
        mock_message.author.bot = True
        mock_message.author.display_name = "Ember | transfaeries"
        mock_message.content = "hello!"
        faebot._buffer_recent_message(conversation_id, 111, "hello!")

        await faebot.on_message(mock_message)

        assert event.is_set()
        assert conversation_id in faebot.proxy_recent
        assert faebot.proxy_recent[conversation_id] == mock_message

    @pytest.mark.asyncio
    async def test_on_message_proxy_swaps_history_with_mentions(
        self, faebot, mock_message
    ):
        """Test that proxy swap works when messages contain @mentions.

        The buffer stores raw content (<@id>) but history stores resolved
        content (@username). Option B fix: resolve proxy content before
        searching history.
        """
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "conversants": {},
            "conversation": [
                "[2024-01-01 12:00:00] ambassador faeries: hey @brownie-dev what's up"
            ],
            "history_length": 20,
            "reply_frequency": 1.0,
            "prompt_template": "default",
            "model": "test-model",
        }

        # Buffer stores raw content with Discord mention format
        faebot._buffer_recent_message(
            conversation_id, 111, "hey <@882358999830364212> what's up"
        )

        # Set up proxy message — PK strips tags but keeps mentions raw
        mock_message.webhook_id = 999888777
        mock_message.author.bot = True
        mock_message.author.display_name = "Ember | transfaeries"
        mock_message.content = "hey <@882358999830364212> what's up"
        mock_message.created_at = Mock()
        mock_message.created_at.strftime.return_value = "2024-01-01 12:00:01"

        # Mock a mention so _resolve_discord_formatting resolves <@id> -> @name
        mention_user = Mock()
        mention_user.id = 882358999830364212
        mention_user.display_name = "brownie-dev"
        mock_message.mentions = [mention_user]
        mock_message.role_mentions = []
        mock_message.channel_mentions = []

        with patch.object(
            faebot, "_handle_conversation", new_callable=AsyncMock
        ) as mock_handle:
            await faebot.on_message(mock_message)
            mock_handle.assert_not_called()

        # History should be swapped to the proxy version
        conv = faebot.conversations[conversation_id]["conversation"]
        assert len(conv) == 1
        assert "Ember | transfaeries" in conv[0]
        assert "brownie-dev" in conv[0]
        # Should NOT contain the raw mention format
        assert "<@882358999830364212>" not in conv[0]

    # --- _handle_conversation proxy wait tests ---

    @pytest.mark.asyncio
    async def test_handle_conversation_no_proxy_timeout(self, faebot, mock_message):
        """Test that _handle_conversation proceeds normally after proxy wait timeout"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "conversants": {mock_message.author.name: mock_message.author.display_name},
            "conversation": [],
            "history_length": 69,
            "reply_frequency": 1.0,
            "prompt_template": "default",
            "model": "test-model",
        }

        async def mock_wait_for_timeout(coro, timeout):
            coro.close()  # Clean up the unawaited coroutine
            raise asyncio.TimeoutError

        with patch.object(faebot, "_should_respond_to_message", return_value=True):
            with patch.object(faebot, "_generate_reply", return_value="test reply"):
                with patch.object(
                    faebot, "_send_typing_indicator", new_callable=AsyncMock
                ):
                    with patch(
                        "asyncio.wait_for", side_effect=mock_wait_for_timeout
                    ):
                        await faebot._handle_conversation(mock_message, conversation_id)
                        mock_message.channel.send.assert_called_once_with("test reply")

        # Proxy state should be cleaned up
        assert conversation_id not in faebot.proxy_pending

    @pytest.mark.asyncio
    async def test_handle_conversation_proxy_swap(self, faebot, mock_message):
        """Test that _handle_conversation redirects to proxy message when one arrives"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "conversants": {mock_message.author.name: mock_message.author.display_name},
            "conversation": ["[2024-01-01 12:00:00] Test User: hello faebot! <"],
            "history_length": 69,
            "reply_frequency": 1.0,
            "prompt_template": "default",
            "model": "test-model",
        }
        mock_message.content = "hello faebot! <"

        # Create a proxy message
        proxy_msg = Mock()
        proxy_msg.webhook_id = 999888777
        proxy_msg.author = Mock()
        proxy_msg.author.bot = True
        proxy_msg.author.display_name = "Ember | transfaeries"
        proxy_msg.author.name = "Ember | transfaeries"
        proxy_msg.content = "hello faebot!"
        proxy_msg.channel = mock_message.channel
        proxy_msg.created_at = Mock()
        proxy_msg.created_at.strftime.return_value = "2024-01-01 12:00:01"
        proxy_msg.mentions = []
        proxy_msg.role_mentions = []
        proxy_msg.channel_mentions = []
        proxy_msg.reference = None
        proxy_msg.guild = mock_message.guild
        proxy_msg.id = 222222222222222222

        # Pre-load the proxy message (simulating it arrived during the wait)
        faebot.proxy_recent[conversation_id] = proxy_msg

        # Make wait_for return immediately (proxy already there)
        async def mock_wait_for(coro, timeout):
            return await coro

        with patch.object(faebot, "_should_respond_to_message", return_value=True):
            with patch.object(faebot, "_generate_reply", return_value="hi ember!"):
                with patch.object(
                    faebot, "_send_typing_indicator", new_callable=AsyncMock
                ):
                    with patch("asyncio.wait_for", side_effect=mock_wait_for):
                        await faebot._handle_conversation(mock_message, conversation_id)

        mock_message.channel.send.assert_called_once_with("hi ember!")
