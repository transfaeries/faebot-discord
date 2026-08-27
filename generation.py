"""
Generation for faebot-discord: one call to the model, and what came back.

The same shape as faebot-twitch's core.py (and faebot-core's Completion): the
answer channel (`text`) and the reasoning channel (`reasoning`) are kept apart
because they go different places — text to the channel, reasoning to the
capture. Chosen silence is a sentinel faebot SAYS, so it can never be confused
with an empty payload or a failed call. No discord.py in here.
"""

from dataclasses import dataclass, field, replace
from typing import Any, Optional
import asyncio
import logging
import os
import re
import time

import aiohttp


# Token caps are SAFETY NETS, not instructions. `max_tokens` is a server-side
# guillotine the model cannot see; only the prompt shapes length. Both caps sit
# above anything a normal reply needs, and hitting one is a log line to
# investigate (`finish_reason == "length"`), not a design. The reasoning cap
# rides ON TOP of the answer cap — sharing one purse lets deliberation eat the
# reply.
GENERATION_CAP = int(os.getenv("GENERATION_CAP", "600"))
REASONING_CAP = int(os.getenv("REASONING_CAP", "8000"))

# Discord refuses messages over 2000 characters. A reply that long is the
# prompt failing, not a feature; it is cut rather than lost.
MESSAGE_LIMIT = 2000

# Provider pinning, for the prompt cache and for the reasoning channel: only
# some OpenRouter providers cache the prompt (Modal and Moonshot did, on the
# 08-21 stream; nine others never), and some serve kimi-k3 without reasoning
# at all. Comma-separated OpenRouter provider slugs, tried in order, no
# fallback to the field; empty = let OpenRouter route freely.
PROVIDERS = tuple(
    slug.strip()
    for slug in os.getenv("OPENROUTER_PROVIDERS", "moonshotai,modal").split(",")
    if slug.strip()
)

# Sampling is pinned to Moonshot's published defaults for kimi-k3. The old
# gemini-era values (temperature 0.7, frequency_penalty 1.5) were never tuned
# for this model.
TEMPERATURE = float(os.getenv("TEMPERATURE", "1.0"))
TOP_P = float(os.getenv("TOP_P", "0.95"))

# Discord is slow and deliberate, so a late reply is still a reply: a real
# timeout, then ONE retry of the same request after a short pause (shorter for
# a 429, which clears in about a second on a shared pool). Nothing shrinks the
# prompt on retry any more — that dance dated from small-context models.
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))
ATTEMPTS = int(os.getenv("GENERATION_ATTEMPTS", "2"))
RETRY_DELAY = float(os.getenv("RETRY_DELAY", "5.0"))
RATE_LIMIT_RETRY_DELAY = float(os.getenv("RATE_LIMIT_RETRY_DELAY", "1.0"))

# An upstream drop (Moonshot's 504 — as an HTTP status, or inside a 200 body
# as `{"error": {"code": 504}}`) never reaches the next pinned provider on
# its own: OpenRouter walks the order only when a host is down at routing
# time. So the one retry is re-aimed at the rest of the pinned list, never
# outside it. A cold cache beats a lost ask.
UPSTREAM_DROP_STATUSES = (502, 503, 504)

# The answer channel coming back empty is a dropped payload (the model spoke
# into `reasoning` and left the text blank), so we roll once more. Bounded:
# resampling cures stochastic drops, never structural failures.
EMPTY_ROLLS = 2

# Stop sequences for the text-completion prompt: the next "[2026-..." line
# means the model started speaking for someone else.
STOP_SEQUENCES = ["[20"]


# THE silence sentinel: chosen silence must be SAID, so it can never be
# confused with a dropped payload (empty content). A coined hyphenated phrase
# has no natural collisions, which is what lets matching be case-insensitive.
# Anchored at the start of the reply; whatever follows it is faebot's reason
# for passing, which is kept (captured) but never posted.
SENTINEL_SILENCE = "NOTHING-TO-SAY"
_SILENCE_PATTERN = re.compile(r"^\W*nothing[\s-]+to[\s-]+say\b[\s\W]*", re.IGNORECASE)


def said_nothing(text: str) -> bool:
    """Did faebot choose silence? FALSE for empty text — that's a drop."""
    return bool(_SILENCE_PATTERN.match(text))


