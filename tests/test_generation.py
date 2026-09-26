"""The generation seams: the silence sentinel, the empty re-roll, the retry
policy, the response parsing, and the history floor. No sockets — a fake
session hands back canned responses in order."""

import asyncio
import os
import sys
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generation  # noqa: E402
from generation import Completion, GenerationFailed  # noqa: E402


class FakeResponse:
    def __init__(self, status=200, body=None, text=""):
        self.status = status
        self._body = body
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._body

    async def text(self):
        return self._text


class FakeSession:
    """Hands back the scripted responses one per call; an Exception in the
    script is raised by the call instead."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def post(self, url, headers, json, timeout):
        self.calls.append(json)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def openrouter(text, reasoning="", finish_reason="stop"):
    return FakeResponse(
        body={
            "choices": [
                {"text": text, "reasoning": reasoning, "finish_reason": finish_reason}
            ],
            "model": "moonshotai/kimi-k3",
            "provider": "Moonshot",
            "usage": {"completion_tokens": 7},
        }
    )


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("generation.asyncio.sleep", return_value=None):
        yield


# --- the sentinel -----------------------------------------------------------


class TestSilence:
    def test_plain_sentinel(self):
        assert generation.said_nothing("NOTHING-TO-SAY")
        assert generation.pass_reason("NOTHING-TO-SAY") == ""

    def test_case_and_spacing_tolerant(self):
        assert generation.said_nothing(" nothing to say — they're busy")
        assert generation.pass_reason("Nothing-To-Say: it's a private moment") == (
            "it's a private moment"
        )

    def test_empty_is_a_drop_not_silence(self):
        assert not generation.said_nothing("")
        assert not generation.said_nothing("   ")
        assert Completion(text="").is_empty
        assert not Completion(text="").passed

    def test_sentinel_mid_reply_is_speech(self):
        assert not generation.said_nothing("I have nothing to say about that!")

    def test_completion_properties(self):
        completion = Completion(text="NOTHING-TO-SAY listening", reasoning="hm")
        assert completion.passed
        assert completion.reason_for_passing == "listening"
        assert completion.capture_meta()["reasoning"] == "hm"


# --- the history floor ------------------------------------------------------


class TestHistoryFloor:
    @pytest.mark.parametrize("limit,floor", [(50, 40), (69, 56), (75, 60), (4, 4)])
    def test_drops_a_fifth(self, limit, floor):
        assert generation.history_floor(limit) == floor


# --- generate() -------------------------------------------------------------


class TestGenerate:
    @pytest.mark.asyncio
    async def test_reads_text_and_reasoning(self):
        session = FakeSession([openrouter(" hi! ", reasoning="a thought")])
        completion = await generation.generate(session, "prompt", "m")
        assert completion.text == " hi! "
        assert completion.reasoning == "a thought"
        assert completion.provider == "Moonshot"
        assert completion.usage == {"completion_tokens": 7}
        assert completion.attempts == 1
        assert completion.params == {
            "temperature": generation.TEMPERATURE,
            "top_p": generation.TOP_P,
        }

    @pytest.mark.asyncio
    async def test_request_shape(self):
        session = FakeSession([openrouter("x")])
        await generation.generate(session, "the prompt", "moonshotai/kimi-k3")
        payload = session.calls[0]
        assert payload["prompt"] == "the prompt"
        assert payload["model"] == "moonshotai/kimi-k3"
        assert payload["reasoning"] == {"max_tokens": generation.REASONING_CAP}
        assert (
            payload["max_tokens"]
            == generation.GENERATION_CAP + generation.REASONING_CAP
        )
        assert "frequency_penalty" not in payload
        assert payload["provider"] == {
            "order": list(generation.PROVIDERS),
            "allow_fallbacks": False,
        }

    @pytest.mark.asyncio
    async def test_no_pin_when_providers_empty(self):
        session = FakeSession([openrouter("x")])
        with patch("generation.PROVIDERS", ()):
            await generation.generate(session, "p", "m")
        assert "provider" not in session.calls[0]

    def test_a_reply_that_fits_is_sent_whole(self):
        assert generation.split_message("short") == ["short"]
        assert generation.split_message("x" * 2000) == ["x" * 2000]

    def test_a_long_reply_splits_at_a_paragraph_and_loses_nothing(self):
        first = "one sentence. " * 100  # 1400 chars
        second = "another thought. " * 60  # 1020 chars
        parts = generation.split_message(first.strip() + "\n\n" + second.strip())
        assert parts == [first.strip(), second.strip()]
        assert all(len(part) <= generation.SPLIT_AT for part in parts)

    def test_without_a_paragraph_it_splits_at_a_sentence_then_a_word(self):
        text = "*leans in* " + "a small true thing. " * 120  # ~2400, one line
        parts = generation.split_message(text)
        assert len(parts) == 2
        assert parts[0].endswith("thing.") and len(parts[0]) <= 1800
        assert " ".join(parts).split() == text.split()
        words = "word " * 500  # no sentence ends at all
        parts = generation.split_message(words.strip())
        assert parts[0].endswith("word") and len(parts[0]) <= 1800
        assert " ".join(parts).split() == words.split()

    def test_an_unbroken_run_is_cut_at_the_target(self):
        parts = generation.split_message("x" * 4500)
        assert [len(part) for part in parts] == [1800, 1800, 900]

    def test_a_seam_too_early_is_passed_over(self):
        """A paragraph break a few words in would make a stub message."""
        text = "hi\n\n" + "a b c " * 400  # the only paragraph break is at 2
        parts = generation.split_message(text)
        assert len(parts[0]) > generation.SPLIT_AT // 2

    @pytest.mark.asyncio
    async def test_empty_answer_rolls_again(self):
        session = FakeSession([openrouter("", reasoning="…"), openrouter("there")])
        completion = await generation.generate(session, "p", "m")
        assert completion.text == "there"
        assert completion.attempts == 2

    @pytest.mark.asyncio
    async def test_empty_twice_is_returned_empty(self):
        session = FakeSession([openrouter(""), openrouter("")])
        completion = await generation.generate(session, "p", "m")
        assert completion.is_empty
        assert completion.attempts == generation.EMPTY_ROLLS

    @pytest.mark.asyncio
    async def test_retries_a_failed_call_once(self):
        session = FakeSession([FakeResponse(status=502, text="bad"), openrouter("ok")])
        completion = await generation.generate(session, "p", "m")
        assert completion.text == "ok"
        assert len(session.calls) == 2

    @pytest.mark.asyncio
    async def test_two_failures_raise(self):
        session = FakeSession([asyncio.TimeoutError(), FakeResponse(status=429)])
        with pytest.raises(GenerationFailed) as raised:
            await generation.generate(session, "p", "m")
        assert raised.value.is_rate_limit
        assert len(session.calls) == 2

    @pytest.mark.asyncio
    async def test_an_upstream_drop_re_aims_the_retry(self, monkeypatch):
        """A 504 — as a status or inside a 200 body — retries on the rest of
        the pinned list, never outside it."""
        monkeypatch.setattr(generation, "PROVIDERS", ("moonshotai", "modal"))
        monkeypatch.setattr(generation, "RATE_LIMIT_RETRY_DELAY", 0)
        session = FakeSession(
            [
                FakeResponse(body={"error": {"code": 504, "message": "aborted"}}),
                openrouter("ok"),
            ]
        )
        completion = await generation.generate(session, "p", "m")
        assert completion.text == "ok"
        assert session.calls[0]["provider"]["order"] == ["moonshotai", "modal"]
        assert session.calls[1]["provider"] == {
            "order": ["modal"],
            "allow_fallbacks": False,
        }

    @pytest.mark.asyncio
    async def test_a_429_re_aims_the_retry_too(self, monkeypatch):
        """The shared pool that said no is the one a same-list retry asks
        again (the 08-27 stream lost five asks that way): a 429 retries on
        the rest of the pinned list, after the short pause."""
        monkeypatch.setattr(generation, "PROVIDERS", ("moonshotai", "modal"))
        monkeypatch.setattr(generation, "RATE_LIMIT_RETRY_DELAY", 0)
        session = FakeSession(
            [FakeResponse(status=429, body={"error": "rate limited"}), openrouter("ok")]
        )
        completion = await generation.generate(session, "p", "m")
        assert completion.text == "ok"
        assert session.calls[0]["provider"]["order"] == ["moonshotai", "modal"]
        assert session.calls[1]["provider"] == {
            "order": ["modal"],
            "allow_fallbacks": False,
        }

    @pytest.mark.asyncio
    async def test_a_429_with_one_pinned_provider_retries_it(self, monkeypatch):
        monkeypatch.setattr(generation, "PROVIDERS", ("moonshotai",))
        monkeypatch.setattr(generation, "RATE_LIMIT_RETRY_DELAY", 0)
        session = FakeSession(
            [FakeResponse(status=429, body={"error": "rate limited"}), openrouter("ok")]
        )
        completion = await generation.generate(session, "p", "m")
        assert completion.text == "ok"
        assert [c["provider"]["order"] for c in session.calls] == [["moonshotai"]] * 2

    @pytest.mark.asyncio
    async def test_no_choices_is_a_failure(self):
        session = FakeSession([FakeResponse(body={"error": "x"})] * 2)
        with pytest.raises(GenerationFailed):
            await generation.generate(session, "p", "m")

    @pytest.mark.asyncio
    async def test_koboldcpp_shape(self, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_MODEL", "true")
        session = FakeSession([FakeResponse(body={"results": [{"text": "local"}]})])
        completion = await generation.generate(session, "p", "m")
        assert completion.text == "local"
        assert "reasoning" not in session.calls[0]
        assert session.calls[0]["max_length"] == generation.GENERATION_CAP
