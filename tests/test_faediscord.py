import pytest
import asyncio
import os
from unittest.mock import AsyncMock, Mock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from faediscord import Faebot, COMMAND_PREFIX, DEFAULT_PROMPT, DM_PROMPT, DEV_PROMPT


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
        return message

    def test_init(self, faebot):
        """Test Faebot initialization"""
        assert isinstance(faebot.conversations, dict)
        assert isinstance(faebot.retries, dict)
        assert isinstance(faebot.pending_responses, dict)
        assert faebot.session is None
        assert faebot.model == os.getenv("MODEL_NAME", "google/gemini-2.0-flash-001")

    @pytest.mark.asyncio
    async def test_on_ready(self, faebot):
        """Test on_ready method"""
        with patch("aiohttp.ClientSession", return_value=AsyncMock()) as mock_session:
            await faebot.on_ready()
            assert faebot.session is not None
            mock_session.assert_called_once()

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
        assert conv["conversants"] == [mock_message.author.name]
        assert conv["reply_frequency"] == 0.05
        assert "Test Server" in conv["prompt"]
        assert "test-channel" in conv["prompt"]
        assert "Test topic" in conv["prompt"]
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
        assert dm_message.author.name in conv["prompt"]
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
        faebot.conversations[conversation_id] = {"prompt": "test prompt"}
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
            "prompt": "test prompt",
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
            "prompt": "test prompt",
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
            "conversants": [mock_message.author.name],
            "conversation": [],
            "history_length": 69,
            "reply_frequency": 0,
        }

        # Create a referenced message
        ref_msg = Mock()
        ref_msg.author.name = "other_user"
        ref_msg.content = "original message"
        ref_msg.created_at.strftime.return_value = "2024-01-01 11:59:00"

        mock_message.reference = Mock()
        mock_message.reference.resolved = ref_msg
        mock_message.content = "this is a reply"

        with patch.object(faebot, "_should_respond_to_message", return_value=False):
            await faebot.on_message(mock_message)

            # Check that both referenced and current messages are logged
            conversation = faebot.conversations[conversation_id]["conversation"]
            assert any("original message" in msg for msg in conversation)
            assert any("replied:" in msg for msg in conversation)

    def test_environment_prompts(self):
        """Test different prompts based on environment"""
        # Test that different prompts are used based on environment
        assert "{server}" in DEFAULT_PROMPT
        assert "{conversants}" in DM_PROMPT
        assert "development mode" in DEV_PROMPT

    @pytest.mark.asyncio
    async def test_conversation_logging(self, faebot, mock_message):
        """Test that conversations are properly logged"""
        conversation_id = str(mock_message.channel.id)
        faebot.conversations[conversation_id] = {
            "conversants": [],
            "conversation": [],
            "history_length": 69,
            "reply_frequency": 0,
        }

        with patch.object(faebot, "_should_respond_to_message", return_value=False):
            await faebot.on_message(mock_message)

            # Check that message was logged
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
            "conversants": [mock_message.author.name],
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
            "conversants": [mock_message.author.name],
            "conversation": [],
            "history_length": 69,
            "reply_frequency": 1.0,  # Always respond
            "prompt": "test prompt",
            "model": "test-model",
        }

        with patch.object(faebot, "_should_respond_to_message", return_value=True):
            with patch.object(faebot, "_generate_reply", return_value="test response"):
                with patch.object(
                    faebot, "_send_typing_indicator", new_callable=AsyncMock
                ):
                    await faebot._handle_conversation(mock_message, conversation_id)

                    # Check that response task was cleaned up
                    assert conversation_id not in faebot.pending_responses
                    mock_message.channel.send.assert_called_once_with("test response")

    @pytest.mark.asyncio
    async def test_prompt_placeholder_replacement(self, faebot, mock_message):
        """Test that prompt placeholders are properly replaced"""
        conversation_id = str(mock_message.channel.id)
        mock_message.channel.topic = "Cool test topic"

        await faebot._initialize_conversation(
            mock_message, conversation_id=conversation_id
        )

        prompt = faebot.conversations[conversation_id]["prompt"]
        # Check that placeholders were replaced
        assert "{server}" not in prompt
        assert "{channel}" not in prompt
        assert "{topic}" not in prompt
        assert "Test Server" in prompt
        assert "test-channel" in prompt
        assert "Cool test topic" in prompt
