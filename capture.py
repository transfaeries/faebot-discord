"""Spike 01 (faebot-core) — Discord capture tap.

A *thin, faithful, opt-in, maximalist* recorder that appends raw Discord events
to the `captured_events` Postgres table so we can transduce them into faebot-core
`Observation`s offline (in faebot-private/snippets/discord/). It is the sibling of
faebot-twitch's capture tap and the in-process successor to the standalone
spike01_listener: record everything the surface gives us, reason about none of it
here — reconciliation is faebot's cognition, not the adapter's.

Design rules (load-bearing — this runs inside the LIVE bot on fly):
  * **Opt-in.** Capture happens only when SPIKE_CAPTURE is set (any non-empty
    value). Unset = every function is a no-op, so the live bot is completely
    unaffected unless we deliberately turn it on. SPIKE_CAPTURE_RAW=0 additionally
    disables the raw gateway-frame catch-all if its volume gets silly.
  * **Never breaks the bot.** Every extraction + write is wrapped; failures are
    swallowed and logged at debug. Writes are fired as background tasks so a slow
    or dormant database can never delay faebot's reply path.
  * **Faithful & maximalist.** We record raw fields verbatim, drop nothing, and
    interpret nothing. The raw socket catch-all captures gateway events we never
    coded for — the bitter-lesson discipline.
  * **Capture-only.** Nothing recorded here feeds the prompt the live bot sends
    to its model. This is corpus accumulation for offline spikes, not cognition.
  * **Append-only.** Rows are inserted, never updated; the BIGSERIAL id is the
    watermark the offline pull script pages through.

Payload shapes deliberately match spike01_listener's JSONL rows, so the offline
pull script can reconstruct byte-shape-identical lines for transduce.py.
"""

import asyncio
import datetime
import json
import logging
import os
from typing import Any, Dict, Optional


CAPTURE_ENABLED = bool(os.getenv("SPIKE_CAPTURE", ""))
RAW_ENABLED = CAPTURE_ENABLED and os.getenv("SPIKE_CAPTURE_RAW", "1") != "0"

# The database handle is injected at startup (see init) to avoid a circular import.
_database: Optional[Any] = None

# Fire-and-forget insert tasks — referenced here so they aren't garbage-collected.
_pending_writes: set = set()


def init(database) -> None:
    """Give the tap its database handle (a FaebotDatabase). Called once at startup."""
    global _database
    _database = database
    logging.info(
        "capture: %s (raw catch-all: %s)",
        "ENABLED — recording to captured_events" if CAPTURE_ENABLED else "off",
        "on" if RAW_ENABLED else "off",
    )


def is_enabled() -> bool:
    return CAPTURE_ENABLED and _database is not None


# --- the writer ---------------------------------------------------------------


def record(kind: str, payload: Dict[str, Any]) -> None:
    """Queue one raw event row. `kind` names the surface event (message,
    message_edit, faebot_message, socket_raw, ...); `payload` carries the raw
    surface attributes verbatim. The timestamp is stamped here, synchronously,
    so ordering survives the background write."""
    if not is_enabled():
        return
    try:
        captured_at = datetime.datetime.now(datetime.timezone.utc)
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        task = asyncio.get_running_loop().create_task(
            _write(kind, captured_at, payload_json)
        )
        _pending_writes.add(task)
        task.add_done_callback(_pending_writes.discard)
    except Exception as error:
        # Capture must never disturb the bot — log and move on.
        logging.debug(
            "capture record failed (%s): %s: %s", kind, type(error).__name__, error
        )


async def _write(kind: str, captured_at: datetime.datetime, payload_json: str) -> None:
    try:
        if _database is None:  # is_enabled() already checked; this appeases mypy
            return
        await _database.save_captured_event(kind, captured_at, payload_json)
    except Exception as error:
        logging.debug(
            "capture write failed (%s): %s: %s", kind, type(error).__name__, error
        )


# --- serialisers (lifted from spike01_listener — proven shapes) -----------------


def serialize_user(user: Any) -> Optional[Dict[str, Any]]:
    if user is None:
        return None
    return {
        "id": getattr(user, "id", None),
        "name": getattr(user, "name", None),
        "display_name": getattr(user, "display_name", None),
        "bot": getattr(user, "bot", None),
        "system": getattr(user, "system", None),
    }


def serialize_channel(channel: Any) -> Optional[Dict[str, Any]]:
    if channel is None:
        return None
    return {
        "id": getattr(channel, "id", None),
        "name": getattr(channel, "name", None),
        "type": str(getattr(channel, "type", None)),
        "guild_id": getattr(getattr(channel, "guild", None), "id", None),
    }


def serialize_emoji(emoji: Any) -> Dict[str, Any]:
    return {
        "name": getattr(emoji, "name", None),
        "id": getattr(emoji, "id", None),
        "custom": getattr(emoji, "id", None) is not None,
    }


