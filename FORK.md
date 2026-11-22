# About this fork

This repository is a fork of
[PostFader](https://github.com/synopsys0/postfader-fl-studio-mcp) 12.0.3, an
FL Studio MCP server under the Apache License 2.0. Its version is
`12.0.3+fork.1`.

This fork removes Piano Roll script generation, plug-in preset selection, and
the TCP and file transports, so the live bridge between the server and
FL Studio uses only MIDI. Everything else works as in upstream 12.0.3. The fork has 80 tools; upstream
12.0.3 has 85.

The fork is not published to PyPI or the MCP Registry. `server.json` keeps
upstream's registry name only because the package tests require that file to
agree with the package version.

## What changed

### Piano Roll scripting removed

- The tools `piano_roll_setup`, `piano_roll_read_notes`,
  `piano_roll_write_notes` and `piano_roll_transform_notes` are gone, and so is
  `fl_studio_mcp/piano_roll.py`.
- The server functions that wrote the generated `Postfader_Apply.pyscript` or
  focused FL Studio and sent its run-last-script shortcut (Ctrl+Alt+Y, or
  Cmd+Opt+Y on macOS) raise `FeatureRemovedError`
  (`fl_studio_mcp/fork_removals.py`). The code that sent the shortcut is
  deleted.
- The Piano Roll status that creation readiness and Production Runs read
  reports Piano Roll scripting as removed.
- The FL Studio script no longer accepts `creative.prepare_piano_roll` or
  `creative.piano_roll_target`, and its `ping` reply no longer advertises
  `piano_roll_navigation`.

### Plug-in preset selection removed

- The `plugin_select_preset` tool is gone.
- `TrackBController.select_plugin_preset`, the one server path that sent
  `plugin.select_preset`, raises `FeatureRemovedError`. The command is out of
  the server's mutation allowlist and out of the FL Studio script's write list
  and command table.
- Reading presets still works: `plugin_list_presets`, `sound_get_inventory`
  and `sound_plan_palette` are unchanged. Loop Starter rerolls, which are not
  presets, still work.

### MIDI-only bridge

- The FL Studio script starts only its MIDI SysEx transport.
- `BridgeClient` builds only the MIDI transport and no longer takes `host`,
  `port` or `mailbox` arguments. It refuses to start while `FL_BRIDGE_HOST`,
  `FL_BRIDGE_PORT` or `FL_BRIDGE_MAILBOX` is set. Without
  `FL_BRIDGE_ENABLE_MIDI=1` the client has no transport, and every call
  reports that it could not reach FL Studio, naming `FL_BRIDGE_ENABLE_MIDI`
  and `FL_BRIDGE_MIDI_PORT`.
- The TCP and file mailbox transport classes stay in the code because the
  tests use them as a deterministic fake FL Studio; nothing in the package
  constructs them.
- The bridge is the only connection the fork narrows. Two tools still reach
  FL Studio another way, as in upstream: on macOS, `plugin_list_available`
  and `plugin_load` drive FL Studio's menus through System Events, which
  focuses FL Studio and sends it keystrokes, and `render_start_job` starts a
  separate FL Studio process to render a saved project.

### Clean refusals

- Production Runs refuse, at validation and before anything is written, any
  plan containing `write_note_sequence`, `transform_piano_roll`,
  `select_plugin_preset`, `select_drum_kit` or `apply_creation_revision`.
  A creation revision may produce note writes or preset or drum-kit
  selections, so the fork refuses `apply_creation_revision` inside a
  Production Run; `review_apply_revision` applies revisions that contain none
  of them, and refuses, at the same validation, a revision that does.
- `run_describe_operations` lists those five operations as unavailable, with
  the reason for each, and the run tools' schemas no longer offer them.
- The planners that change nothing (`plan_sound_palette`,
  `create_sound_palette_variation`, `plan_creation_revision`) still work.
- `sound_apply_palette` refuses a palette, before any write, if any role being
  applied would select a preset. In a Production Run, an `apply_sound_palette`
  operation that carries its palette inline is refused at validation, before
  anything in the run is written. One that applies a palette produced by an
  earlier step of the same run is refused only when it is reached, so earlier
  operations in that run may already have applied.

### Other

- The MIDI port probe's child Python starts with `CREATE_NO_WINDOW` on
  Windows, so it cannot open a console window.
- `manifest.json` and `docs/tools.md` are regenerated; the other documents
  that describe removed features carry a note pointing here.
- The live acceptance scripts under `scripts/` are unchanged and still
  exercise removed features; they need FL Studio and are not part of the safe
  test suite.

## Where the removed code still is

To keep upstream releases easy to merge, the fork cuts the removed features
off instead of deleting every module. The shared data models, the FL Studio
script's handlers for the removed commands, the bodies of the refusing server
functions, and the TCP and file transport classes stay in place, unreachable.

## Merging an upstream release

1. Fetch the upstream remote and merge the release tag.
2. Resolve conflicts so that each change above still holds. Search the merged
   tree for `piano_roll`, `select_preset`, `prepare_piano_roll`,
   `_SocketTransport`, `_TcpTransport` and `_FileTransport`, and check that no
   new tool, FL Studio script command, run operation or transport reaches a
   removed feature. Add any new run operation that does to
   `REMOVED_FEATURE_OPERATION_MESSAGES` in `fl_studio_mcp/production_runs.py`;
   `run_describe_operations` and the run tools' schemas follow it.
3. Set the version to `<upstream version>+fork.1` in `pyproject.toml`,
   `fl_studio_mcp/__init__.py`, `server.json` (both places) and
   `manifest.json`.
4. Regenerate the tool lists with `python scripts/sync_mcpb_manifest.py` and
   `python scripts/generate_tool_reference.py`, and update the tool count in
   `README.md` and this file.
5. Run `python scripts/run_safe_tests.py`, `ruff`, `pyright` and
   `python scripts/check_public_tree.py`.
