import pytest
from unittest.mock import patch
import Faebot
import Discord

def test_generate_valid_response():
    with patch("replicate.run") as mock_run:
        mock_run.return_value = ["This is a test response"]
        bot = Faebot(intents=discord.Intents.default())
        response = bot.generate("Test prompt")
        assert response == "This is a test response"

def test_generate_empty_response_clears_memory():
    with patch("replicate.run") as mock_run:
        mock_run.return_value = [""]
        bot = Faebot(intents=discord.Intents.default())
        bot.conversation = ["This is a conversation"]
        response = bot.generate("Test prompt")
        assert response == "I don't know what to say"
        assert bot.conversation == []