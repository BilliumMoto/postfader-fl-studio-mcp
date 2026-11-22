"""Deterministic tests for the v0.20 creative and arrangement pack."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE / "fakefl"))
sys.path.insert(0, str(ROOT / "fl_studio_mcp" / "_bridge"))
sys.path.insert(0, str(ROOT))

import _state  # noqa: E402
import device_UniversalBridge as bridge  # noqa: E402

from fl_studio_mcp import creative as creative_module  # noqa: E402
from fl_studio_mcp.bridge_install import expected_bridge_deployment  # noqa: E402
from fl_studio_mcp.fork_removals import (  # noqa: E402
    PIANO_ROLL_REMOVED,
    FeatureRemovedError,
)
from fl_studio_mcp.creative import (  # noqa: E402
    PIANO_ROLL,
    CreativeNote,
    HotkeyDispatch,
    MidiTrackSpec,
    PianoRollTargetReceipt,
    PianoRollTransform,
    SectionMarker,
    add_section_markers,
    compose_bassline,
    compose_chord_progression,
    compose_drums,
    compose_melody,
    export_type1_midi,
    piano_roll_scripts_directory,
    prepare_empty_pattern,
    record_automation_value,
    transform_piano_roll,
    write_piano_roll_notes,
)
from fl_studio_mcp.music_analysis import (  # noqa: E402
    analyze_tempo_and_key,
    transcribe_monophonic,
)
from fl_studio_mcp.sound_selection.models import (  # noqa: E402
    DrumPadMap,
    DrumRoleMapping,
)
from fl_studio_mcp.verified_writer import VerifiedWritesUnavailable  # noqa: E402


bridge.BRIDGE_SOURCE_SHA256 = expected_bridge_deployment()[1]


class DirectFakeClient:
    transport = "tcp"

    def ping(self):
        return bridge.cmd_ping({})

    def call(self, command: str, **arguments):
        result = bridge.HANDLERS[command](arguments)
        if isinstance(result, types.GeneratorType):
            while True:
                try:
                    next(result)
                except StopIteration as stopped:
                    return stopped.value
        return result


def target_receipt() -> PianoRollTargetReceipt:
    return PianoRollTargetReceipt(
        command="creative.prepare_piano_roll",
        channel_index=2,
        pattern_number=3,
        before_channel_indices=[0],
        after_channel_indices=[2],
        before_pattern_number=1,
        after_pattern_number=3,
        piano_roll_visible_before=False,
        piano_roll_visible_after=True,
        selected_target_verified=True,
        piano_roll_visibility_verified=True,
        session_fingerprint="a" * 32,
    )


def run_prepared_piano_roll_script(script_path: str) -> None:
    source = Path(script_path).read_text(encoding="ascii")
    exec(compile(source, "Postfader_Apply.pyscript", "exec"), {})


class CreativeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._bridge_write_state = (
            bridge.LEAN_WRITES_ENABLED,
            bridge.WRITE_MODE_ORIGIN,
        )
        _state.reset()
        bridge.LEAN_WRITES_ENABLED = True
        bridge.WRITE_MODE_ORIGIN = "runtime_request"
        with PIANO_ROLL._lock:
            PIANO_ROLL._prepared = False
            PIANO_ROLL._armed = False
            PIANO_ROLL._last_request_id = None
            PIANO_ROLL._last_count = None
            PIANO_ROLL._last_digest = None
            PIANO_ROLL._last_operation = None
            PIANO_ROLL._arm_request_id = None
            PIANO_ROLL._arm_receipt_secret = None
            PIANO_ROLL._arm_receipt_path = None

    def tearDown(self) -> None:
        bridge.LEAN_WRITES_ENABLED, bridge.WRITE_MODE_ORIGIN = (
            self._bridge_write_state
        )

    @staticmethod
    def _drum_map(*roles: str) -> DrumPadMap:
        pitches = {
            "kick": 48,
            "snare": 49,
            "closed_hat": 50,
            "open_hat": 51,
        }
        return DrumPadMap(
            map_id="test-drum-map",
            pad_count=len(roles),
            mappings=tuple(
                DrumRoleMapping(
                    role=role,
                    pad_index=index,
                    midi_note=pitches[role],
                    confidence=1.0,
                    source="user_explicit",
                )
                for index, role in enumerate(roles)
            ),
        )

    def test_composition_is_deterministic_and_scale_bounded(self) -> None:
        chords = compose_chord_progression(
            ["I", "vi", "IV", "V7"], root="C", collection="major"
        )
        self.assertEqual(chords.note_count, 13)
        self.assertEqual(chords.duration_beats, 16.0)
        self.assertTrue(all(note.pitch % 12 in {0, 2, 4, 5, 7, 9, 11} for note in chords.notes))

        first = compose_melody(
            root="D", collection="dorian", bars=8, seed=493, contour="arch"
        )
        second = compose_melody(
            root="D", collection="dorian", bars=8, seed=493, contour="arch"
        )
        different = compose_melody(
            root="D", collection="dorian", bars=8, seed=494, contour="arch"
        )
        self.assertEqual(first.note_digest_sha256, second.note_digest_sha256)
        self.assertNotEqual(first.note_digest_sha256, different.note_digest_sha256)
        self.assertTrue(first.notes)

        bass = compose_bassline(
            ["i", "VI", "III", "VII"],
            root="A",
            collection="natural_minor",
            style="walking",
            seed=11,
        )
        drums = compose_drums(style="dnb", bars=4, seed=11, swing=0.08)
        self.assertTrue(bass.notes)
        self.assertGreater(drums.note_count, 40)
        self.assertIn("General MIDI", drums.warnings[0])

    def test_typed_drum_map_retargets_roles_without_general_midi_warning(self) -> None:
        drum_map = self._drum_map("kick", "snare", "closed_hat", "open_hat")
        first = compose_drums(style="house", bars=2, seed=11, drum_map=drum_map)
        second = compose_drums(style="house", bars=2, seed=11, drum_map=drum_map)

        self.assertEqual(first.note_digest_sha256, second.note_digest_sha256)
        self.assertEqual(first.warnings, [])
        self.assertTrue({48, 49, 50, 51}.issubset({note.pitch for note in first.notes}))
        self.assertTrue(all(note.pitch not in {36, 38, 42, 46} for note in first.notes))
        trap = compose_drums(
            style="trap",
            bars=1,
            seed=11,
            drum_map=self._drum_map("kick", "snare", "closed_hat"),
        )
        self.assertTrue(trap.notes)
        self.assertNotIn(51, {note.pitch for note in trap.notes})

    def test_typed_drum_map_requires_roles_used_by_style_without_fallback(self) -> None:
        with self.assertRaisesRegex(ValueError, "closed_hat"):
            compose_drums(
                style="dnb",
                bars=1,
                drum_map=self._drum_map("kick", "snare"),
            )

        with self.assertRaisesRegex(ValueError, "open_hat"):
            compose_drums(
                style="house",
                bars=1,
                drum_map=self._drum_map("kick", "snare", "closed_hat"),
            )

        with self.assertRaises(TypeError):
            compose_drums(
                style="dnb",
                bars=1,
                drum_map={"kick": 48},  # type: ignore[arg-type]
            )

    def test_type1_midi_export_is_reopened_and_event_verified(self) -> None:
        track = MidiTrackSpec(
            name="Lead",
            channel=2,
            notes=[
                CreativeNote(pitch=60, start_beats=0.0, duration_beats=1.0),
                CreativeNote(pitch=64, start_beats=1.0, duration_beats=0.5),
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "composition.mid")
            receipt = export_type1_midi(path, [track], tempo_bpm=123.0, ppq=960)
            self.assertTrue(receipt.verified)
            self.assertTrue(receipt.header_verified)
            self.assertTrue(receipt.note_events_verified)
            self.assertEqual(receipt.track_count, 2)
            self.assertEqual(receipt.note_count, 2)
            self.assertEqual(receipt.sha256, receipt.readback_sha256)
            with self.assertRaises(FileExistsError):
                export_type1_midi(path, [track])
            replaced = export_type1_midi(path, [track], overwrite=True)
            self.assertTrue(replaced.overwritten_existing_file)

    def test_type1_midi_create_only_export_wins_one_concurrent_race(self) -> None:
        tracks = [
            MidiTrackSpec(
                name="First",
                channel=1,
                notes=[CreativeNote(pitch=60, start_beats=0.0, duration_beats=1.0)],
            ),
            MidiTrackSpec(
                name="Second",
                channel=2,
                notes=[CreativeNote(pitch=67, start_beats=0.0, duration_beats=1.0)],
            ),
        ]
        barrier = threading.Barrier(2)
        from fl_studio_mcp import creative as creative_module

        real_midi_bytes = creative_module._midi_bytes

        def synchronized_midi_bytes(*args, **kwargs):
            barrier.wait(timeout=5)
            return real_midi_bytes(*args, **kwargs)

        def export(track: MidiTrackSpec, path: str):
            try:
                return "ok", export_type1_midi(
                    path,
                    [track],
                    tempo_bpm=111.0 if track.name == "First" else 137.0,
                    overwrite=False,
                )
            except Exception as exc:  # collect the race outcome in the caller
                return "error", exc

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "concurrent.mid")
            with mock.patch(
                "fl_studio_mcp.creative._midi_bytes",
                side_effect=synchronized_midi_bytes,
            ):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(export, track, path) for track in tracks]
                    outcomes = [future.result(timeout=10) for future in futures]

            successes = [value for kind, value in outcomes if kind == "ok"]
            failures = [value for kind, value in outcomes if kind == "error"]
            self.assertEqual(len(successes), 1, outcomes)
            self.assertEqual(len(failures), 1, outcomes)
            self.assertIsInstance(failures[0], FileExistsError)
            winner = successes[0]
            self.assertFalse(winner.overwritten_existing_file)
            self.assertEqual(
                hashlib.sha256(Path(path).read_bytes()).hexdigest(), winner.sha256
            )

    def test_type1_midi_create_only_reports_unsupported_filesystem(self) -> None:
        track = MidiTrackSpec(
            name="Lead",
            channel=1,
            notes=[CreativeNote(pitch=60, start_beats=0.0, duration_beats=1.0)],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsupported.mid"
            with mock.patch(
                "fl_studio_mcp.creative.os.link",
                side_effect=OSError("hard links unavailable"),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "filesystem must support same-directory hard links",
                ):
                    export_type1_midi(str(path), [track], overwrite=False)
            self.assertFalse(path.exists())

    def test_piano_roll_receipt_uses_exclusive_direct_final_binary_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "arm.json"
            source = creative_module._bootstrap_script(
                request_id="a" * 32,
                receipt_path=receipt_path,
                receipt_secret="b" * 64,
            )
            self.assertIn('open(RECEIPT_PATH, "xb")', source)
            self.assertNotIn("os.replace", source)
            self.assertNotIn("os.rename", source)
            self.assertNotIn("os.link", source)
            exec(compile(source, "Postfader_Apply.pyscript", "exec"), {})

            self.assertTrue(receipt_path.is_file())
            self.assertFalse(Path(f"{receipt_path}.tmp").exists())
            with self.assertRaises(FileExistsError):
                exec(compile(source, "Postfader_Apply.pyscript", "exec"), {})

    def test_piano_roll_receipt_wait_retries_partial_or_malformed_receipts(self) -> None:
        notes = [CreativeNote(pitch=60, start_beats=0, duration_beats=1)]
        digest = creative_module.note_digest(notes)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_bytes(b"{")
            expected_evidence = object()
            with (
                mock.patch.object(
                    creative_module,
                    "_read_piano_roll_receipt",
                    side_effect=[(None, "partial receipt"), (expected_evidence, None)],
                ),
                mock.patch.object(creative_module.time, "sleep"),
            ):
                evidence, error = creative_module._await_piano_roll_receipt(
                    path,
                    receipt_secret="4" * 64,
                    request_id="3" * 32,
                    requested_note_digest=digest,
                    notes=notes,
                    mode="replace",
                )

            self.assertIs(evidence, expected_evidence)
            self.assertIsNone(error)

            expected_persistence = object()
            with (
                mock.patch.object(
                    creative_module,
                    "_read_persistence_receipt",
                    side_effect=[
                        (None, "malformed persistence receipt"),
                        (expected_persistence, None),
                    ],
                ),
                mock.patch.object(creative_module.time, "sleep"),
            ):
                persistence, error = creative_module._await_persistence_receipt(
                    path,
                    receipt_secret="4" * 64,
                    request_id="3" * 32,
                    expected_score_note_count=1,
                    expected_score_digest="5" * 64,
                    expected_ppq=96,
                )

            self.assertIs(persistence, expected_persistence)
            self.assertIsNone(error)

            with (
                mock.patch.object(
                    creative_module,
                    "_read_arm_receipt",
                    side_effect=[(False, "partial arming receipt"), (True, None)],
                ),
                mock.patch.object(creative_module.time, "sleep"),
            ):
                armed, error = creative_module._await_arm_receipt(
                    path, receipt_secret="4" * 64, request_id="3" * 32
                )

            self.assertTrue(armed)
            self.assertIsNone(error)

    def test_piano_roll_receipt_wait_reports_last_validation_error(self) -> None:
        notes = [CreativeNote(pitch=60, start_beats=0, duration_beats=1)]
        digest = creative_module.note_digest(notes)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_bytes(b"malformed")
            with (
                mock.patch.object(
                    creative_module,
                    "PIANO_ROLL_RECEIPT_WAIT_SECONDS",
                    0.0,
                ),
                mock.patch.object(
                    creative_module,
                    "_read_piano_roll_receipt",
                    return_value=(None, "latest validation error"),
                ),
            ):
                evidence, error = creative_module._await_piano_roll_receipt(
                    path,
                    receipt_secret="4" * 64,
                    request_id="3" * 32,
                    requested_note_digest=digest,
                    notes=notes,
                    mode="replace",
                )

        self.assertIsNone(evidence)
        self.assertEqual(error, "latest validation error")

    def test_piano_roll_directory_reuses_platform_aware_user_data_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_data = Path(directory) / "Redirected FL Data"
            explicit_scripts = Path(directory) / "Explicit scripts"
            with mock.patch.dict(
                os.environ,
                {
                    "FL_STUDIO_USER_DATA_DIR": os.fspath(user_data),
                    "POSTFADER_PIANO_ROLL_SCRIPTS_DIR": "",
                },
                clear=False,
            ):
                self.assertEqual(
                    piano_roll_scripts_directory(),
                    (
                        user_data
                        / "Settings"
                        / "Piano roll scripts"
                        / "Postfader"
                    ).resolve(),
                )
            with mock.patch.dict(
                os.environ,
                {
                    "FL_STUDIO_USER_DATA_DIR": os.fspath(user_data),
                    "POSTFADER_PIANO_ROLL_SCRIPTS_DIR": os.fspath(
                        explicit_scripts
                    ),
                },
                clear=False,
            ):
                self.assertEqual(
                    piano_roll_scripts_directory(), explicit_scripts.resolve()
                )
            with mock.patch.dict(
                os.environ,
                {"POSTFADER_PIANO_ROLL_SCRIPTS_DIR": "relative/scripts"},
                clear=False,
            ):
                with self.assertRaisesRegex(ValueError, "must be an absolute"):
                    piano_roll_scripts_directory()

    def test_piano_roll_script_runtime_receipt_verifies_exact_score_delta(self) -> None:
        class FakeNote:
            def __init__(self) -> None:
                self.number = 60
                self.time = 0
                self.length = 96
                self.velocity = 0.8
                self.pan = 0.5
                self.release = 0.5
                self.color = 0
                self.pitchofs = 0
                self.slide = False
                self.porta = False
                self.muted = False
                self.selected = True

        class FakeScore:
            PPQ = 96

            def __init__(self) -> None:
                existing = FakeNote()
                existing.number = 48
                self.notes = [existing]

            @property
            def noteCount(self) -> int:
                return len(self.notes)

            def getNote(self, index: int) -> FakeNote:
                return self.notes[index]

            def addNote(self, note: FakeNote) -> None:
                # FL treats editor selection as transient UI state and clears
                # it after the note is committed to the score.
                note.selected = False
                self.notes.append(note)

            def clearNotes(self, _all: bool = False) -> None:
                self.notes.clear()

        notes = [
            CreativeNote(
                pitch=67,
                start_beats=0.5,
                duration_beats=1.25,
                velocity=0.73,
                pan=0.4,
            ),
            CreativeNote(pitch=71, start_beats=2.0, duration_beats=0.5),
        ]
        request_id = "1" * 32
        receipt_secret = "2" * 64
        requested_digest = creative_module.note_digest(notes)
        fake_module = types.ModuleType("flpianoroll")
        fake_module.Note = FakeNote
        fake_module.score = FakeScore()

        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "receipt.json"
            source = creative_module._notes_script(
                notes,
                "append",
                request_id,
                requested_note_digest=requested_digest,
                receipt_path=receipt_path,
                receipt_secret=receipt_secret,
            )
            self.assertIn('open(RECEIPT_PATH, "xb")', source)
            self.assertNotIn("os.replace", source)
            self.assertNotIn("os.rename", source)
            self.assertNotIn("os.link", source)
            with mock.patch.dict(sys.modules, {"flpianoroll": fake_module}):
                exec(compile(source, "Postfader_Apply.pyscript", "exec"), {})
            evidence, error = creative_module._read_piano_roll_receipt(
                receipt_path,
                receipt_secret=receipt_secret,
                request_id=request_id,
                requested_note_digest=requested_digest,
                notes=notes,
                mode="append",
            )
            assert evidence is not None
            verification_path = Path(directory) / "persistence.json"
            verification_source = creative_module._persistence_script(
                request_id=request_id,
                expected_score_note_count=evidence.score_note_count,
                expected_score_digest=evidence.score_digest_sha256,
                receipt_path=verification_path,
                receipt_secret=receipt_secret,
            )
            self.assertIn('open(RECEIPT_PATH, "xb")', verification_source)
            self.assertNotIn("os.replace", verification_source)
            self.assertNotIn("os.rename", verification_source)
            self.assertNotIn("os.link", verification_source)
            with mock.patch.dict(sys.modules, {"flpianoroll": fake_module}):
                exec(
                    compile(
                        verification_source,
                        "Postfader_Apply.pyscript",
                        "exec",
                    ),
                    {},
                )
            persistence, persistence_error = (
                creative_module._read_persistence_receipt(
                    verification_path,
                    receipt_secret=receipt_secret,
                    request_id=request_id,
                    expected_score_note_count=evidence.score_note_count,
                    expected_score_digest=evidence.score_digest_sha256,
                    expected_ppq=evidence.ppq,
                )
            )

        self.assertIsNone(error)
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertTrue(evidence.script_completed)
        self.assertTrue(evidence.postcondition_verified)
        self.assertEqual(evidence.before_note_count, 1)
        self.assertEqual(evidence.score_note_count, 3)
        self.assertEqual(evidence.ppq, 96)
        self.assertIsNone(persistence_error)
        self.assertIsNotNone(persistence)
        assert persistence is not None
        self.assertTrue(persistence.persistence_check_verified)

    def test_runtime_note_digest_uses_fl_control_grid_without_timing_tolerance(self) -> None:
        requested = CreativeNote(
            pitch=60,
            start_beats=0.5,
            duration_beats=1.25,
            velocity=0.72,
            pan=0.72,
            release=0.72,
        )
        requested_digest = creative_module._runtime_note_digest([requested], ppq=96)

        # FL persists each of these controls at the nearest 1/128 position.
        for field in ("velocity", "pan", "release"):
            persisted = requested.model_copy(update={field: 0.71875})
            self.assertEqual(
                requested_digest,
                creative_module._runtime_note_digest([persisted], ppq=96),
            )

        # The next representable control position is still a meaningful change.
        neighboring_control = requested.model_copy(update={"velocity": 93 / 128})
        self.assertNotEqual(
            requested_digest,
            creative_module._runtime_note_digest([neighboring_control], ppq=96),
        )

        # Pitch, start time, and duration remain exact identity fields; no broad
        # float tolerance may make a musically different note look unchanged.
        for field, value in (
            ("pitch", 61),
            ("start_beats", 0.75),
            ("duration_beats", 1.5),
            ("color", 1),
            ("pitch_offset_tenths", 1),
            ("slide", True),
            ("portamento", True),
            ("muted", True),
        ):
            changed_note = requested.model_copy(update={field: value})
            self.assertNotEqual(
                requested_digest,
                creative_module._runtime_note_digest([changed_note], ppq=96),
            )

        # Selection is an editor-only flag.  FL may clear it after dispatch,
        # so it must not turn a semantically identical persisted note into a
        # false verification failure.
        deselected = requested.model_copy(update={"selected": False})
        self.assertEqual(
            requested_digest,
            creative_module._runtime_note_digest([deselected], ppq=96),
        )

    def test_piano_roll_script_runtime_receipt_rejects_tampering(self) -> None:
        notes = [CreativeNote(pitch=60, start_beats=0, duration_beats=1)]
        request_id = "3" * 32
        digest = creative_module.note_digest(notes)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_text(
                '{"hmac_sha256":"' + "0" * 64 + '","payload":{}}',
                encoding="ascii",
            )
            evidence, error = creative_module._read_piano_roll_receipt(
                path,
                receipt_secret="4" * 64,
                request_id=request_id,
                requested_note_digest=digest,
                notes=notes,
                mode="replace",
            )

        self.assertIsNone(evidence)
        self.assertIn("payload fields", error)

    def test_piano_roll_scripting_refuses_before_any_bridge_call_or_file_write(self) -> None:
        """Tested feature: every Piano Roll path this fork removed refuses before it reaches FL or writes a script.
        Check: note writes, transforms, setup, script writing and the shortcut each raise FeatureRemovedError, no bridge command is sent, the scripts folder stays empty, and status reports the removal without reading that folder.
        Mocks: the bridge client, replaced with a recording client; the scripts folder, pointed at a temporary directory.
        Test would be deprecated if: this fork restores Piano Roll scripting.
        """
        class RecordingClient(DirectFakeClient):
            def __init__(self) -> None:
                self.calls: list[str] = []

            def call(self, command: str, **arguments):
                self.calls.append(command)
                return super().call(command, **arguments)

        client = RecordingClient()
        notes = [CreativeNote(pitch=60, start_beats=0.0, duration_beats=1.0)]
        with (
            tempfile.TemporaryDirectory(prefix="postfader-removed-pr-") as scripts,
            mock.patch.dict(
                os.environ, {creative_module.PIANO_ROLL_SCRIPTS_DIR_ENV: scripts}
            ),
            mock.patch("fl_studio_mcp.creative.get_client", return_value=client),
        ):
            with PIANO_ROLL._lock:
                PIANO_ROLL._armed = True
            refusals = {
                "write notes": lambda: write_piano_roll_notes(
                    notes, channel_index=0, pattern_number=1
                ),
                "write notes without trigger": lambda: write_piano_roll_notes(
                    notes, channel_index=0, pattern_number=1, auto_trigger=False
                ),
                "transform": lambda: transform_piano_roll(
                    PianoRollTransform(operation="transpose", semitones=12),
                    channel_index=0,
                    pattern_number=1,
                ),
                "prepare": lambda: PIANO_ROLL.bridge_action("prepare"),
                "confirm": lambda: PIANO_ROLL.bridge_action(
                    "confirm", confirm_user_ran_script=True
                ),
                "script write": lambda: creative_module._atomic_text(
                    Path(scripts) / creative_module.PIANO_ROLL_SCRIPT_NAME, "x"
                ),
                "shortcut": creative_module._trigger_piano_roll_shortcut,
            }
            for label, invoke in refusals.items():
                with self.subTest(path=label):
                    with self.assertRaises(FeatureRemovedError) as refused:
                        invoke()
                    self.assertEqual(str(refused.exception), PIANO_ROLL_REMOVED)
            self.assertEqual(client.calls, [])
            self.assertEqual(os.listdir(scripts), [])
            with mock.patch(
                "fl_studio_mcp.creative.piano_roll_scripts_directory",
                side_effect=AssertionError("status must not read the scripts folder"),
            ):
                status = PIANO_ROLL.status()
        self.assertTrue(status.removed)
        self.assertFalse(status.armed_this_session)
        self.assertFalse(status.script_exists)
        self.assertFalse(status.automatic_trigger_supported)
        self.assertEqual(status.setup_instruction, PIANO_ROLL_REMOVED)

    def test_pattern_creation_uses_protocol_instead_of_build_hash(self) -> None:
        client = DirectFakeClient()
        with (
            mock.patch.object(bridge, "BRIDGE_SOURCE_SHA256", "0" * 64),
            mock.patch("fl_studio_mcp.creative.get_client", return_value=client),
        ):
            result = prepare_empty_pattern(name="Verse", length_beats=32)
        self.assertTrue(result.verified)
        self.assertEqual(_state.PATTERNS[result.pattern_number].name, "Verse")

    def test_native_pattern_marker_and_automation_workflows(self) -> None:
        client = DirectFakeClient()
        with mock.patch("fl_studio_mcp.creative.get_client", return_value=client):
            pattern = prepare_empty_pattern(name="Verse", length_beats=32)
            self.assertEqual(pattern.pattern_number, 3)
            self.assertEqual(pattern.outcome, "complete_verified")
            self.assertTrue(pattern.verified)
            self.assertEqual(_state.PATTERNS[3].name, "Verse")
            self.assertEqual(_state.PATTERNS[3].length, 32)

            markers = add_section_markers(
                [
                    SectionMarker(name="Intro", bar_number=1),
                    SectionMarker(name="Verse", bar_number=5, beat_offset=2),
                ]
            )
            self.assertTrue(markers.names_verified)
            self.assertFalse(markers.times_verified)
            self.assertFalse(markers.verified)
            self.assertEqual(_state.ARRANGEMENT_MARKERS[1][0], 18 * _state.REC_PPQ)

            _state.PLAYING = True
            _state.RECORDING = True
            automation = record_automation_value(
                target_kind="channel",
                target_index=1,
                property="pan",
                value_normalized=0.75,
                expected_before=0.5,
            )
            self.assertTrue(automation.control_value_verified)
            self.assertTrue(automation.capture_conditions_held)
            self.assertIsNone(automation.automation_event_recorded)
            self.assertFalse(automation.verified)
            self.assertAlmostEqual(_state.CHANNELS[1].pan, 0.5, places=3)

    def test_creative_workflows_reject_a_stale_session_before_mutation(self) -> None:
        """Tested feature: pattern, marker and automation workflows refuse a stale FL session before changing anything.
        Check: each workflow given a stale session fingerprint raises the session precondition error, no bridge command is sent, and the fake project is unchanged.
        Mocks: the bridge client, replaced with a recording client over the fake FL modules.
        Test would be deprecated if: these workflows stop taking a session fingerprint.
        """
        class RecordingClient(DirectFakeClient):
            def __init__(self) -> None:
                self.calls: list[str] = []

            def call(self, command: str, **arguments):
                self.calls.append(command)
                return super().call(command, **arguments)

        client = RecordingClient()
        stale = "b" * 32
        with mock.patch("fl_studio_mcp.creative.get_client", return_value=client):
            with self.assertRaisesRegex(
                VerifiedWritesUnavailable, "session precondition failed"
            ):
                prepare_empty_pattern(
                    name="Stale",
                    session_fingerprint=stale,
                )
            with self.assertRaisesRegex(
                VerifiedWritesUnavailable, "session precondition failed"
            ):
                add_section_markers(
                    [SectionMarker(name="Intro", bar_number=1)],
                    session_fingerprint=stale,
                )
            with self.assertRaisesRegex(
                VerifiedWritesUnavailable, "session precondition failed"
            ):
                record_automation_value(
                    target_kind="channel",
                    target_index=1,
                    property="pan",
                    value_normalized=0.75,
                    session_fingerprint=stale,
                )

        self.assertEqual(client.calls, [])
        self.assertEqual(_state.ARRANGEMENT_MARKERS, [])
        self.assertEqual(_state.PATTERNS[3].name, "Pattern 3")

    def test_pattern_preparation_returns_ordered_partial_receipts(self) -> None:
        client = DirectFakeClient()
        with mock.patch("fl_studio_mcp.creative.get_client", return_value=client):
            complete = prepare_empty_pattern(name="Baseline", length_beats=8)

        _state.reset()
        failed_selection = complete.selection.model_copy(
            update={
                "verified": False,
                "verification_summary": "selection readback did not match",
            }
        )
        with (
            mock.patch("fl_studio_mcp.creative.get_client", return_value=client),
            mock.patch(
                "fl_studio_mcp.creative.TrackBController.select_pattern",
                return_value=failed_selection,
            ),
            mock.patch(
                "fl_studio_mcp.creative.TrackBController.set_pattern_identity"
            ) as identity_write,
        ):
            partial = prepare_empty_pattern(name="Verse", length_beats=32)
        self.assertEqual(partial.outcome, "selection_unverified")
        self.assertIsNone(partial.identity)
        self.assertIsNone(partial.length)
        self.assertFalse(partial.verified)
        identity_write.assert_not_called()

        _state.reset()
        failed_identity = complete.identity.model_copy(
            update={
                "verified": False,
                "name_verified": False,
                "verification_summary": "identity readback did not match",
            }
        )
        with (
            mock.patch("fl_studio_mcp.creative.get_client", return_value=client),
            mock.patch(
                "fl_studio_mcp.creative.TrackBController.select_pattern",
                return_value=complete.selection,
            ),
            mock.patch(
                "fl_studio_mcp.creative.TrackBController.set_pattern_identity",
                return_value=failed_identity,
            ),
            mock.patch(
                "fl_studio_mcp.creative.TrackBController.set_pattern_length"
            ) as length_write,
        ):
            partial = prepare_empty_pattern(name="Verse", length_beats=32)
        self.assertEqual(partial.outcome, "identity_unverified")
        self.assertIsNotNone(partial.identity)
        self.assertIsNone(partial.length)
        self.assertFalse(partial.verified)
        length_write.assert_not_called()

    def test_pattern_preparation_refuses_a_changed_exact_target_before_writes(
        self,
    ) -> None:
        client = DirectFakeClient()
        before = (
            _state.CURRENT_PATTERN,
            _state.PATTERNS[3].name,
            _state.PATTERNS[3].length,
            tuple(_state.UNDO),
        )

        with mock.patch("fl_studio_mcp.creative.get_client", return_value=client):
            with self.assertRaisesRegex(ValueError, "no longer the first empty pattern"):
                prepare_empty_pattern(
                    name="Drop",
                    start_pattern_number=3,
                    expected_pattern_number=4,
                )

        self.assertEqual(
            (
                _state.CURRENT_PATTERN,
                _state.PATTERNS[3].name,
                _state.PATTERNS[3].length,
                tuple(_state.UNDO),
            ),
            before,
        )

    def test_tempo_key_and_monophonic_transcription_use_decoded_audio(self) -> None:
        rate = 22050
        with tempfile.TemporaryDirectory() as directory:
            analysis_path = str(Path(directory) / "tempo-key.wav")
            duration = 8
            audio = np.zeros(rate * duration, dtype=np.float64)
            for onset in np.arange(0, duration, 0.5):
                start = int(onset * rate)
                audio[start:start + 600] += np.hanning(600)
            timeline = np.arange(len(audio)) / rate
            audio += 0.08 * np.sin(2 * np.pi * 261.625 * timeline)
            audio += 0.06 * np.sin(2 * np.pi * 329.628 * timeline)
            audio += 0.05 * np.sin(2 * np.pi * 391.995 * timeline)
            audio /= np.max(np.abs(audio))
            sf.write(analysis_path, audio, rate)
            report = analyze_tempo_and_key(analysis_path, max_seconds=8)
            self.assertTrue(report.tempo.available)
            self.assertLess(abs(report.tempo.bpm - 120.0), 7.0)
            self.assertEqual(report.key.key, "C major")
            self.assertIn(report.key.confidence, {"medium", "high"})

            melody_path = str(Path(directory) / "melody.wav")
            melody = np.zeros(rate * 2, dtype=np.float64)
            first_time = np.arange(int(rate * 0.8)) / rate
            melody[: len(first_time)] = 0.5 * np.sin(2 * np.pi * 440.0 * first_time)
            second_time = np.arange(int(rate * 0.8)) / rate
            melody[rate:rate + len(second_time)] = 0.5 * np.sin(
                2 * np.pi * 523.251 * second_time
            )
            sf.write(melody_path, melody, rate)
            transcription = transcribe_monophonic(
                melody_path,
                tempo_bpm=120.0,
                max_seconds=2.0,
                quantize_grid_beats=0.25,
            )
            self.assertEqual([note.pitch for note in transcription.sequence.notes], [69, 72])
            self.assertGreater(transcription.voiced_frame_share, 0.7)
            self.assertFalse(transcription.mutations_applied)


if __name__ == "__main__":
    unittest.main()