def serialize_message(message: Any) -> Dict[str, Any]:
    return {
        "id": message.id,
        "content": message.content,
        "author": serialize_user(message.author),
        "channel": serialize_channel(message.channel),
        "guild_id": getattr(message.guild, "id", None),
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        # webhook_id is set for PluralKit / webhook reposts — the proxy churn.
        "webhook_id": message.webhook_id,
        "type": str(message.type),
        "reply_to": getattr(message.reference, "message_id", None)
        if message.reference
        else None,
        "mentions": [user.id for user in message.mentions],
        "role_mentions": [role.id for role in message.role_mentions],
        "channel_mentions": [channel.id for channel in message.channel_mentions],
        "mention_everyone": message.mention_everyone,
        "attachments": [
            {
                "filename": attachment.filename,
                "content_type": attachment.content_type,
                "size": attachment.size,
            }
            for attachment in message.attachments
        ],
        "embeds": len(message.embeds),
        "stickers": [sticker.name for sticker in message.stickers],
        "reactions": [
            {"emoji": str(reaction.emoji), "count": reaction.count}
            for reaction in message.reactions
        ],
    }


# --- recorders (one per surface event; all no-ops unless enabled) ---------------


def record_message(message: Any) -> None:
    """Every incoming message, called at the very top of on_message — BEFORE the
    self-check and all filtering, so we also capture faebot's own echo (the
    from-the-outside view of faer speech), proxy webhooks, and dotted messages."""
    if not is_enabled():
        return
    try:
        record("message", {"message": serialize_message(message)})
    except Exception as error:
        logging.debug("capture message failed: %s: %s", type(error).__name__, error)


def record_message_edit(payload: Any) -> None:
    """Raw edit event — fires even for uncached messages. The gateway payload
    carries the new content; cached_before (when we have it) carries the old."""
    if not is_enabled():
        return
    try:
        cached = payload.cached_message
        record(
            "message_edit",
            {
                "message_id": payload.message_id,
                "channel_id": payload.channel_id,
                "guild_id": payload.guild_id,
                "cached_before": serialize_message(cached) if cached else None,
                "raw_data": payload.data,
            },
        )
    except Exception as error:
        logging.debug("capture edit failed: %s: %s", type(error).__name__, error)


def record_message_delete(payload: Any) -> None:
    """Raw delete event — the PluralKit churn's deleted originals show up here
    (their content was already captured at on_message time, so an id suffices)."""
    if not is_enabled():
        return
    try:
        cached = payload.cached_message
        record(
            "message_delete",
            {
                "message_id": payload.message_id,
                "channel_id": payload.channel_id,
                "guild_id": payload.guild_id,
                "cached_before": serialize_message(cached) if cached else None,
            },
        )
    except Exception as error:
        logging.debug("capture delete failed: %s: %s", type(error).__name__, error)


def record_reaction(payload: Any, action: str) -> None:
    """Raw reaction add/remove. `action` is "reaction_add" or "reaction_remove"."""
    if not is_enabled():
        return
    try:
        record(
            action,
            {
                "user_id": payload.user_id,
                "message_id": payload.message_id,
                "channel_id": payload.channel_id,
                "guild_id": payload.guild_id,
                "emoji": serialize_emoji(payload.emoji),
            },
        )
    except Exception as error:
        logging.debug("capture reaction failed: %s: %s", type(error).__name__, error)


def record_typing(channel: Any, user: Any, when: Any) -> None:
    """The typing signal — noise, but real lived signal (see spike 01 findings)."""
    if not is_enabled():
        return
    try:
        record(
            "typing",
            {
                "guild_id": getattr(getattr(channel, "guild", None), "id", None),
                "channel": serialize_channel(channel),
                "user": serialize_user(user),
                "when": when.isoformat() if hasattr(when, "isoformat") else str(when),
            },
        )
    except Exception as error:
        logging.debug("capture typing failed: %s: %s", type(error).__name__, error)


def record_member(member: Any, action: str) -> None:
    """Member join/leave. `action` is "member_join" or "member_remove"."""
    if not is_enabled():
        return
    try:
        record(
            action,
            {"guild_id": member.guild.id, "member": serialize_user(member)},
        )
    except Exception as error:
        logging.debug("capture member failed: %s: %s", type(error).__name__, error)


def record_faebot_message(
    sent_message: Any,
    conversation_id: str,
    prompt: str,
    model: str,
    context: Any,
) -> None:
    """faebot's own generated reply, captured at the send point — the only place
    faer INTERNAL metadata exists (the prompt that produced it, the model, the
    context). The gateway echo of the same message is captured separately by
    record_message; the two views link offline by message id."""
    if not is_enabled():
        return
    try:
        record(
            "faebot_message",
            {
                "message_id": getattr(sent_message, "id", None),
                "channel": serialize_channel(getattr(sent_message, "channel", None)),
                "content": getattr(sent_message, "content", None),
                "conversation_id": conversation_id,
                "prompt": prompt,
                "model": model,
                "context": context,
            },
        )
    except Exception as error:
        logging.debug(
            "capture faebot_message failed: %s: %s", type(error).__name__, error
        )


def record_socket_raw(frame: Any) -> None:
    """Catch-all: every gateway dispatch frame discord.py receives (needs the
    client's enable_debug_events). Guarantees nothing we didn't anticipate slips
    past — event types with no handler above still land here, verbatim. Skips
    non-dispatch protocol frames (heartbeats etc., op != 0) — the PING/PONG rule."""
    if not RAW_ENABLED or not is_enabled():
        return
    try:
        if isinstance(frame, bytes):
            return  # compressed/partial transport frames — not a dispatch event
        parsed = json.loads(frame)
        if parsed.get("op") != 0:
            return  # protocol keepalive/handshake, no perceptual content
        record("socket_raw", {"frame": parsed})
    except Exception as error:
        logging.debug("capture socket_raw failed: %s: %s", type(error).__name__, error)
