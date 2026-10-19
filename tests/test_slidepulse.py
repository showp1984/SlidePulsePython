"""Tests for slidepulse.py against a simulated device folder.

Run: python -m unittest discover -s tests
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
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
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.cfg = sp.load_config()

    def tearDown(self):
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
        for name in sp.EVENTS:
            data = sp.validate(sp.build_program(name, self.cfg))
            self.assertLessEqual(len(data), sp.MAX_BYTES)

    def test_expected_programs(self):
        self.assertEqual(sp.build_program("copilot-done", self.cfg),
                         "off\n#0064ff 1s pulse\nrepeat 2\noff\n")
        self.assertEqual(sp.build_program("installed", self.cfg),
                         "#00e040 #004313 #001205\nroll 1s linear\nrepeat 2\noff\n")
        self.assertEqual(sp.build_program("breakpoint", self.cfg),
                         "#ff0000 #4c0000 #140000\nroll 1s linear\nrepeat\n")
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
        self.assertTrue(sp._valid_setting("device", "E:\\"))


class StateTests(DeviceTestCase):
    def test_flash_then_off(self):
        sp.apply("event", "build-ok", self.cfg, now=1000)
        self.assertIn("1s pulse", self.shown())
        with sp.locked_state() as state:
            sp.render(state, self.cfg, 1003)
        self.assertEqual(self.shown(), "off\n")

    def test_flash_restores_held_state(self):
        sp.apply("event", "breakpoint", self.cfg, now=1000)
        self.assertIn("#ff0000 #4c0000", self.shown())
        sp.apply("event", "copilot-done", self.cfg, now=1001)
        self.assertIn("#0064ff 1s pulse", self.shown())
        with sp.locked_state() as state:
            sp.render(state, self.cfg, 1004)
        self.assertIn("#ff0000 #4c0000", self.shown())

    def test_priority_crash_over_breakpoint_over_input(self):
        sp.apply("event", "copilot-input", self.cfg, now=1000)
        sp.apply("event", "crash", self.cfg, now=1001)
        sp.apply("event", "breakpoint", self.cfg, now=1002)
        self.assertIn("300ms pulse", self.shown())
        sp.apply("clear", "crash", self.cfg, now=1003)
        self.assertIn("#ff0000 #4c0000", self.shown())
        sp.apply("clear", "breakpoint", self.cfg, now=1004)
        self.assertIn("#0064ff #001e4c", self.shown())

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

    def test_missing_device_keeps_state(self):
        self.led.unlink()
        self.assertEqual(sp.apply("event", "breakpoint", self.cfg, now=1000), "no device")
        self.led.write_bytes(b"")
        with sp.locked_state() as state:
            sp.render(state, self.cfg, 1001)
        self.assertIn("#ff0000 #4c0000", self.shown())

    def test_corrupt_state_file_is_ignored(self):
        (sp.state_dir() / "state.json").write_text("{not json", encoding="utf-8")
        sp.apply("event", "breakpoint", self.cfg, now=1000)
        self.assertIn("roll", self.shown())


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
        cfg = sp.load_config()
        cfg["adb"] = None
        self.watcher = sp.CrashWatcher(cfg, lambda message: None)

    def test_java_crash(self):
        self.assertEqual(self.watcher.parse("E/AndroidRuntime( 4321): Process: com.example.app, PID: 4321"),
                         "com.example.app")

    def test_native_crash(self):
        line = ("F/libc    ( 4321): Fatal signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0 "
                "in tid 4321 (RenderThread), pid 4321 (com.example.app)")
        self.assertEqual(self.watcher.parse(line), "com.example.app")

    def test_system_crash_ignored(self):
        self.assertIsNone(self.watcher.parse("E/AndroidRuntime( 99): Process: com.android.systemui, PID: 99"))
        self.assertIsNone(self.watcher.parse("E/AndroidRuntime( 99): FATAL EXCEPTION: main"))


class InstallTests(DeviceTestCase):
    def test_copilot_hooks_json(self):
        doc = json.loads(sp.copilot_hooks_json())
        self.assertEqual(doc["version"], 1)
        self.assertEqual(set(doc["hooks"]), set(sp.COPILOT_HOOKS))
        notification = doc["hooks"]["notification"][0]
        self.assertEqual(notification["matcher"], "permission_prompt|elicitation_dialog")
        self.assertTrue(notification["powershell"].startswith("& '"))
        self.assertTrue(notification["powershell"].endswith("'copilot' 'notification'"))

    def test_quoting(self):
        self.assertEqual(sp.ps_quote("C:\\it's"), "'C:\\it''s'")
        self.assertEqual(sp.groovy_quote("C:\\it's"), "'C:\\\\it\\'s'")

    def test_templates_fill_completely(self):
        for label, dest, content in sp.integration_files(Path(self.tmp.name) / "AndroidStudio2099.1"):
            self.assertNotIn("{{", content, label)

    def test_install_and_uninstall_in_temp_homes(self):
        base = Path(self.tmp.name)
        os.environ["COPILOT_HOME"] = str(base / "copilot")
        os.environ["GRADLE_USER_HOME"] = str(base / "gradle")
        try:
            studio = base / "AndroidStudio2099.1"
            self.assertEqual(sp.install(False, str(studio)), 0)
            files = [dest for _, dest, _ in sp.integration_files(studio)]
            self.assertTrue(all(f.is_file() for f in files))
            foreign = base / "gradle" / "init.d" / "slidepulse.gradle"
            foreign.write_text("// someone else's script\n", encoding="utf-8")
            self.assertEqual(sp.install(False, str(studio)), 1)  # refuses to overwrite it
            sp.uninstall(str(studio))
            self.assertTrue(foreign.exists())
            self.assertFalse(any(f.exists() for f in files if f != foreign))
        finally:
            os.environ.pop("COPILOT_HOME")
            os.environ.pop("GRADLE_USER_HOME")


class CliTests(DeviceTestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(ROOT / "slidepulse.py"), *args],
                              capture_output=True, text=True, env={**os.environ, **self.env}, timeout=30)

    def test_copilot_hook_exits_zero_even_without_device(self):
        self.led.unlink()
        result = self.run_cli("copilot", "agentStop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_cli("copilot", "someFutureHook").returncode, 0)

    def test_event_from_cli(self):
        self.assertEqual(self.run_cli("event", "breakpoint").returncode, 0)
        self.assertIn("roll 1s linear", self.shown())
        self.assertEqual(self.run_cli("clear", "breakpoint").returncode, 0)
        self.assertEqual(self.shown(), "off\n")

    def test_agent_restores_held_state_after_flash(self):
        env = {**os.environ, **self.env}
        env.pop("SLIDEPULSE_NO_AGENT")
        self.assertEqual(self.run_cli("event", "breakpoint").returncode, 0)
        agent = subprocess.Popen([sys.executable, "-I", str(ROOT / "slidepulse.py"), "agent"], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not sp.agent_running() and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue(sp.agent_running())
            self.run_cli("event", "build-ok")
            self.assertIn("#00e040 1s pulse", self.shown())
            time.sleep(3)
            self.assertIn("#ff0000 #4c0000", self.shown())  # back to the breakpoint light
            self.assertEqual(self.run_cli("stop").returncode, 0)
            agent.wait(timeout=10)
        finally:
            if agent.poll() is None:
                agent.kill()

    def test_write_rejects_unsafe_program(self):
        result = self.run_cli("write", "#ff0000; $(calc)")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.shown(), "#ffffff\n")


if __name__ == "__main__":
    unittest.main()
