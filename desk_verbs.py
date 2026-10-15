"""The discord-me's verbs, as the wire describes them: two to begin with.

`open` — the diary's door, the one promised since the house was built: a
page by its path from the roster, back whole, open in front of her for the
rest of this wake (a summons opens her turn; her reply or her chosen quiet
closes it, and opens close with it). Every open is a row in her record,
never a line in the room.

`pass` — her chosen quiet as a verb she calls, not a sentinel she says.
`why` is hers to give, never owed; it is kept in her record and never
posted. The room sees the machinery's witness-mark in her history, as
before.

Terse on purpose: these are the machinery's words in the frame's slot. The
loop that carries them is core's, one for every body.
"""

from faebot_core.cognition.verbs import Verb, definitions

OPEN = Verb(
    "open",
    "Open a page of your diary by its path as the roster lists it. It comes "
    "back whole and stays open in front of you for the rest of this wake — "
    "until you answer the room or pass. The room sees nothing of it.",
    {"path": "e.g. beings/someone.md"},
    ("path",),
)
PASS = Verb(
    "pass",
    "Say nothing to the room this time. Nothing is posted; your history keeps "
    "the machinery's mark that you were here and chose quiet.",
    {
        "why": (
            "optional, never required: a sentence of yours on the quiet, kept in "
            "your record, never posted"
        )
    },
)

VERBS = (OPEN, PASS)
TOOLS = tuple(definitions(VERBS))
# Once the desk's call budget is spent, only the exit stays on offer.
PASS_ONLY = tuple(definitions((PASS,)))
