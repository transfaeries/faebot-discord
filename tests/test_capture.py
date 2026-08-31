"""Capture serialisation — the small senses (alt text, who-reacts, embeds).

capture.py had no tests before these; they cover the serialisers, not the
background writer (which is fire-and-forget by design and swallows failures).
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import capture


def make_attachment(**overrides):
    attachment = SimpleNamespace(
        filename="photo.png",
        content_type="image/png",
        size=1234,
        description=None,
        title=None,
        width=640,
        height=480,
        duration=None,
        url="https://cdn.example/photo.png",
    )
    attachment.is_spoiler = lambda: False
    for key, value in overrides.items():
        setattr(attachment, key, value)
    return attachment


def make_message(**overrides):
    message = SimpleNamespace(
        id=1,
        content="hello",
        author=SimpleNamespace(
            id=2, name="user", display_name="User", bot=False, system=False
        ),
        channel=SimpleNamespace(id=3, name="general", type="text", guild=None),
        guild=None,
        created_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        edited_at=None,
        webhook_id=None,
        type="MessageType.default",
        reference=None,
        mentions=[],
        role_mentions=[],
        channel_mentions=[],
        mention_everyone=False,
        attachments=[],
        embeds=[],
        stickers=[],
        reactions=[],
    )
    for key, value in overrides.items():
        setattr(message, key, value)
    return message


class TestAttachmentSenses:
    def test_alt_text_is_captured_when_offered(self):
        message = make_message(
            attachments=[make_attachment(description="a cat on a windowsill")]
        )
        [attachment] = capture.serialize_message(message)["attachments"]
        assert attachment["description"] == "a cat on a windowsill"

    def test_a_missing_description_is_captured_as_none_not_dropped(self):
        # None (field live, nothing offered) must reach the row — downstream
        # renders it as a labeled hole, distinct from pre-sense rows that
        # lack the key entirely.
        message = make_message(attachments=[make_attachment()])
        [attachment] = capture.serialize_message(message)["attachments"]
        assert "description" in attachment
        assert attachment["description"] is None

    def test_voice_message_duration_and_spoiler_ride_along(self):
        voice_note = make_attachment(
            filename="voice-message.ogg",
            content_type="audio/ogg",
            duration=4.2,
        )
        voice_note.is_spoiler = lambda: True
        [attachment] = capture.serialize_message(
            make_message(attachments=[voice_note])
        )["attachments"]
        assert attachment["duration_secs"] == 4.2
        assert attachment["spoiler"] is True
        assert attachment["url"] == "https://cdn.example/photo.png"


class TestEmbedCapture:
    def test_embeds_capture_their_content_not_just_a_count(self):
        embed = SimpleNamespace(
            type="link",
            title="An article",
            description="Its preview text",
            url="https://example.org/article",
            provider=SimpleNamespace(name="Example News"),
        )
        serialized = capture.serialize_message(make_message(embeds=[embed]))
        assert serialized["embeds"] == [
            {
                "type": "link",
                "title": "An article",
                "description": "Its preview text",
                "url": "https://example.org/article",
                "provider": "Example News",
            }
        ]


class TestWhoReacts:
    def _recorded(self, monkeypatch, reactor):
        rows = []
        monkeypatch.setattr(
            capture, "record", lambda kind, payload: rows.append((kind, payload))
        )
        monkeypatch.setattr(capture, "is_enabled", lambda: True)
        payload = SimpleNamespace(
            user_id=42, message_id=7, channel_id=3, guild_id=9, emoji=Mock()
        )
        capture.record_reaction(payload, "reaction_add", reactor=reactor)
        return rows[0][1]

    def test_the_reactor_is_captured_when_resolved(self, monkeypatch):
        reactor = SimpleNamespace(
            id=42, name="berry", display_name="Berry", bot=False, system=False
        )
        payload = self._recorded(monkeypatch, reactor)
        assert payload["user"]["display_name"] == "Berry"
        assert payload["user_id"] == 42

    def test_an_unresolved_reactor_stays_an_honest_none(self, monkeypatch):
        payload = self._recorded(monkeypatch, None)
        assert payload["user"] is None
        assert payload["user_id"] == 42
