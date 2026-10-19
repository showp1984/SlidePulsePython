"""Tests for slidepulse.py against a simulated device folder.

Run: python -m unittest discover -s tests
"""
import importlib.util
import json
import shlex
import socket
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("slidepulse", ROOT / "slidepulse.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)


class DeviceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.device = base / "SIDEPULSE"
        self.device.mkdir()
        self.led = self.device / "LEDS.LED"
        self.led.write_bytes(b"#ffffff\n")
        self.env = {"SLIDEPULSE_STATE_DIR": str(base / "state"), "SLIDEPULSE_DEVICE": str(self.device),
                    "SLIDEPULSE_NO_AGENT": "1"}
        for variable in ("COPILOT_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME", "GRADLE_USER_HOME"):
            self.env[variable] = str(base / variable.lower())  # never the real agent or Gradle settings
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.real_studio_dirs, sp.studio_config_dirs = sp.studio_config_dirs, lambda: []  # never the real IDE
        self.cfg = sp.load_config()

    def tearDown(self):
        sp.studio_config_dirs = self.real_studio_dirs
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def shown(self):
        return self.led.read_text(encoding="ascii")


class ProgramTests(DeviceTestCase):
    def test_every_event_builds_a_valid_program(self):
        for brightness in (255, 100):  # a brightness line makes the program longer
            self.cfg["brightness"] = brightness
            for name in sp.EVENTS:
                data = sp.validate(sp.build_program(name, self.cfg))
                self.assertLessEqual(len(data), sp.MAX_BYTES)

    def test_expected_programs(self):
        self.assertEqual(sp.build_program("copilot-done", self.cfg),
                         "off\n#0064ff 1s pulse\nrepeat 2\noff\n")
        self.assertEqual(sp.build_program("breakpoint", self.cfg), "\n".join([
            "#ff0000 #000000",
            "0:#000000 125ms linear;1:#ff0000 250ms pulse;2:#ff0000 250ms pulse 125ms;"
            "3:#ff0000 250ms pulse 250ms;4:#ff0000 250ms pulse 375ms;5:#ff0000 250ms pulse 500ms;"
            "6:#ff0000 250ms pulse 625ms;7:#ff0000 125ms linear 750ms",
            "7:#000000 125ms linear;6:#ff0000 250ms pulse;5:#ff0000 250ms pulse 125ms;"
            "4:#ff0000 250ms pulse 250ms;3:#ff0000 250ms pulse 375ms;2:#ff0000 250ms pulse 500ms;"
            "1:#ff0000 250ms pulse 625ms;0:#ff0000 125ms linear 750ms",
            "repeat", ""]))
        self.assertEqual(sp.build_program("claude-working", self.cfg),
                         "#ff5000 #4c1800 #140600\nroll 1s linear\nrepeat\n")
        installed = sp.build_program("installed", self.cfg).split("\n")
        self.assertEqual(installed[0], "#00e040 #000000")
        self.assertEqual(installed[3:], ["repeat 1", "off", ""])  # one sweep there and back, 1.75 s

    def test_running_light_goes_back_and_forth(self):
        _, there, back, _ = sp.build_program("breakpoint", self.cfg).split("\n", 3)
        order = lambda line: [int(seg.split(":")[0]) for seg in line.split(";")]
        self.assertEqual(order(there), list(range(8)))
        self.assertEqual(order(back), list(range(7, -1, -1)))
        self.assertEqual(sp.build_program("crash", self.cfg), "off\n#ff0000 300ms pulse\nrepeat\n")
        self.assertEqual(sp.build_program("build-failed", self.cfg),
                         "#ff0000 125ms none\noff 125ms none\nrepeat 8\noff\n")

    def test_brightness_line(self):
        self.cfg["brightness"] = 100
        self.assertTrue(sp.build_program("build-ok", self.cfg).startswith("brightness 100\n"))

    def test_validate_rejects_bad_programs(self):
        for bad in ("#ff0000 > x", "a" * 600, "\n".join(["off"] * 21), "#ff0000 é", "rm -rf $HOME"):
            with self.assertRaises(ValueError):
                sp.validate(bad)

    def test_bad_config_values_fall_back(self):
        (ROOT / "config.json").exists() and self.skipTest("a local config.json is present")
        self.assertFalse(sp._valid_setting("brightness", 0))
        self.assertFalse(sp._valid_setting("brightness", True))
        self.assertFalse(sp._valid_setting("crash_ignore", "android."))
        self.assertTrue(sp._valid_setting("device", str(self.device)))
        self.assertFalse(sp._valid_setting("device", "relative\\SIDEPULSE"))


class StateTests(DeviceTestCase):
    def test_flash_then_off(self):
        sp.apply("event", "build-ok", self.cfg, now=1000)
        self.assertIn("1s pulse", self.shown())
        with sp.locked_state() as state:
            sp.render(state, self.cfg, 1003)
        self.assertEqual(self.shown(), "off\n")

    def test_flash_restores_held_state(self):
        sp.apply("event", "breakpoint", self.cfg, now=1000)
        self.assertIn("#ff0000 #000000", self.shown())
        sp.apply("event", "copilot-done", self.cfg, now=1001)
        self.assertIn("#0064ff 1s pulse", self.shown())
        with sp.locked_state() as state:
            sp.render(state, self.cfg, 1004)
        self.assertIn("#ff0000 #000000", self.shown())

    def test_priority_crash_over_breakpoint_over_input(self):
        sp.apply("event", "copilot-input", self.cfg, now=1000)
        sp.apply("event", "crash", self.cfg, now=1001)
        sp.apply("event", "breakpoint", self.cfg, now=1002)
        self.assertIn("300ms pulse", self.shown())
        sp.apply("clear", "crash", self.cfg, now=1003)
        self.assertIn("#ff0000 #000000", self.shown())
        sp.apply("clear", "breakpoint", self.cfg, now=1004)
        self.assertIn("#0064ff #000000", self.shown())

    def test_copilot_done_clears_input_and_build_clears_crash(self):
        sp.apply("event", "copilot-input", self.cfg, now=1000)
        sp.apply("event", "copilot-done", self.cfg, now=1001)
        sp.apply("event", "crash", self.cfg, now=1002)
        sp.apply("event", "installed", self.cfg, now=1003)
        with sp.locked_state() as state:
            self.assertEqual(state["held"], {})
        sp.apply("event", "crash", self.cfg, now=1004)
        sp.apply("event", "build-failed", self.cfg, now=1005)
        with sp.locked_state() as state:
            self.assertEqual(state["held"], {})

    def test_agent_working_input_done(self):
        sp.apply("event", "claude-working", self.cfg, now=1000)
        self.assertIn("#ff5000 #4c1800 #140600\nroll", self.shown())  # one way round
        sp.apply("event", "claude-input", self.cfg, now=1001)
        self.assertIn("1:#ff5000 250ms pulse", self.shown())  # back and forth until answered
        sp.apply("event", "claude-working", self.cfg, now=1002)  # a tool ran after the approval
        self.assertIn("roll", self.shown())
        sp.apply("event", "claude-done", self.cfg, now=1003)
        self.assertIn("#ff5000 1s pulse", self.shown())
        with sp.locked_state() as state:
            self.assertEqual(state["held"], {})
            sp.render(state, self.cfg, 1006)
        self.assertEqual(self.shown(), "off\n")

    def test_agents_keep_their_own_states(self):
        sp.apply("event", "grok-input", self.cfg, now=1000)
        sp.apply("event", "chatgpt-working", self.cfg, now=1001)
        self.assertIn("1:#ffffff 250ms pulse", self.shown())  # input outranks working
        sp.apply("clear", "grok", self.cfg, now=1002)
        self.assertIn("#10a37f", self.shown())
        sp.apply("event", "chatgpt-done", self.cfg, now=1003)
        with sp.locked_state() as state:
            self.assertEqual(state["held"], {})

    def test_most_recently_active_agent_shows(self):
        sp.apply("event", "claude-working", self.cfg, now=1000)
        sp.apply("event", "copilot-working", self.cfg, now=1001)
        self.assertIn("#0064ff", self.shown())
        sp.apply("event", "claude-working", self.cfg, now=1002)
        self.assertIn("#ff5000", self.shown())

    def test_refreshing_a_hold_lets_a_flash_finish(self):
        sp.apply("event", "claude-working", self.cfg, now=1000)
        sp.apply("event", "build-ok", self.cfg, now=1001)
        sp.apply("event", "claude-working", self.cfg, now=1002)  # the next tool call
        self.assertIn("#00e040 1s pulse", self.shown())

    def test_working_expires_without_activity(self):
        sp.apply("event", "claude-working", self.cfg, now=1000)
        with sp.locked_state() as state:
            sp.render(state, self.cfg, 1000 + self.cfg["working_seconds"] + 1)
        self.assertEqual(self.shown(), "off\n")

    def test_held_states_expire(self):
        sp.apply("event", "crash", self.cfg, now=1000)
        with sp.locked_state() as state:
            sp.render(state, self.cfg, 1000 + self.cfg["crash_seconds"] + 1)
        self.assertEqual(self.shown(), "off\n")

    def test_repeated_hold_does_not_restart_animation(self):
        sp.apply("event", "copilot-input", self.cfg, now=1000)
        self.led.write_bytes(b"marker\n")
        sp.apply("event", "copilot-input", self.cfg, now=1001)
        self.assertEqual(self.shown(), "marker\n")

    def test_nothing_to_show_keeps_unknown_device_state(self):
        sp.apply("clear", "copilot-input", self.cfg, now=1000)
        self.assertEqual(self.shown(), "#ffffff\n")

    def test_failed_write_keeps_state(self):
        sp.apply("event", "breakpoint", self.cfg, now=1000)
        os.chmod(self.led, 0o444)  # read-only, like a drive that is still being scanned
        try:
            self.assertTrue(sp.apply("clear", "breakpoint", self.cfg, now=1001).startswith("write failed"))
        finally:
            os.chmod(self.led, 0o666)
        with sp.locked_state() as state:
            self.assertEqual(state["held"], {})  # the clear was kept ...
            sp.render(state, self.cfg, 1002)
        self.assertEqual(self.shown(), "off\n")  # ... and drawn on the retry

    def test_hostile_state_files_are_ignored(self):
        path = sp.state_dir() / "state.json"
        for text in ('{"flash": {"name": ["x"], "start": 1, "until": 2}}',
                     '{"held": {"crash": ' + "9" * 400 + '}}',
                     '{"held": {"crash": Infinity}}',
                     '[' * 100000 + ']' * 100000,
                     '{"held": []}', '"text"', '{not json'):
            path.write_text(text, encoding="utf-8")
            with sp.locked_state() as state:
                self.assertEqual(state["held"], {})
                self.assertIsNone(state["flash"])

    def test_relative_state_dir_is_rejected(self):
        os.environ["SLIDEPULSE_STATE_DIR"] = "relstate"
        try:
            with self.assertRaises(ValueError):
                sp.state_dir()
        finally:
            os.environ["SLIDEPULSE_STATE_DIR"] = self.env["SLIDEPULSE_STATE_DIR"]

    def test_missing_device_keeps_state(self):
        self.led.unlink()
        self.assertEqual(sp.apply("event", "breakpoint", self.cfg, now=1000), "no device")
        self.led.write_bytes(b"")
        with sp.locked_state() as state:
            sp.render(state, self.cfg, 1001)
        self.assertIn("#ff0000 #000000", self.shown())

    def test_corrupt_state_file_is_ignored(self):
        (sp.state_dir() / "state.json").write_text("{not json", encoding="utf-8")
        sp.apply("event", "breakpoint", self.cfg, now=1000)
        self.assertIn("250ms pulse 625ms", self.shown())


class WriteTests(DeviceTestCase):
    def test_writes_in_place_without_creating_files(self):
        before = sorted(p.name for p in self.device.iterdir())
        sp.write_program(self.led, "#ff00ff 1s pulse\nrepeat")
        self.assertEqual(self.shown(), "#ff00ff 1s pulse\nrepeat\n")
        sp.write_program(self.led, "off")
        self.assertEqual(self.shown(), "off\n")  # shorter program truncates the old one
        self.assertEqual(sorted(p.name for p in self.device.iterdir()), before)

    def test_never_creates_led_file(self):
        self.led.unlink()
        with self.assertRaises(OSError):
            sp.write_program(self.led, "off")
        self.assertFalse(self.led.exists())

    def test_refuses_symlink(self):
        target = Path(self.tmp.name) / "victim.txt"
        target.write_text("keep me", encoding="utf-8")
        self.led.unlink()
        try:
            os.symlink(target, self.led)
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        with self.assertRaises(OSError):
            sp.write_program(self.led, "off")
        self.assertEqual(target.read_text(encoding="utf-8"), "keep me")

    def test_refuses_large_file(self):
        self.led.write_bytes(b"x" * (sp.MAX_LED_FILE_BYTES + 1))
        with self.assertRaises(OSError):
            sp.write_program(self.led, "off")


class CrashParserTests(unittest.TestCase):
    def setUp(self):
        self.watcher = sp.CrashWatcher(sp.load_config(), lambda message: None)

    def test_java_crash(self):
        self.assertEqual(self.watcher.parse("E/AndroidRuntime( 4321): Process: com.example.app, PID: 4321"),
                         "com.example.app")

    def test_native_crash(self):
        line = ("F/libc    ( 4321): Fatal signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0 "
                "in tid 4321 (RenderThread), pid 4321 (com.example.app)")
        self.assertEqual(self.watcher.parse(line), "com.example.app")

    def test_tombstone_names_the_full_package(self):
        line = "F/DEBUG   ( 4400): pid: 4321, tid: 4321, name: example.app  >>> com.example.app <<<"
        self.assertEqual(self.watcher.parse(line), "com.example.app")

    def test_system_crash_ignored(self):
        self.assertIsNone(self.watcher.parse("E/AndroidRuntime( 99): Process: com.android.systemui, PID: 99"))
        self.assertIsNone(self.watcher.parse("E/AndroidRuntime( 99): FATAL EXCEPTION: main"))

    def test_native_daemon_crash_ignored(self):
        for name in ("surfaceflinger", "/system/bin/cameraserver"):
            line = f"F/libc    (  77): Fatal signal 6 (SIGABRT), code -1 in tid 77 (binder:77_2), pid 77 ({name})"
            self.assertIsNone(self.watcher.parse(line), name)


class FakeAdbServer:
    """Speaks just enough of the adb host protocol: host:devices, host:transport, shell:."""

    def __init__(self, crash_lines):
        self.crash_lines = crash_lines
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    @staticmethod
    def request(conn):
        size = int(conn.recv(4), 16)
        return conn.recv(size).decode()

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        with conn:
            request = self.request(conn)
            if request == "host:devices":
                body = b"emulator-5554\tdevice\nbad serial!\tdevice\n0123\toffline\n"
                conn.sendall(b"OKAY" + b"%04x" % len(body) + body)
            elif request == "host:transport:emulator-5554":
                conn.sendall(b"OKAY")
                if self.request(conn) == "shell:logcat -b crash -v brief -T 1":
                    conn.sendall(b"OKAY")
                    conn.sendall(b"E/AndroidRuntime(1): Process: com.old.backlog, PID: 1\r\n")
                    time.sleep(2)
                    conn.sendall(self.crash_lines)
                    time.sleep(5)
            else:
                conn.sendall(b"FAIL")

    def close(self):
        self.server.close()


class AdbTests(unittest.TestCase):
    def setUp(self):
        self.adb = FakeAdbServer(b"E/AndroidRuntime(42): FATAL EXCEPTION: main\r\n"
                                 b"E/AndroidRuntime(42): Process: com.example.app, PID: 42\r\n")
        os.environ["ANDROID_ADB_SERVER_PORT"] = str(self.adb.port)

    def tearDown(self):
        os.environ.pop("ANDROID_ADB_SERVER_PORT")
        self.adb.close()

    def test_devices_lists_only_ready_valid_serials(self):
        self.assertEqual(sp.adb_devices(self.adb.port), {"emulator-5554"})

    def test_crash_watcher_reports_new_crash_and_skips_backlog(self):
        watcher = sp.CrashWatcher(sp.load_config(), lambda message: None)
        try:
            watcher.poll()
            self.assertTrue(watcher.connected())
            deadline, apps = time.monotonic() + 8, []
            while not apps and time.monotonic() < deadline:
                time.sleep(0.1)
                apps = watcher.take()
            self.assertEqual(apps, ["com.example.app"])
        finally:
            watcher.close()

    def test_no_server_means_no_devices(self):
        self.adb.close()
        with self.assertRaises(OSError):
            sp.adb_devices(self.adb.port)


class InstallTests(DeviceTestCase):
    def test_copilot_hooks(self):
        doc = sp.agent_hooks("copilot")
        self.assertEqual(doc["version"], 1)
        self.assertEqual(set(doc["hooks"]), set(sp.AGENT_HOOKS["copilot"]))
        notification = doc["hooks"]["notification"][0]
        self.assertEqual(notification["matcher"], "permission_prompt|elicitation_dialog")
        self.assertEqual(notification["exec"], sys.executable)  # no shell, nothing to quote
        self.assertEqual(notification["args"],
                         ["-I", str(ROOT / "slidepulse.py"), "hook", "copilot", "notification"])
        self.assertFalse({"bash", "powershell", "command"} & set(notification))

    def test_claude_hooks_start_python_directly(self):
        hooks = sp.agent_hooks("claude")["hooks"]
        self.assertNotIn("Idle", hooks)  # an idle_prompt Notification hook, not an event of its own
        self.assertEqual([g["matcher"] for g in hooks["Notification"]],
                         ["permission_prompt|elicitation_dialog|elicitation_url_dialog", "idle_prompt"])
        handler = hooks["Stop"][0]["hooks"][0]
        self.assertEqual(handler["command"], sys.executable)
        self.assertEqual(handler["args"], ["-I", str(ROOT / "slidepulse.py"), "hook", "claude", "Stop"])

    def test_codex_and_grok_command_lines_quote_every_part(self):
        for agent in ("chatgpt", "grok"):
            for event, groups in sp.agent_hooks(agent)["hooks"].items():
                for group in groups:
                    argv = shlex.split(group["hooks"][0]["command"])
                    self.assertEqual(argv[:5], [sys.executable, "-I", str(ROOT / "slidepulse.py"), "hook", agent])
                    self.assertIn(argv[5], sp.AGENT_HOOKS[agent])
        self.assertEqual(sp.shell_command(["/a b/py", "it's"]), "'/a b/py' 'it'\"'\"'s'")

    def test_every_hook_key_has_a_meaning(self):
        for agent, hooks in sp.AGENT_HOOKS.items():
            for key, meaning in hooks.items():
                self.assertIn(meaning, ("working", "input", "done", "off"), (agent, key))

    def test_groovy_quoting(self):
        self.assertEqual(sp.groovy_quote("C:\\it's"), "'C:\\\\it\\'s'")
        self.assertEqual(sp.groovy_quote("a\nb${x}"), "'a\\nb${x}'")  # single quotes: no interpolation

    def test_templates_fill_completely(self):
        for label, dest, content in sp.integration_files(Path(self.tmp.name) / "AndroidStudio2099.1"):
            self.assertNotIn("{{", content, label)

    def test_install_and_uninstall_in_temp_homes(self):
        base = Path(self.tmp.name)
        studio = base / "AndroidStudio2099.1"
        self.assertEqual(sp.install(False, str(studio)), 0)
        files = [dest for _, dest, _ in sp.integration_files(studio)]
        self.assertTrue(all(f.is_file() for f in files))
        foreign = base / "gradle_user_home" / "init.d" / "slidepulse.gradle"
        foreign.write_text("// someone else's script\n", encoding="utf-8")
        self.assertEqual(sp.install(False, str(studio)), 1)  # refuses to overwrite it
        sp.uninstall(str(studio))
        self.assertTrue(foreign.exists())
        self.assertFalse(any(f.exists() for f in files if f != foreign))
        self.assertFalse(any(dest.exists() for _, dest, _ in sp.shared_hook_files()))  # held only ours

    def test_shared_hook_files_keep_everything_else(self):
        settings = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
        settings.parent.mkdir()
        mine = {"attribution": {"commit": ""},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}
        settings.write_text(json.dumps(mine), encoding="utf-8")
        studio = str(Path(self.tmp.name) / "AndroidStudio2099.1")
        sp.install(False, studio)
        sp.install(False, studio)  # again: replaces its hooks instead of adding a second set
        doc = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(doc["attribution"], {"commit": ""})
        self.assertEqual(len(doc["hooks"]["Stop"]), 2)
        self.assertEqual(doc["hooks"]["Stop"][0], mine["hooks"]["Stop"][0])
        sp.uninstall(studio)
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), mine)

    def test_unreadable_shared_file_is_left_alone(self):
        hooks = Path(os.environ["CODEX_HOME"]) / "hooks.json"
        hooks.parent.mkdir()
        hooks.write_text("{not json", encoding="utf-8")
        studio = str(Path(self.tmp.name) / "AndroidStudio2099.1")
        self.assertEqual(sp.install(False, studio), 1)
        sp.uninstall(studio)
        self.assertEqual(hooks.read_text(encoding="utf-8"), "{not json")


