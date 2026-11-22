"""Synthetic tests for the live Sound Selection integration seam."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from fl_studio_mcp.fork_removals import PRESET_SELECTION_REMOVED
from fl_studio_mcp.sound_selection.executor import (
    SoundSelectionAuthorizationError,
    SoundSelectionService,
    SoundSelectionSessionError,
)
from fl_studio_mcp.sound_selection.models import (
    PaletteApplyReceipt,
    SoundFeedbackRequest,
    SoundInventory,
    SoundRoleRequest,
    SoundSelectionRequest,
    SoundTargetInventory,
)
from fl_studio_mcp.track_b_contracts import (
    ChannelGeneratorTarget,
    MixerEffectTarget,
    PluginCurrentPreset,
    PluginPad,
    PluginPadMap,
    PluginPresetPage,
    PluginPresetRecord,
    PluginPresetState,
    TargetedLoadedPluginInventory,
    TargetedPluginSummary,
    VerifiedPluginPresetSelection,
)


SESSION = "a" * 32
TARGET_FINGERPRINT = "b" * 64
TARGET = ChannelGeneratorTarget(channel_index=3)
STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeInspector:
    def __init__(self) -> None:
        self.scans = 0
        self.preset_reads = 0
        self.current_reads = 0
        self.pad_reads = 0
        self.summary = TargetedPluginSummary(
            target=TARGET,
            name="Synthetic Synth",
            target_fingerprint=TARGET_FINGERPRINT,
            reported_parameter_count=4,
        )

    def connection_info(self):
        return SimpleNamespace(
            connected=True,
            compatible=True,
            session_fingerprint=SESSION,
        )

    def scan_loaded_plugins(self, *, only_used: bool = False) -> TargetedLoadedPluginInventory:
        self.scans += 1
        return TargetedLoadedPluginInventory(
            observed_at=STAMP,
            plugins=[self.summary],
            warnings=["synthetic inventory"],
        )

    def list_plugin_presets(self, *, target, start, limit, include_current, include_empty_names):
        self.preset_reads += 1
        return PluginPresetPage(
            observed_at=STAMP,
            plugin=self.summary,
            preset_count=2,
            start=start,
            limit=limit,
            scanned_count=2,
            returned_count=2,
            has_more=False,
            presets=[
                PluginPresetRecord(index=0, name="Current", is_current=True),
                PluginPresetRecord(index=1, name="Bright Lead"),
            ],
            current_preset_name="Current",
            current_preset_index=0,
            current_preset_status="stable",
            duplicate_names=[],
            blank_name_indices=[],
            session_fingerprint=SESSION,
        )

    def get_plugin_current_preset(self, *, target, allow_master=False):
        self.current_reads += 1
        return PluginCurrentPreset(
            observed_at=STAMP,
            plugin=self.summary,
            preset_count=2,
            current_preset_name="Current",
            current_preset_index=0,
            current_preset_status="stable",
            session_fingerprint=SESSION,
        )

    def inspect_plugin_pad_map(self, *, target, allow_master=False):
        self.pad_reads += 1
        return PluginPadMap(
            observed_at=STAMP,
            plugin=self.summary,
            pad_count=2,
            pads=[
                PluginPad(pad_index=0, semitone=36, semitone_name="kick", empty=False, muted=False),
                PluginPad(pad_index=1, semitone=38, semitone_name="snare", empty=False, muted=False),
            ],
            complete=True,
            session_fingerprint=SESSION,
        )


class FakeMode:
    def __init__(self) -> None:
        self.calls = []

    def set_write_mode(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(after_enabled=True)


class FakeController:
    def __init__(self) -> None:
        self.calls = []

    def select_plugin_preset(self, **kwargs):
        self.calls.append(kwargs)
        expected = kwargs["expected_current"]
        before = PluginPresetState(
            name="Current", index=0, identity_status="stable"
        )
        if expected is not None:
            before = PluginPresetState(name=expected.name, index=expected.index, identity_status="stable")
        after = PluginPresetState(
            name=kwargs["preset_name"],
            index=kwargs["preset_index"],
            identity_status="stable",
        )
        return VerifiedPluginPresetSelection(
            applied_at=STAMP,
            verified=True,
            verification_summary="synthetic later-tick readback",
            target=TARGET,
            plugin=TargetedPluginSummary(
                target=TARGET,
                name="Synthetic Synth",
                target_fingerprint=TARGET_FINGERPRINT,
                reported_parameter_count=4,
            ),
            requested_preset_name=kwargs["preset_name"],
            requested_preset_index=kwargs["preset_index"],
            before=before,
            after=after,
            outcome="verified",
            navigation_direction="next",
            navigation_steps=1,
            max_navigation_steps=kwargs["max_navigation_steps"],
            settle_tick_limit=kwargs["settle_tick_limit"],
            target_fingerprint=TARGET_FINGERPRINT,
            session_fingerprint=SESSION,
            session_precondition_applied=True,
        )


class FakeHistory:
    def __init__(self, path: str) -> None:
        from fl_studio_mcp.sound_selection.history import LocalSoundSelectionHistory

        self._history = LocalSoundSelectionHistory(path=path)
        self.usage_calls = []

    def record_usage(self, *args, **kwargs):
        self.usage_calls.append((args, kwargs))
        return self._history.record_usage(*args, **kwargs)

    def record_feedback(self, **kwargs):
        return self._history.record_feedback(**kwargs)

    def status(self):
        return self._history.status()

    def reset(self):
        return self._history.reset()


class SoundSelectionServiceTests(unittest.TestCase):
    def make_service(self, directory: str):
        inspector = FakeInspector()
        controller = FakeController()
        mode = FakeMode()
        history = FakeHistory(str(Path(directory) / "history.json"))
        service = SoundSelectionService(
            inspector=inspector,
            controller=controller,
            write_mode_manager=mode,
            history=history,
            atlas_registry=SimpleNamespace(products=()),
            atlas_inspector=lambda request, **kwargs: SimpleNamespace(plugins=(), warnings=()),
            now=lambda: STAMP,
        )
        return service, inspector, controller, mode, history

    def test_inventory_scans_once_reuses_target_scan_and_reads_bounded_surfaces(self):
        with TemporaryDirectory() as directory:
            service, inspector, _, _, _ = self.make_service(directory)
            inventory = service.inventory()
            self.assertEqual(inspector.scans, 1)
            self.assertEqual(inspector.preset_reads, 1)
            self.assertEqual(inspector.current_reads, 1)
            self.assertEqual(inspector.pad_reads, 1)
            self.assertEqual(inventory.session_fingerprint, SESSION)
            self.assertEqual(inventory.loaded_generators[0].target_fingerprint, TARGET_FINGERPRINT)
            self.assertEqual(inventory.loaded_generators[0].pad_map.mapped_notes, (("kick", 36), ("snare", 38)))

    def test_apply_checks_authorization_and_session_then_refuses_preset_roles(self):
        """Tested feature: palette application checks authorization and session, then refuses a palette whose role would select a preset.
        Check: missing authorization or a wrong session raises, and an authorized apply fails with the preset-removal blocker without enabling write mode, selecting a preset, or writing history.
        Mocks: the FL inspector, controller, write-mode manager and history, replaced with recording fakes.
        Test would be deprecated if: this fork restores preset selection.
        """
        with TemporaryDirectory() as directory:
            service, inspector, controller, mode, history = self.make_service(directory)
            request = SoundSelectionRequest(
                brief="bright lead",
                roles=(SoundRoleRequest(role_id="main_lead", role_type="lead", preferred_presets=("Bright Lead",)),),
            )
            inventory = SoundInventory(
                session_fingerprint=SESSION,
                loaded_generators=(SoundTargetInventory(
                    target=TARGET,
                    target_fingerprint=TARGET_FINGERPRINT,
                    product_id="synthetic",
                    product_name="Synthetic Synth",
                    current_preset="Current",
                    current_preset_index=0,
                    preset_names=("Current", "Bright Lead"),
                    preset_indices=(0, 1),
                    preset_navigation_available=True,
                    preset_identity_stable=True,
                    preset_readback_available=True,
                ),),
            )
            plan = service.plan(request, inventory=inventory)
            with self.assertRaises(SoundSelectionAuthorizationError):
                service.apply(plan, session_fingerprint=SESSION, authorized_to_modify=False)
            with self.assertRaisesRegex(
                SoundSelectionSessionError,
                "non-null 32-character lowercase session fingerprint",
            ):
                service.apply(plan, session_fingerprint=None, authorized_to_modify=True)
            with self.assertRaises(SoundSelectionSessionError):
                service.apply(plan, session_fingerprint="c" * 32, authorized_to_modify=True)
            self.assertEqual(plan.assignments[0].selection_action, "select_preset")
            scans_before = inspector.scans
            first = service.apply(plan, session_fingerprint=SESSION, authorized_to_modify=True)
            self.assertEqual(first.status, "failed")
            self.assertEqual(first.state.palette_id, plan.palette_id)
            self.assertIn(PRESET_SELECTION_REMOVED, " ".join(first.blockers))
            self.assertIn("'main_lead'", " ".join(first.blockers))
            self.assertEqual(first.receipts, ())
            self.assertEqual(first.history_written, 0)
            self.assertEqual(mode.calls, [])
            self.assertEqual(controller.calls, [])
            self.assertEqual(history.usage_calls, [])
            self.assertEqual(inspector.scans, scans_before)  # refused before preflight

    def test_scoped_preset_role_refuses_even_with_write_mode_pre_enabled(self):
        """Tested feature: a scoped palette apply refuses a role that would select a preset, even when write mode is already on, and unknown palettes are reported as missing.
        Check: applying only the preset role with write_mode_already_enabled fails with the removal blocker and no controller or write-mode call, and a lookup of an unknown palette finds nothing.
        Mocks: the FL inspector, controller, write-mode manager and history, replaced with recording fakes.
        Test would be deprecated if: this fork restores preset selection.
        """
        with TemporaryDirectory() as directory:
            service, _, controller, mode, _ = self.make_service(directory)
            request = SoundSelectionRequest(
                brief="bright lead",
                roles=(SoundRoleRequest(role_id="main_lead", role_type="lead", preferred_presets=("Bright Lead",)),),
            )
            inventory = SoundInventory(
                session_fingerprint=SESSION,
                loaded_generators=(SoundTargetInventory(
                    target=TARGET,
                    target_fingerprint=TARGET_FINGERPRINT,
                    product_id="synthetic",
                    product_name="Synthetic Synth",
                    current_preset="Current",
                    current_preset_index=0,
                    preset_names=("Current", "Bright Lead"),
                    preset_indices=(0, 1),
                    preset_navigation_available=True,
                    preset_identity_stable=True,
                    preset_readback_available=True,
                ),),
            )
            plan = service.plan(request, inventory=inventory)
            result = service.apply(
                plan,
                SESSION,
                True,
                role_ids=("main_lead",),
                write_mode_already_enabled=True,
            )
            self.assertEqual(result.status, "failed")
            self.assertIn(PRESET_SELECTION_REMOVED, " ".join(result.blockers))
            self.assertEqual(len(mode.calls), 0)
            self.assertEqual(len(controller.calls), 0)
            missing = service.lookup("palette-does-not-exist")
            self.assertFalse(missing.found)
            self.assertTrue(missing.process_local)
            self.assertIsNone(missing.state)
            self.assertIn("another process", missing.message)

    def test_persist_history_false_writes_no_usage_history(self):
        """Tested feature: a palette planned with persist_history=False applies without writing usage history.
        Check: a keep-current palette applies, and no usage record is written or reported.
        Mocks: the FL inspector, controller, write-mode manager and history, replaced with recording fakes.
        Test would be deprecated if: palettes stop recording usage history.
        """
        with TemporaryDirectory() as directory:
            service, inspector, controller, mode, history = self.make_service(directory)
            request = SoundSelectionRequest(
                brief="lead",
                persist_history=False,
                roles=(SoundRoleRequest(role_id="main_lead", role_type="lead", preferred_presets=("Current",)),),
            )
            plan = service.plan(request)
            self.assertEqual(plan.assignments[0].selection_action, "keep_current")
            result = service.apply(plan, session_fingerprint=SESSION, authorized_to_modify=True)
            self.assertEqual(result.status, "applied")
            self.assertEqual(result.history_written, 0)
            self.assertEqual(len(history.usage_calls), 0)
            self.assertEqual(len(controller.calls), 0)

    def test_feedback_status_and_reset_are_local_explicit_wrappers(self):
        with TemporaryDirectory() as directory:
            service, _, _, _, _ = self.make_service(directory)
            feedback = SoundFeedbackRequest(palette_id="palette-unknown", verdict="neutral", persist=False)
            result = service.record_feedback(feedback)
            self.assertFalse(result.persisted)
            with self.assertRaises(SoundSelectionAuthorizationError):
                service.history_reset(confirm=False)
            reset = service.history_reset(confirm=True)
            self.assertFalse(reset.recoverable)

    def test_keep_current_is_verified_history_and_feedback_updates_state(self):
        with TemporaryDirectory() as directory:
            service, _, controller, _, history = self.make_service(directory)
            request = SoundSelectionRequest(
                brief="keep the current lead",
                roles=(
                    SoundRoleRequest(
                        role_id="main_lead",
                        role_type="lead",
                        preferred_presets=("Current",),
                    ),
                ),
            )
            inventory = SoundInventory(
                session_fingerprint=SESSION,
                loaded_generators=(
                    SoundTargetInventory(
                        target=TARGET,
                        target_fingerprint=TARGET_FINGERPRINT,
                        product_id="synthetic",
                        product_name="Synthetic Synth",
                        current_preset="Current",
                        current_preset_index=0,
                        preset_names=("Current",),
                        preset_indices=(0,),
                        preset_navigation_available=True,
                        preset_identity_stable=True,
                        preset_readback_available=True,
                    ),
                ),
            )
            plan = service.plan(request, inventory=inventory)
            self.assertEqual(plan.assignments[0].selection_action, "keep_current")
            applied = service.apply(
                plan,
                session_fingerprint=SESSION,
                authorized_to_modify=True,
            )
            feedback = service.record_feedback(
                SoundFeedbackRequest(
                    palette_id=plan.palette_id,
                    role_id="main_lead",
                    verdict="accepted",
                    persist=False,
                )
            )
            state = service.get(plan.palette_id)

            self.assertEqual(applied.status, "applied")
            self.assertTrue(applied.assignment_receipts[0].verified)
            self.assertEqual(len(controller.calls), 0)
            self.assertEqual(len(history.usage_calls), 1)
            self.assertFalse(feedback.persisted)
            self.assertEqual(len(state.accepted_feedback), 1)

    def test_section_variation_is_registered_and_its_preset_delta_refuses(self):
        """Tested feature: a section variation is registered on its base palette, and applying a delta that selects a preset refuses.
        Check: the variation has one assignment, applying it fails with the removal blocker over exactly that assignment and no controller call, and the base palette records the variation.
        Mocks: the FL inspector, controller, write-mode manager and history, replaced with recording fakes.
        Test would be deprecated if: this fork restores preset selection.
        """
        with TemporaryDirectory() as directory:
            service, _, controller, _, _ = self.make_service(directory)
            request = SoundSelectionRequest(
                brief="vary the texture in the drop",
                roles=(
                    SoundRoleRequest(
                        role_id="texture",
                        role_type="texture",
                        allow_section_variation=True,
                    ),
                ),
            )
            inventory = SoundInventory(
                session_fingerprint=SESSION,
                loaded_generators=(
                    SoundTargetInventory(
                        target=TARGET,
                        target_fingerprint=TARGET_FINGERPRINT,
                        product_id="synthetic",
                        product_name="Synthetic Synth",
                        current_preset="Current",
                        current_preset_index=0,
                        preset_names=("Current", "Bright Lead"),
                        preset_indices=(0, 1),
                        preset_navigation_available=True,
                        preset_identity_stable=True,
                        preset_readback_available=True,
                    ),
                ),
            )
            plan = service.plan(request, inventory=inventory)
            variation = service.create_variation(
                plan.palette_id,
                request,
                section="drop_b",
                inventory=inventory,
            )
            self.assertEqual(len(variation.assignments), 1)

            applied = service.apply(
                variation,
                session_fingerprint=SESSION,
                authorized_to_modify=True,
            )
            state = service.get(plan.palette_id)

            self.assertEqual(variation.assignments[0].selection_action, "select_preset")
            self.assertEqual(applied.status, "failed")
            self.assertIn(PRESET_SELECTION_REMOVED, " ".join(applied.blockers))
            self.assertEqual(applied.assignment_scope, variation.assignments)
            self.assertEqual(controller.calls, [])
            self.assertEqual(state.section_variations[0].variation_id, variation.variation_id)

    def test_inventory_filters_effects_before_per_target_reads(self):
        class EffectInspector(FakeInspector):
            def __init__(self) -> None:
                super().__init__()
                self.effect_target = MixerEffectTarget(track_index=4, slot_index=1)
                self.effect_summary = TargetedPluginSummary(
                    target=self.effect_target,
                    name="Synthetic Effect",
                    target_fingerprint="c" * 64,
                    reported_parameter_count=2,
                )

            def scan_loaded_plugins(self, *, only_used=False):
                self.scans += 1
                return TargetedLoadedPluginInventory(
                    observed_at=STAMP,
                    plugins=[self.summary, self.effect_summary],
                )

            def _summary(self, target):
                return self.effect_summary if target.kind == "mixer_effect" else self.summary

            def list_plugin_presets(
                self, *, target, start, limit, include_current, include_empty_names, **kwargs
            ):
                self.preset_reads += 1
                return SimpleNamespace(
                    plugin=self._summary(target),
                    preset_count=1,
                    presets=(PluginPresetRecord(index=0, name="Current"),),
                    current=PluginPresetState(
                        name="Current", index=0, identity_status="stable"
                    ),
                    session_fingerprint=SESSION,
                    warnings=(),
                )

            def get_plugin_current_preset(self, *, target, **kwargs):
                self.current_reads += 1
                return SimpleNamespace(
                    plugin=self._summary(target),
                    preset_count=1,
                    current=PluginPresetState(
                        name="Current", index=0, identity_status="stable"
                    ),
                    session_fingerprint=SESSION,
                    warnings=(),
                )

        with TemporaryDirectory() as directory:
            inspector = EffectInspector()
            service, _, _, _, _ = self.make_service(directory)
            service.inspector = inspector
            without_effects = service.inventory(
                SoundSelectionRequest(brief="generators only", allow_effect_presets=False)
            )
            self.assertEqual(len(without_effects.loaded_generators), 1)
            self.assertEqual(without_effects.loaded_effects, ())
            self.assertEqual(inspector.preset_reads, 1)
            self.assertEqual(inspector.current_reads, 1)

            with_effects = service.inventory(
                SoundSelectionRequest(brief="include effects", allow_effect_presets=True)
            )
            self.assertEqual(len(with_effects.loaded_generators), 1)
            self.assertEqual(len(with_effects.loaded_effects), 1)
            self.assertEqual(inspector.preset_reads, 3)
            self.assertEqual(inspector.current_reads, 3)

    def test_apply_fails_closed_without_live_connection_proof(self):
        """Tested feature: palette application fails closed when the live connection cannot be revalidated.
        Check: a keep-current palette applied through an inspector without connection proof fails with the revalidation blocker and no controller or write-mode call.
        Mocks: the FL inspector, controller, write-mode manager and history, replaced with recording fakes.
        Test would be deprecated if: palette application stops revalidating the live session.
        """
        with TemporaryDirectory() as directory:
            service, inspector, controller, mode, _ = self.make_service(directory)
            inventory = SoundInventory(
                session_fingerprint=SESSION,
                loaded_generators=(
                    SoundTargetInventory(
                        target=TARGET,
                        target_fingerprint=TARGET_FINGERPRINT,
                        product_name="Synthetic Synth",
                        current_preset="Current",
                        current_preset_index=0,
                        preset_names=("Current",),
                        preset_indices=(0,),
                        preset_navigation_available=True,
                        preset_identity_stable=True,
                        preset_readback_available=True,
                    ),
                ),
            )
            plan = service.plan(
                SoundSelectionRequest(
                    brief="bright lead",
                    roles=(
                        SoundRoleRequest(
                            role_id="main_lead",
                            role_type="lead",
                            preferred_presets=("Current",),
                        ),
                    ),
                ),
                inventory=inventory,
            )
            service.inspector = SimpleNamespace(
                scan_loaded_plugins=inspector.scan_loaded_plugins,
                get_plugin_current_preset=inspector.get_plugin_current_preset,
            )
            result = service.apply(plan, SESSION, True)
            self.assertEqual(result.status, "failed")
            self.assertIn("session revalidation is unavailable", " ".join(result.blockers))
            self.assertEqual(controller.calls, [])
            self.assertEqual(mode.calls, [])

    def test_keep_current_requires_stable_live_identity(self):
        with TemporaryDirectory() as directory:
            service, inspector, controller, mode, _ = self.make_service(directory)
            inventory = SoundInventory(
                session_fingerprint=SESSION,
                loaded_generators=(
                    SoundTargetInventory(
                        target=TARGET,
                        target_fingerprint=TARGET_FINGERPRINT,
                        product_name="Synthetic Synth",
                        current_preset="Current",
                        current_preset_index=0,
                        preset_names=("Current",),
                        preset_indices=(0,),
                        preset_navigation_available=True,
                        preset_identity_stable=True,
                        preset_readback_available=True,
                    ),
                ),
            )
            plan = service.plan(
                SoundSelectionRequest(
                    brief="keep the lead",
                    roles=(
                        SoundRoleRequest(
                            role_id="main_lead",
                            role_type="lead",
                            preferred_presets=("Current",),
                        ),
                    ),
                ),
                inventory=inventory,
            )

            def unresolved(*, target, allow_master=False):
                return PluginCurrentPreset(
                    observed_at=STAMP,
                    plugin=inspector.summary,
                    preset_count=1,
                    current_preset_name="Current",
                    current_preset_index=None,
                    current_preset_status="unresolved",
                    session_fingerprint=SESSION,
                )

            inspector.get_plugin_current_preset = unresolved
            result = service.apply(plan, SESSION, True)
            self.assertEqual(result.status, "failed")
            self.assertIn("not stable", " ".join(result.blockers))
            self.assertEqual(controller.calls, [])
            self.assertEqual(mode.calls, [])

    def test_supplied_state_cannot_replace_process_local_state(self):
        with TemporaryDirectory() as directory:
            service, _, controller, mode, _ = self.make_service(directory)
            plan = service.plan(
                SoundSelectionRequest(brief="plan", roles=()),
                inventory=SoundInventory(session_fingerprint=SESSION),
            )
            altered = service.get(plan.palette_id).model_copy(update={"status": "applied"})
            with self.assertRaisesRegex(ValueError, "immutable process-local state"):
                service.apply(altered, SESSION, True)
            self.assertEqual(controller.calls, [])
            self.assertEqual(mode.calls, [])

    def test_blocked_variation_never_enables_writes(self):
        with TemporaryDirectory() as directory:
            service, _, controller, mode, _ = self.make_service(directory)
            request = SoundSelectionRequest(
                brief="vary the texture",
                roles=(
                    SoundRoleRequest(
                        role_id="texture",
                        role_type="texture",
                        allow_section_variation=True,
                    ),
                ),
            )
            base_inventory = SoundInventory(
                session_fingerprint=SESSION,
                loaded_generators=(
                    SoundTargetInventory(
                        target=TARGET,
                        target_fingerprint=TARGET_FINGERPRINT,
                        product_name="Synthetic Synth",
                        current_preset="Current",
                        current_preset_index=0,
                        preset_names=("Current", "Bright Lead"),
                        preset_indices=(0, 1),
                        preset_navigation_available=True,
                        preset_identity_stable=True,
                        preset_readback_available=True,
                    ),
                ),
            )
            base = service.plan(request, inventory=base_inventory)
            variation = service.create_variation(
                base.palette_id,
                request,
                section="drop_b",
                inventory=SoundInventory(session_fingerprint=SESSION),
            )
            self.assertTrue(variation.blockers)
            result = service.apply(variation, SESSION, True)
            self.assertEqual(result.status, "failed")
            self.assertEqual(controller.calls, [])
            self.assertEqual(mode.calls, [])

    def test_full_receipt_registry_blocks_before_mutation(self):
        """Tested feature: palette application stops before any FL call when its receipt registry is full.
        Check: a keep-current palette whose registry already holds the maximum receipts fails with the bound blocker and no controller or write-mode call.
        Mocks: the FL inspector, controller, write-mode manager and history, replaced with recording fakes.
        Test would be deprecated if: palette receipts stop being bounded.
        """
        with TemporaryDirectory() as directory:
            service, _, controller, mode, _ = self.make_service(directory)
            inventory = SoundInventory(
                session_fingerprint=SESSION,
                loaded_generators=(
                    SoundTargetInventory(
                        target=TARGET,
                        target_fingerprint=TARGET_FINGERPRINT,
                        product_name="Synthetic Synth",
                        current_preset="Current",
                        current_preset_index=0,
                        preset_names=("Current",),
                        preset_indices=(0,),
                        preset_navigation_available=True,
                        preset_identity_stable=True,
                        preset_readback_available=True,
                    ),
                ),
            )
            plan = service.plan(
                SoundSelectionRequest(
                    brief="bright lead",
                    roles=(
                        SoundRoleRequest(
                            role_id="main_lead",
                            role_type="lead",
                            preferred_presets=("Current",),
                        ),
                    ),
                ),
                inventory=inventory,
            )
            service.palette_registry.record_receipts(
                plan.palette_id,
                tuple(
                    PaletteApplyReceipt(
                        assignment_id=f"filled-{index}",
                        role_id="texture",
                        verified=True,
                        verification_summary="synthetic prior receipt",
                    )
                    for index in range(128)
                ),
                status="partially_applied",
                now=STAMP,
            )
            result = service.apply(plan, SESSION, True)
            self.assertEqual(result.status, "failed")
            self.assertIn("receipt bound is full", " ".join(result.blockers))
            self.assertEqual(controller.calls, [])
            self.assertEqual(mode.calls, [])

    def test_full_palette_feedback_state_is_not_pruned_or_rewritten(self):
        with TemporaryDirectory() as directory:
            service, _, _, _, _ = self.make_service(directory)
            plan = service.plan(
                SoundSelectionRequest(brief="feedback bound", roles=()),
                inventory=SoundInventory(session_fingerprint=SESSION),
            )
            state = service.get(plan.palette_id)
            prior = tuple(f"feedback-{index}" for index in range(128))
            service.palette_registry.put(
                state.model_copy(update={"accepted_feedback": prior})
            )
            result = service.record_feedback(
                SoundFeedbackRequest(
                    palette_id=plan.palette_id,
                    verdict="accepted",
                    persist=False,
                )
            )
            self.assertIn("feedback state is full", " ".join(result.warnings))
            self.assertEqual(service.get(plan.palette_id).accepted_feedback, prior)


if __name__ == "__main__":
    unittest.main()
