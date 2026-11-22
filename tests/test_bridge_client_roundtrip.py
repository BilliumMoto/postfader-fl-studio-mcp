"""Drive BridgeClient end to end against the bridge script on a deterministic link.

Production FL Studio is reached only over MIDI SysEx, which this offline suite
never opens. These checks start the bridge's loopback TCP transport directly
and give a BridgeClient the matching TCP transport, so the client's command
round trips, chunked jobs, large replies, verified writes, error reporting and
concurrent use are exercised through the same code the MCP server uses.
"""

import os
import sys
import threading
import time

from _checks import check, section, summary

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "fakefl"))
sys.path.insert(0, os.path.join(ROOT, "fl_studio_mcp", "_bridge"))
sys.path.insert(0, ROOT)

# Exercise one narrow verified write after the read-only round trips.
os.environ["FL_BRIDGE_ENABLE_WRITES"] = "1"

import _state  # noqa: E402
import device_UniversalBridge as bridge  # noqa: E402

# No deterministic process may contact the production bridge port.
bridge.PORT = 0


def start_tcp_fake_bridge():
    """Start the bridge's TCP transport directly as this file's fake FL link.

    The FL script's own startup builds only MIDI, so this check constructs the
    TCP transport itself instead of calling OnInit.
    """
    transport = bridge._SocketTransport()
    transport.start()
    bridge._transport = transport
    return transport.server.getsockname()[1]


def tcp_client(port, timeout):
    """A BridgeClient whose only transport is TCP to the fake bridge."""
    from fl_studio_mcp import bridge_client

    client = bridge_client.BridgeClient(timeout=timeout, midi_port="unused")
    client._transports = [bridge_client._TcpTransport("127.0.0.1", port, timeout)]
    return client


def main():
    """Tested feature: BridgeClient carries commands, long jobs, writes and errors to the bridge, and reports a dead bridge.
    Check: over a TCP link to the bridge script with fake FL modules, reads, a chunked scan, a large reply and a verified write round trip, bridge errors and unknown commands raise with their detail, two clients work concurrently, and a stopped bridge gives the not-connected error.
    Mocks: the fake FL modules in tests/fakefl, and the client's MIDI transport replaced with a TCP one.
    Test would be deprecated if: BridgeClient or the bridge's command protocol is removed.
    """
    _state.reset()
    port = start_tcp_fake_bridge()
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            bridge.OnIdle()
            time.sleep(0.004)

    t = threading.Thread(target=pump, daemon=True)
    t.start()

    from fl_studio_mcp.bridge_client import BridgeError

    client = tcp_client(port, timeout=15)

    section("client reaches the bridge")
    info = client.ping()
    check("ping answered", info.get("pong") is True, info)
    check("client reports its transport", client.transport == "tcp", client.transport)

    section("commands round trip")
    proj = client.call("project.info")
    check("project.info", proj["tempo_bpm"] == 140.0, proj)
    mix = client.call("mixer.list")
    check("mixer.list", mix["track_count"] == 126, mix.get("track_count"))
    vox = [item for item in mix["tracks"] if item["index"] == 3][0]
    check("plugins visible", len(vox["plugins"]) == 3, vox["plugins"])

    section("chunked job")
    t0 = time.time()
    full = client.call("mixer.list", only_used=False)
    elapsed = time.time() - t0
    check("full scan completed", len(full["tracks"]) == 126, len(full["tracks"]))
    check("scan finished promptly (%.2fs)" % elapsed, elapsed < 10, elapsed)

    section("large payload survives the round trip")
    big = client.call("mixer.list", only_used=False, peaks=True)
    check("peaks included", big["tracks"][0].get("peak_l") is not None,
          big["tracks"][0])
    check("payload intact", all("plugins" in item for item in big["tracks"]))

    section("writes")
    changed = client.call("mixer.set_volume", track=3, value=0.6)
    check("verified write applied",
          changed["verified"] is True
          and abs(_state.TRACKS[3].volume - 0.6) < 1e-9,
          changed)

    section("errors propagate")
    try:
        client.call("plugin.set_param", track=3, slot=1, index=999, value=0.5)
        check("bad parameter raises", False, "no exception")
    except BridgeError as e:
        check("bad parameter raises", "outside" in str(e), str(e)[:80])
    try:
        client.call("no.such.command")
        check("unknown command raises", False, "no exception")
    except BridgeError as e:
        check("unknown command lists options", "available commands" in str(e),
              str(e)[:80])

    section("concurrent clients")
    other = tcp_client(port, timeout=15)
    results = {}

    def worker(name, cl, cmd, kwargs):
        try:
            results[name] = cl.call(cmd, **kwargs)
        except Exception as e:  # noqa: BLE001
            results[name] = e

    threads = [
        threading.Thread(target=worker,
                         args=("scan", client, "mixer.list", {"only_used": False})),
        threading.Thread(target=worker, args=("ping", other, "ping", {})),
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=20)
    check("long scan answered", isinstance(results.get("scan"), dict)
          and len(results["scan"]["tracks"]) == 126, results.get("scan"))
    check("second client answered concurrently",
          isinstance(results.get("ping"), dict)
          and results["ping"].get("pong"), results.get("ping"))

    section("stopped bridge")
    client.close()
    other.close()
    stop.set()
    t.join(timeout=2)
    bridge.OnDeInit()
    dead = tcp_client(port, timeout=2)
    try:
        dead.ping()
        check("dead bridge reports clearly", False, "ping unexpectedly succeeded")
    except BridgeError as e:
        check("dead bridge reports clearly", "Could not reach" in str(e), str(e)[:60])

    return summary()


if __name__ == "__main__":
    sys.exit(main())