class CliTests(DeviceTestCase):
    def run_cli(self, *args, env=None, stdin=None):
        return subprocess.run([sys.executable, "-I", str(ROOT / "slidepulse.py"), *args],
                              capture_output=True, text=True, env=env or {**os.environ, **self.env},
                              input=stdin, timeout=30)

    def wait_for_led(self, text, seconds=5.0):
        deadline = time.monotonic() + seconds
        while text not in self.shown() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIn(text, self.shown())

    def test_copilot_hook_exits_zero_even_without_device(self):
        self.led.unlink()
        result = self.run_cli("copilot", "agentStop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_cli("copilot", "someFutureHook").returncode, 0)

    def test_agent_hooks_from_cli(self):
        self.assertEqual(self.run_cli("hook", "grok", "UserPromptSubmit").returncode, 0)
        self.assertIn("#ffffff #4c4c4c #141414", self.shown())
        self.assertEqual(self.run_cli("hook", "grok", "StopCancelled").returncode, 0)
        self.assertEqual(self.shown(), "off\n")
        self.assertEqual(self.run_cli("copilot", "userPromptSubmitted").returncode, 0)  # 1.1 hook files
        self.assertIn("#0064ff #001e4c", self.shown())

    def test_hooks_never_fail(self):
        for args in (["hook", "claude", "SomeFutureEvent"], ["hook", "nobody", "Stop"], ["hook"],
                     ["hook", "claude", "Stop", "extra"], ["event", "no-such-event"]):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, args)  # exit 2 would keep Claude, Codex or Grok going
            self.assertEqual(result.stdout, "", args)  # Claude adds a hook's stdout to its context

    def test_hook_exits_cleanly_when_stdin_stays_open(self):
        process = subprocess.Popen([sys.executable, "-I", str(ROOT / "slidepulse.py"), "hook", "claude", "Stop"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, **self.env})
        try:
            self.assertEqual(process.wait(timeout=10), 0)  # not an abort at interpreter shutdown
            self.assertNotIn(b"Fatal Python error", process.stderr.read())
        finally:
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_claude_hook_ignores_grok(self):
        env = {**os.environ, **self.env, "GROK_HOOK_EVENT": "stop"}
        self.assertEqual(self.run_cli("hook", "claude", "UserPromptSubmit", env=env).returncode, 0)
        self.assertEqual(self.shown(), "#ffffff\n")

    def test_copilot_hook_drains_a_large_payload_quickly(self):
        started = time.monotonic()
        result = self.run_cli("copilot", "postToolUse", stdin=json.dumps({"toolResult": "x" * 5_000_000}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started, sp.HOOK_DEADLINE + 1)

    def test_hook_survives_hostile_state_file(self):
        (sp.state_dir() / "state.json").write_text("[" * 100000, encoding="utf-8")
        self.assertEqual(self.run_cli("event", "breakpoint").returncode, 0)
        self.assertIn("250ms pulse 625ms", self.shown())

    def test_event_from_cli(self):
        self.assertEqual(self.run_cli("event", "breakpoint").returncode, 0)
        self.assertIn("250ms pulse 625ms", self.shown())
        self.assertEqual(self.run_cli("clear", "breakpoint").returncode, 0)
        self.assertEqual(self.shown(), "off\n")

    def test_hook_starts_agent_which_draws_and_restores(self):
        env = {**os.environ, **self.env}
        env.pop("SLIDEPULSE_NO_AGENT")
        try:
            self.assertEqual(self.run_cli("event", "breakpoint", env=env).returncode, 0)
            self.wait_for_led("#ff0000 #000000")  # drawn by the agent the hook started
            self.assertTrue(sp.agent_running())
            self.run_cli("event", "build-ok", env=env)
            self.wait_for_led("#00e040 1s pulse", seconds=1)
            self.wait_for_led("#ff0000 #000000", seconds=4)  # back to the breakpoint light
            self.run_cli("clear", "breakpoint", env=env)
            self.wait_for_led("off\n", seconds=1)
        finally:
            self.assertEqual(self.run_cli("stop", env=env).returncode, 0)
        self.assertFalse(sp.agent_running())

    def test_write_rejects_unsafe_program(self):
        result = self.run_cli("write", "#ff0000; $(calc)")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.shown(), "#ffffff\n")


if __name__ == "__main__":
    unittest.main()
