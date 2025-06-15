import os
import sys
import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock
import logging
import discord

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_commands import (
    admin_command,
    admin_commands,
    COMMAND_PREFIX,
    _list_conversations,
    _invite_conversation,
    _forget_conversation,
    _admin_help,
    _set_or_return_model,
    _set_reply_frequency,
    _set_history_length,
    _set_conversation_prompt,
    _toggle_debug_mode,
)


class TestAdminCommands:
    @pytest.fixture
    def mock_bot(self):
        """Mock bot instance for testing"""
        bot = MagicMock()
        bot.conversations = {}
        bot.debug_prompts = False
        bot.conversation = []  # Add this for the forget tests
        return bot

    @pytest.fixture
    def mock_message(self):
        """Mock Discord message"""
        message = MagicMock()
        message.author = MagicMock()
        message.author.name = "test_admin"
        message.content = f"{COMMAND_PREFIX}test"
        message.channel = MagicMock()
        message.channel.send = AsyncMock()
        return message

    @pytest.fixture
    def setup_test_conversation(self, mock_bot):
        """Setup a test conversation in the mock bot"""
        mock_bot.conversations = {
            "123456": {
                "id": "123456",
                "name": "test-channel",
                "conversation": ["message1", "message2"],
                "model": "google/gemini-2.0-flash-001",
                "reply_frequency": 0.5,
                "history_length": 50,
                "prompt": "Test prompt",
            },
            "789012": {
                "id": "789012",
                "name": "another-channel",
                "conversation": ["hello", "world"],
                "model": "google/gemini-2.0-pro-001",
                "reply_frequency": 0.7,
                "history_length": 100,
                "prompt": "Another test prompt",
            },
        }
        return mock_bot

    def test_admin_command_decorator(self):
        """Test that the admin_command decorator registers commands correctly"""
        # Clear existing commands for this test
        admin_commands.clear()

        # Create a test command
        @admin_command("test")
        async def test_command(bot, message, message_tokens=None, conversation_id=None):
            """Test command"""
            pass

        assert f"{COMMAND_PREFIX}test" in admin_commands
        assert admin_commands[f"{COMMAND_PREFIX}test"].__doc__ == "Test command"

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_list_conversations_empty(self, mock_bot, mock_message):
        """Test listing conversations when there are none"""
        result = await _list_conversations(mock_bot, mock_message)

        mock_message.channel.send.assert_called_once_with(
            "there are no conversations in memory"
        )
        assert result == mock_message.channel.send.return_value

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_list_conversations(self, setup_test_conversation, mock_message):
        """Test listing conversations when there are some"""
        mock_bot = setup_test_conversation

        result = await _list_conversations(mock_bot, mock_message)

        mock_message.channel.send.assert_called_once()
        call_args = mock_message.channel.send.call_args[0][0]
        assert "here are the conversations I have in memory" in call_args
        assert "123456" in call_args
        assert "789012" in call_args
        assert "test-channel" in call_args
        assert "another-channel" in call_args

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_invite_conversation(self, mock_bot, mock_message):
        """Test initializing a conversation"""
        mock_bot._initialize_conversation = AsyncMock()
        conversation_id = "123456"

        await _invite_conversation(mock_bot, mock_message, ["invite"], conversation_id)

        mock_bot._initialize_conversation.assert_called_once_with(
            mock_message, message_tokens=["invite"], conversation_id=conversation_id
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_forget_conversation_empty(self, mock_bot, mock_message):
        """Test forgetting a conversation when there are none"""
        result = await _forget_conversation(
            mock_bot, mock_message, ["forget"], "123456"
        )

        mock_message.channel.send.assert_called_once_with(
            "there are no conversations to forget"
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_forget_current_conversation(
        self, setup_test_conversation, mock_message
    ):
        """Test forgetting the current conversation"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"

        # Set up the bot's conversation attribute
        mock_bot.conversation = []  # This needs to be initialized

        result = await _forget_conversation(
            mock_bot, mock_message, ["forget"], conversation_id
        )

        # Check that the conversation was cleared
        assert mock_bot.conversations[conversation_id]["conversation"] == []
        mock_message.channel.send.assert_called_once_with(
            f"cleared conversation {conversation_id}"
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_forget_specific_conversation(
        self, setup_test_conversation, mock_message
    ):
        """Test forgetting a specific conversation by ID"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"  # Current conversation
        target_id = "789012"  # Target to forget
        mock_message.content = f"{COMMAND_PREFIX}forget {target_id}"

        result = await _forget_conversation(
            mock_bot, mock_message, ["forget", target_id], conversation_id
        )

        # Check that the target conversation was cleared
        assert mock_bot.conversations[target_id]["conversation"] == []
        mock_message.channel.send.assert_called_once_with(
            f"cleared conversation {target_id}"
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_forget_invalid_conversation(
        self, setup_test_conversation, mock_message
    ):
        """Test forgetting an invalid conversation ID"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"
        invalid_id = "999999"
        mock_message.content = f"{COMMAND_PREFIX}forget {invalid_id}"

        result = await _forget_conversation(
            mock_bot, mock_message, ["forget", invalid_id], conversation_id
        )

        mock_message.channel.send.assert_called_once_with(
            f"Conversation ID '{invalid_id}' does not exist. Please provide a valid conversation ID."
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_admin_help(self, mock_bot, mock_message):
        """Test the admin help command"""
        result = await _admin_help(mock_bot, mock_message)

        mock_message.channel.send.assert_called_once()
        call_args = mock_message.channel.send.call_args[0][0]
        assert "Available admin commands" in call_args

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_set_model(self, setup_test_conversation, mock_message):
        """Test setting a new model for a conversation"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"
        new_model = "new-model-name"
        mock_message.content = f"{COMMAND_PREFIX}model {new_model}"

        result = await _set_or_return_model(
            mock_bot, mock_message, ["model", new_model], conversation_id
        )

        assert mock_bot.conversations[conversation_id]["model"] == new_model
        mock_message.channel.send.assert_called_once_with(
            f"Model changed to: {new_model} for conversation {conversation_id}"
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_get_current_model(self, setup_test_conversation, mock_message):
        """Test getting the current model for a conversation"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"
        current_model = mock_bot.conversations[conversation_id]["model"]

        result = await _set_or_return_model(
            mock_bot, mock_message, ["model"], conversation_id
        )

        mock_message.channel.send.assert_called_once_with(
            f"Current model for conversation {conversation_id}: {current_model}"
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_set_model_for_specific_conversation(
        self, setup_test_conversation, mock_message
    ):
        """Test setting a model for a specific conversation by ID"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"  # Current conversation
        target_id = "789012"  # Target to modify
        new_model = "new-model-name"
        mock_message.content = f"{COMMAND_PREFIX}model {target_id} {new_model}"

        result = await _set_or_return_model(
            mock_bot, mock_message, ["model", target_id, new_model], conversation_id
        )

        assert mock_bot.conversations[target_id]["model"] == new_model
        mock_message.channel.send.assert_called_once_with(
            f"Model changed to: {new_model} for conversation {target_id}"
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_toggle_debug_mode(self, mock_bot, mock_message):
        """Test toggling debug mode"""
        # Initial state is False
        assert mock_bot.debug_prompts is False

        # First toggle (False -> True)
        result = await _toggle_debug_mode(mock_bot, mock_message)
        assert mock_bot.debug_prompts is True
        mock_message.channel.send.assert_called_with("Debug mode is now: on")

        # Reset mock
        mock_message.channel.send.reset_mock()

        # Second toggle (True -> False)
        result = await _toggle_debug_mode(mock_bot, mock_message)
        assert mock_bot.debug_prompts is False
        mock_message.channel.send.assert_called_with("Debug mode is now: off")

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_set_reply_frequency(self, setup_test_conversation, mock_message):
        """Test setting reply frequency for a conversation"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"
        new_frequency = "0.8"
        mock_message.content = f"{COMMAND_PREFIX}frequency {new_frequency}"

        # Test the function
        result = await _set_reply_frequency(
            mock_bot, mock_message, ["frequency", new_frequency], conversation_id
        )

        # Verify results
        assert mock_bot.conversations[conversation_id]["reply_frequency"] == float(
            new_frequency
        )
        mock_message.channel.send.assert_called_once_with(
            f"Reply frequency set to: {new_frequency} for conversation {conversation_id}"
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_get_reply_frequency(self, setup_test_conversation, mock_message):
        """Test getting current reply frequency for a conversation"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"
        current_frequency = mock_bot.conversations[conversation_id]["reply_frequency"]

        result = await _set_reply_frequency(
            mock_bot, mock_message, ["frequency"], conversation_id
        )

        mock_message.channel.send.assert_called_once_with(
            f"Current reply frequency for conversation {conversation_id}: {current_frequency}"
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_set_history_length(self, setup_test_conversation, mock_message):
        """Test setting history length for a conversation"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"
        new_length = "200"
        mock_message.content = f"{COMMAND_PREFIX}history {new_length}"

        result = await _set_history_length(
            mock_bot, mock_message, ["history", new_length], conversation_id
        )

        assert mock_bot.conversations[conversation_id]["history_length"] == int(
            new_length
        )
        mock_message.channel.send.assert_called_once_with(
            f"History length set to: {new_length} for conversation {conversation_id}"
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_get_history_length(self, setup_test_conversation, mock_message):
        """Test getting current history length for a conversation"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"
        current_length = mock_bot.conversations[conversation_id]["history_length"]

        result = await _set_history_length(
            mock_bot, mock_message, ["history"], conversation_id
        )

        mock_message.channel.send.assert_called_once_with(
            f"Current history length is set to: {current_length}"
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_set_conversation_prompt(self, setup_test_conversation, mock_message):
        """Test setting a new prompt for a conversation"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"
        new_prompt = (
            "This is a new test prompt with {server} and {channel} placeholders"
        )
        mock_message.content = f"{COMMAND_PREFIX}prompt {new_prompt}"

        result = await _set_conversation_prompt(
            mock_bot, mock_message, ["prompt", new_prompt], conversation_id
        )

        assert mock_bot.conversations[conversation_id]["prompt"] == new_prompt
        mock_message.channel.send.assert_called_once_with(
            f"Prompt updated for conversation {conversation_id}"
        )

    @pytest.mark.asyncio
    @patch("admin_commands.admin", "test_admin")  # Mock the admin env variable
    async def test_get_conversation_prompt(self, setup_test_conversation, mock_message):
        """Test getting the current prompt for a conversation"""
        mock_bot = setup_test_conversation
        conversation_id = "123456"
        current_prompt = mock_bot.conversations[conversation_id]["prompt"]

        result = await _set_conversation_prompt(
            mock_bot, mock_message, ["prompt"], conversation_id
        )

        mock_message.channel.send.assert_called_once_with(
            f"Current prompt for conversation {conversation_id}: {current_prompt}"
        )