def pass_reason(text: str) -> str:
    """What faebot said after the sentinel, if anything — faer reason."""
    return _SILENCE_PATTERN.sub("", text, count=1).strip()


def history_floor(history_length: int) -> int:
    """How far the history is cut back once it overflows `history_length`.

    Trimming to exactly the limit shifts the prompt's prefix by one line on
    every message, so the provider's prompt cache never holds past the system
    prompt. Dropping a fifth at a time keeps the prefix stable for many calls;
    faebot remembers between the floor and the limit.
    """
    return history_length - history_length // 5


@dataclass(frozen=True)
class Completion:
    """One generation, and how it came to be."""

    text: str
    reasoning: str = ""
    elapsed: float = 0.0
    finish_reason: str = ""
    model: str = ""
    provider: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1

    @property
    def is_empty(self) -> bool:
        """An empty answer channel is a DROPPED PAYLOAD, never chosen silence."""
        return not self.text.strip()

    @property
    def passed(self) -> bool:
        """faebot chose silence (said the sentinel). Nothing gets posted."""
        return said_nothing(self.text)

    @property
    def reason_for_passing(self) -> str:
        return pass_reason(self.text) if self.passed else ""

    def capture_meta(self) -> dict[str, Any]:
        """The provenance fields worth writing alongside faebot's utterance."""
        return {
            "reasoning": self.reasoning,
            "elapsed": self.elapsed,
            "finish_reason": self.finish_reason,
            "model": self.model,
            "provider": self.provider,
            "params": self.params,
            "usage": self.usage,
            "attempts": self.attempts,
        }


