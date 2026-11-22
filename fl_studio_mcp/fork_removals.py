"""Features this fork of PostFader removed, and the error that reports them.

This fork removes Piano Roll script generation (with the keyboard shortcut
that ran each generated script inside FL Studio) and plug-in preset selection.
Its MCP server registers no tool for either feature, and its FL Studio script
does not accept their commands. The server functions that would still reach
either feature raise ``FeatureRemovedError`` with one of the messages below,
and Production Runs and sound palettes refuse plans that need them before
anything is written.

The fork's live bridge to FL Studio also uses only MIDI; that change needs no
error of its own, because a client without MIDI has no transport and reports
the existing not-connected error.
"""

from __future__ import annotations


class FeatureRemovedError(RuntimeError):
    """Raised by a code path whose feature this fork of PostFader removed."""


PIANO_ROLL_REMOVED = (
    "Piano Roll scripting is removed in this fork of PostFader: it writes no "
    "Piano Roll scripts, sends no Piano Roll run-script shortcut, and cannot "
    "read or write Piano Roll notes."
)

PRESET_SELECTION_REMOVED = (
    "Plug-in preset selection is removed in this fork of PostFader: it does "
    "not select plug-in presets or drum kits."
)

# Every value of SoundPaletteAssignment.selection_action, and the ones this
# fork can still apply. Palette application allows only the listed actions,
# so an action added upstream is refused until it is reviewed here.
KNOWN_SELECTION_ACTIONS = frozenset({"keep_current", "select_preset", "loop_starter_reroll"})
APPLICABLE_SELECTION_ACTIONS = frozenset({"keep_current", "loop_starter_reroll"})


def check_selection_actions(actions: frozenset[str] | set[str]) -> None:
    """Fail loudly when the palette model's selection actions differ from the reviewed set."""

    if set(actions) != KNOWN_SELECTION_ACTIONS:
        # The hint below asks a maintainer to review and edit this module, a
        # code change no test can make; the guard's own test checks that a
        # matching set passes and a differing one raises.
        raise RuntimeError(
            "SoundPaletteAssignment.selection_action values %s differ from the "
            "set this fork reviewed, %s; review fork_removals.py before using "
            "them" % (sorted(actions), sorted(KNOWN_SELECTION_ACTIONS))
        )
