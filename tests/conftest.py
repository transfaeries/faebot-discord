import pytest
import asyncio
import discord
from unittest.mock import AsyncMock, Mock, patch
import aiohttp


@pytest.fixture
def mock_discord_intents():
    """Mock Discord intents"""
    return discord.Intents.default()


@pytest.fixture
def mock_message():
    """Mock Discord message"""
    message = Mock()
    message.author = Mock()
    message.author.name = "test_user"
    message.content = "test message"
    message.channel = Mock()
    message.channel.id = 123456789
    message.channel.name = "test-channel"
    message.channel.type = ["text"]
    message.created_at = Mock()
    message.created_at.strftime.return_value = "2024-01-01 12:00:00"
    message.guild = Mock()
    message.guild.name = "Test Server"
    message.reference = None
    return message


@pytest.fixture
def mock_dm_message():
    """Mock Discord DM message"""
    message = Mock()
    message.author = Mock()
    message.author.name = "test_user"
    message.content = "test dm message"
    message.channel = Mock()
    message.channel.id = 987654321
    message.channel.type = ["private"]
    message.created_at = Mock()
    message.created_at.strftime.return_value = "2024-01-01 12:00:00"
    message.reference = None
    return message


@pytest.fixture
def mock_channel():
    """Mock Discord channel"""
    channel = Mock()
    channel.id = 123456789
    channel.name = "test-channel"
    channel.type = ["text"]
    channel.topic = "Test channel topic"
    channel.send = AsyncMock()
    channel.typing = AsyncMock()
    return channel


@pytest.fixture
async def mock_aiohttp_session():
    """Mock aiohttp session"""
    session = Mock(spec=aiohttp.ClientSession)
    session.post = AsyncMock()
    session.close = AsyncMock()
    return session


@pytest.fixture
def mock_openrouter_response():
    """Mock OpenRouter API response"""
    return {
        "choices": [{"message": {"content": "This is a test response from the AI"}}]
    }