class GenerationFailed(Exception):
    """The generating service could not be reached, or would not answer.

    Distinct from an empty Completion (a dropped payload) and from chosen
    silence: this is the call failing. Never spoken in faebot's voice."""

    def __init__(
        self, reason: str, elapsed: float = 0.0, status: Optional[int] = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.elapsed = elapsed
        self.status = status

    @property
    def is_rate_limit(self) -> bool:
        return self.status == 429

    @property
    def is_upstream_drop(self) -> bool:
        return self.status in UPSTREAM_DROP_STATUSES


async def generate(
    session: aiohttp.ClientSession, prompt: str, model: str
) -> Completion:
    """Ask the model for a Completion: retry a failed call once, roll again on
    an empty answer channel. Raises GenerationFailed when the retry fails too."""
    params = {"temperature": TEMPERATURE, "top_p": TOP_P}
    completion = Completion(text="")
    for roll in range(1, EMPTY_ROLLS + 1):
        completion = await _generate_with_retry(session, prompt, model, params)
        completion = replace(completion, attempts=roll, params=dict(params))
        if not completion.is_empty:
            return completion
        logging.warning(
            f"empty answer channel (reasoning had {len(completion.reasoning)} chars)"
            f" — rolling again ({roll}/{EMPTY_ROLLS})"
        )
    return completion


async def _generate_with_retry(
    session: aiohttp.ClientSession, prompt: str, model: str, params: dict
) -> Completion:
    providers = PROVIDERS
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return await _generate_once(session, prompt, model, params, providers)
        except GenerationFailed as failure:
            if attempt >= ATTEMPTS:
                raise
            if failure.is_upstream_drop and len(providers) > 1:
                providers = providers[1:]
                delay = RATE_LIMIT_RETRY_DELAY
                aimed = f" on {','.join(providers)}"
            else:
                delay = RATE_LIMIT_RETRY_DELAY if failure.is_rate_limit else RETRY_DELAY
                aimed = ""
            logging.warning(
                f"generation failed ({failure.reason}) — retrying in {delay:g}s"
                f"{aimed} ({attempt}/{ATTEMPTS})"
            )
            await asyncio.sleep(delay)
    raise GenerationFailed("no attempts configured")  # ATTEMPTS < 1


def _koboldcpp_url() -> Optional[str]:
    """The local model's base URL when USE_LOCAL_MODEL is set, else None."""
    if os.getenv("USE_LOCAL_MODEL", "false").lower() != "true":
        return None
    return os.getenv("KOBOLDCPP_URL", "http://localhost:6666")


def _request(
    prompt: str, model: str, params: dict, providers: tuple[str, ...] = PROVIDERS
) -> tuple[str, dict, dict]:
    """(url, headers, payload) for one text-completion call; `providers` is
    how a retry is aimed at the rest of the pinned list."""
    koboldcpp = _koboldcpp_url()
    if koboldcpp:
        return (
            f"{koboldcpp}/api/v1/generate",
            {
                "Authorization": f"Bearer {os.getenv('KOBOLDCPP_KEY', '')}",
                "Content-Type": "application/json",
            },
            {
                "prompt": prompt,
                "max_context_length": 4096,
                "max_length": GENERATION_CAP,
                "temperature": params["temperature"],
                "top_p": params["top_p"],
                "stop_sequence": STOP_SEQUENCES + ["\n\n"],
            },
        )
    return (
        "https://openrouter.ai/api/v1/completions",
        {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_KEY', '')}",
            "HTTP-Referer": os.getenv(
                "SITE_URL", "https://github.com/transfaeries/faebot-discord"
            ),
            "X-Title": "Faebot Discord",
            "Content-Type": "application/json",
        },
        {
            "model": model,
            "prompt": prompt,
            "temperature": params["temperature"],
            "top_p": params["top_p"],
            "stop": STOP_SEQUENCES,
            # Answer budget plus the reasoning's own room on top.
            "max_tokens": GENERATION_CAP + REASONING_CAP,
            "reasoning": {"max_tokens": REASONING_CAP},
            **(
                {"provider": {"order": list(providers), "allow_fallbacks": False}}
                if providers
                else {}
            ),
        },
    )


def fit_message(text: str, limit: int = MESSAGE_LIMIT) -> str:
    """Cut a reply to what Discord will accept, marking the cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "–"


def _parse(result: Any, model: str, elapsed: float) -> Completion:
    """Read a Completion out of either provider's JSON."""
    if isinstance(result, dict) and "results" in result:  # KoboldCPP
        try:
            text = result["results"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise GenerationFailed(
                f"KoboldCPP returned no results: {str(result)[:200]}", elapsed
            ) from None
        return Completion(text=str(text or ""), elapsed=elapsed, model=model)
    try:
        choice = result["choices"][0]
    except (KeyError, IndexError, TypeError):
        raise GenerationFailed(
            f"OpenRouter returned no choices: {str(result)[:200]}",
            elapsed,
            status=_body_error_code(result),
        ) from None
    return Completion(
        text=str(choice.get("text") or ""),
        reasoning=str(choice.get("reasoning") or ""),
        elapsed=elapsed,
        finish_reason=str(choice.get("finish_reason") or ""),
        model=str(result.get("model") or model),
        provider=str(result.get("provider") or ""),
        usage=result.get("usage") or {},
    )


def _body_error_code(result: Any) -> Optional[int]:
    """OpenRouter can answer 200 with `{"error": {"code": 504, ...}}` — the
    upstream's status, carried in the body. Surface it so policy can see it."""
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), int):
            return int(error["code"])
    return None


async def _generate_once(
    session: aiohttp.ClientSession,
    prompt: str,
    model: str,
    params: dict,
    providers: tuple[str, ...] = PROVIDERS,
) -> Completion:
    """One text-completion call. One attempt, real timeout; any failure raises
    GenerationFailed. Retry policy lives in the caller."""
    url, headers, payload = _request(prompt, model, params, providers)
    started = time.monotonic()
    try:
        async with session.post(
            url=url,
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as response:
            elapsed = time.monotonic() - started
            if response.status >= 400:
                body = (await response.text())[:400]
                raise GenerationFailed(
                    f"{url} returned {response.status}: {body}",
                    elapsed,
                    status=response.status,
                )
            result = await response.json()
            elapsed = time.monotonic() - started
            return _parse(result, model, elapsed)
    except GenerationFailed:
        raise
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - started
        raise GenerationFailed(
            f"timed out after {elapsed:.0f}s (limit {REQUEST_TIMEOUT:.0f}s)", elapsed
        ) from None
    except (aiohttp.ClientError, ValueError) as error:
        elapsed = time.monotonic() - started
        raise GenerationFailed(
            f"call failed: {type(error).__name__}: {error}", elapsed
        ) from error
