"""Features this fork of PostFader removed, and the error that reports them.

This fork removes Piano Roll script generation (with the keyboard shortcut
that ran each generated script inside FL Studio) and plug-in preset selection.
Its MCP server registers no tool for either feature, and its FL Studio script
does not accept their commands. The server functions that would still reach
either feature raise ``FeatureRemovedError`` with one of the messages below,
and Production Runs and sound palettes refuse plans that need them before
anything is written.

The fork also reaches FL Studio only over MIDI; that change needs no error of
its own, because a client without MIDI has no transport and reports the
existing not-connected error.
"""

from __future__ import annotations


class FeatureRemovedError(RuntimeError):
    """Raised by a code path whose feature this fork of PostFader removed."""


PIANO_ROLL_REMOVED = (
    "Piano Roll scripting is removed in this fork of PostFader: it does not "
    "write Piano Roll scripts, send keyboard shortcuts to FL Studio, or read "
    "or write Piano Roll notes."
)

PRESET_SELECTION_REMOVED = (
    "Plug-in preset selection is removed in this fork of PostFader: it does "
    "not select plug-in presets or drum kits."
)
