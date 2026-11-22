#!/usr/bin/env python3
"""Synthetic FL checks for bounded preset reads and Loop Starter bridge commands.

This fork removed preset selection, so plugin.select_preset is not tested here;
tests/test_bridge.py checks that the bridge refuses it.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, os.fspath(HERE / "fakefl"))

import _state  # noqa: E402


BRIDGE_PATH = ROOT / "fl_studio_mcp" / "_bridge" / "device_UniversalBridge.py"
_spec = importlib.util.spec_from_file_location("preset_bridge_test", BRIDGE_PATH)
assert _spec is not None and _spec.loader is not None
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)


def exhaust(generator: types.GeneratorType) -> tuple[dict, int]:
    """Drive a bridge generator as FL would, returning result and idle ticks."""

    yielded = 0
    while True:
        try:
            next(generator)
        except StopIteration as stopped:
            return stopped.value, yielded
        yielded += 1
        bridge._idle_tick += 1


def plugin(
    presets: list[str] | None = None,
    pads: list[dict[str, object]] | None = None,
) -> object:
    return _state.Plugin(
        "Synthetic Preset Plug",
        [("Gain", 0.5)],
        presets=presets,
        pads=pads,
    )


EFFECT = {
    "target_kind": "mixer_effect",
    "track": 3,
    "slot": 0,
    "use_global_index": False,
}
GENERATOR = {
    "target_kind": "channel_generator",
    "channel": 0,
    "slot": -1,
    "use_global_index": True,
    "index_scope": "global",
}


class PresetBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        _state.reset()
        bridge.MAX_PRESET_ENUMERATION = 4096
        bridge.LEAN_WRITES_ENABLED = True
        bridge._idle_tick = 0
        bridge._jobs[:] = []
        self.session = bridge.SESSION_FINGERPRINT

    def install_effect(
        self,
        presets: list[str] | None = None,
        pads: list[dict[str, object]] | None = None,
    ) -> object:
        selected = plugin(presets, pads)
        _state.TRACKS[3].slots[0] = selected
        return selected

    def install_generator(self, presets: list[str]) -> object:
        selected = plugin(presets)
        _state.CHANNELS[0].generator_plugin = selected
        return selected

    def test_reads_effect_and_global_generator_with_explicit_scopes(self) -> None:
        self.install_effect(["A", "B", "C"])
        result, ticks = exhaust(
            bridge.cmd_plugin_presets({**EFFECT, "start": 0, "limit": 3})
        )
        self.assertEqual(ticks, 0)
        self.assertEqual(result["track"], 3)
        self.assertFalse(result["use_global_index"])
        self.assertEqual([row["name"] for row in result["presets"]], ["A", "B", "C"])
        self.assertEqual(result["current_preset_name"], "A")
        self.assertEqual(result["current_preset_index"], 0)
        self.assertEqual(len(result["target_fingerprint"]), 64)

        self.install_generator(["Pad", "Lead"])
        result, _ = exhaust(
            bridge.cmd_plugin_current_preset(dict(GENERATOR))
        )
        self.assertEqual(result["channel"], 0)
        self.assertEqual(result["slot"], -1)
        self.assertTrue(result["use_global_index"])
        self.assertEqual(result["index_scope"], "global")
        self.assertEqual(result["current_preset_name"], "Pad")
        self.assertEqual(len(result["target_fingerprint"]), 64)

    def test_pagination_blank_duplicate_and_bounds_are_truthful(self) -> None:
        self.install_effect(["A", "", "B", "A"])
        result, _ = exhaust(
            bridge.cmd_plugin_presets(
                {**EFFECT, "start": 0, "limit": 4, "include_empty_names": False}
            )
        )
        self.assertEqual([row["index"] for row in result["presets"]], [0, 2, 3])
        self.assertEqual(result["blank_name_indices"], [1])
        self.assertEqual(result["duplicate_names"], ["A"])
        self.assertTrue(result["partial"] is False)
        with self.assertRaises(ValueError):
            exhaust(bridge.cmd_plugin_presets({**EFFECT, "limit": 0}))
        with self.assertRaises(ValueError):
            exhaust(bridge.cmd_plugin_presets({**EFFECT, "start": 5}))

    def test_large_catalog_preserves_current_name_but_not_unresolved_index(self) -> None:
        self.install_effect(["Current", "P1", "P2", "P3", "P4"])
        bridge.MAX_PRESET_ENUMERATION = 3
        result, _ = exhaust(
            bridge.cmd_plugin_presets({**EFFECT, "start": 0, "limit": 2})
        )
        self.assertEqual(result["current_preset_name"], "Current")
        self.assertIsNone(result["current_preset_index"])
        self.assertEqual(result["current_preset_status"], "stable")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["truncated_by"], "max_enumeration")

    def test_pad_map_is_generic_bounded_and_normalizes_values(self) -> None:
        self.install_effect(
            ["A"],
            [
                {"semitone": 36, "color": -1, "empty": False, "muted": True},
                {"semitone": 42, "color": 0x010203, "empty": True, "muted": False},
            ],
        )
        result, _ = exhaust(bridge.cmd_plugin_pad_map(dict(EFFECT)))
        self.assertTrue(result["complete"])
        self.assertEqual(result["pad_count"], 2)
        self.assertEqual(result["pads"][0]["color"], 0xFFFFFFFF)
        self.assertEqual(result["pads"][0]["semitone"], 36)
        self.assertEqual(result["pads"][0]["semitone_name"], "Note 36")
        self.assertTrue(result["pads"][1]["empty"])

    def test_preset_owned_user_label_does_not_change_target_identity(self) -> None:
        """Tested feature: a plug-in whose user label follows its preset keeps the same target identity when its preset changes.
        Check: with the label reporting the current preset, reading the current preset before and after the plug-in moves to another preset gives the new label and an unchanged target fingerprint.
        Mocks: the fake FL modules in tests/fakefl, with getPluginName patched so the user label is the current preset's name.
        Test would be deprecated if: target fingerprints stop excluding a plug-in's user label.
        """
        selected = self.install_effect(["A", "B", "C"])
        original_get_plugin_name = bridge.plugins.getPluginName

        def preset_owned_user_name(
            index: int,
            slot_index: int = -1,
            user_name: bool = False,
            use_global_index: bool = False,
        ) -> str:
            if user_name:
                return selected.presets[selected.current_preset]
            return original_get_plugin_name(
                index, slot_index, user_name, use_global_index
            )

        with mock.patch.object(
            bridge.plugins, "getPluginName", side_effect=preset_owned_user_name
        ):
            before, _ = exhaust(bridge.cmd_plugin_current_preset(dict(EFFECT)))
            # This fork cannot select presets; move the fake plug-in to its
            # third preset directly, as a user changing it in FL would.
            selected.current_preset = 2
            after, _ = exhaust(bridge.cmd_plugin_current_preset(dict(EFFECT)))

        self.assertEqual(before["plugin_user_name"], "A")
        self.assertEqual(after["plugin_user_name"], "C")
        self.assertEqual(after["current_preset_name"], "C")
        self.assertEqual(after["target_fingerprint"], before["target_fingerprint"])

    def test_loop_starter_is_explicit_global_single_dispatch_and_unverified(self) -> None:
        channel = 1
        before = bridge._channel_summary(channel)
        with mock.patch.object(
            bridge.channels,
            "rerollLoopStarterLoop",
            wraps=bridge.channels.rerollLoopStarterLoop,
        ) as reroll:
            result = bridge.cmd_channels_reroll_loop_starter_loop(
                {
                    "channel": channel,
                    "index_scope": "global",
                    "session_fingerprint": self.session,
                    "expected_before": {
                        "channel_fingerprint": before["channel_fingerprint"]
                    },
                }
            )
        self.assertEqual(reroll.call_count, 1)
        self.assertTrue(result["dispatched"])
        self.assertFalse(result["identity_verified"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["index_scope"], "global")
        self.assertEqual(
            getattr(_state.CHANNELS[channel], "loop_starter_rerolls", 0), 1
        )

        with self.assertRaises(ValueError):
            bridge.cmd_channels_reroll_loop_starter_loop(
                {
                    "channel": channel,
                    "index_scope": "global",
                    "expected_before": {"channel_fingerprint": "0" * 64},
                }
            )
        self.assertEqual(getattr(_state.CHANNELS[channel], "loop_starter_rerolls", 0), 1)


if __name__ == "__main__":
    unittest.main()
