# slidepulse

A small, auditable controller for SidePulse-compatible LED lights (the SD-card
"SidePulse Pro" and the USB-C "Dot"). It lights them up for GitHub Copilot and
Android Studio events. One Python file, standard library only, no network access.

| Event | Detected from | Light |
|---|---|---|
| `copilot-done` | Copilot finished its turn (`agentStop` hook) | blue pulse, 2 s |
| `copilot-input` | Copilot asks a question or needs a permission (`notification` hook) | blue running light until you answer |
| `build-ok` | A Gradle build with an assemble/bundle/build/install task succeeded | green pulse, 2 s |
| `build-failed` | Such a build failed (cancelled builds are ignored) | quick red blinks, 2 s |
| `installed` | Android Studio reports "Install successfully finished" | green running light, 2 s |
| `crash` | `adb logcat -b crash` shows an app crash (Java or native) | fast red pulse until the next build/install (max 60 s) |
| `breakpoint` | The Android Studio debugger is paused | red running light while paused |

A 2-second flash plays over a held state and then returns to it. Held states
rank crash > breakpoint > Copilot input. A finished turn clears "needs input",
and a new build (passed or failed) or install clears a crash.

## Quick start (Windows)

1. Plug the light in. It shows up as a small drive whose name starts with `SIDEPULSE` or `PULSEDOT`.
2. Check that it reacts:

   ```bash
   py -3.13 slidepulse.py demo
   ```

3. Install the integrations (Copilot hooks, Gradle init script, Android Studio bridge):

   ```bash
   py -3.13 slidepulse.py install
   ```

4. Restart Android Studio and start a new Copilot CLI session.

`install` stores the absolute path of the Python that ran it, so use the Python
you want to keep (3.9 or newer). Run `install` again after moving the folder.
`py -3.13 slidepulse.py install --dry-run` shows the files without writing them.

## How each event is detected

**Copilot.** `install` writes the user-level hook file `%USERPROFILE%\.copilot\hooks\slidepulse.json`.
It is used by the Copilot CLI in a terminal and by the **Copilot CLI** agent inside Android Studio.
The Copilot plugin's other agent mode (plugin 1.11.1) only offers session, prompt, tool and error hooks
from a project's `.github/hooks` folder. It has no turn-finished or needs-input hook, so pick
"Copilot CLI" in the Copilot chat's agent picker. Hook files in `~/.copilot/hooks` and the
`notification` event are recent CLI features; if a light never shows up, run `copilot update`.

**Build passed or failed.** `%USERPROFILE%\.gradle\init.d\slidepulse.gradle` loads
`%USERPROFILE%\.gradle\slidepulse\build-done.gradle` on Gradle 8.1 or newer. It uses Gradle
dataflow actions, so it works with the configuration cache. It reacts only to builds that run an
assemble, bundle, build or install task, so Android Studio sync, `clean` and test runs stay dark.
A build you stop is not reported as a failure. Command-line builds count too.

**Installed and breakpoint.** Android Studio runs `slidepulse.groovy` from
`<settings>\extensions\com.intellij\startup\` once per start. It reads the debugger state every
300 ms and waits 600 ms before reporting a resume, so stepping does not flicker. It also listens
for the "Install successfully finished" notification. It does not change projects or files. It also
starts the background agent.

**Crash.** The background agent follows `adb logcat -b crash` for each connected device. It only
talks to an adb server that is already running (Android Studio starts one) and never starts one
itself. System processes (`android.*`, `com.android.*`, `com.google.android.*`, `system_server`)
are ignored.

## Background agent

The agent restores a held state after a flash, expires stale states, redraws the light after it
is re-plugged and watches for crashes. Any event starts it on demand. It runs detached, without a
window, in a few MB of memory.

```bash
py -3.13 slidepulse.py status
```

```bash
py -3.13 slidepulse.py stop
```

Its log is `%LOCALAPPDATA%\slidepulse\agent.log`. Set `"agent": false` to never start it; events
still show, but flashes no longer return to a held state.

## Configuration

Optional `config.json` next to `slidepulse.py`. Every key is optional; these are the defaults:

```json
{
  "device": null,
  "brightness": 255,
  "colors": {"copilot": "#0064ff", "build": "#00e040", "alert": "#ff0000"},
  "agent": true,
  "crash_watch": true,
  "crash_seconds": 60,
  "crash_ignore": ["android.", "com.android.", "com.google.android.", "system_server"],
  "hold_max_seconds": 3600,
  "adb": null,
  "keepalive": null
}
```

- `device`: drive (for example `"E:\\"`) or `LEDS.LED` path, if auto-detection does not find it.
  The `SLIDEPULSE_DEVICE` environment variable does the same.
- `hold_max_seconds`: held states expire after this, in case nobody clears them.
- `keepalive`: touch a `keepalive` file on the drive every minute. MacBook SD readers power the
  card off after 3 minutes of inactivity, so this defaults to on for macOS only.

## Commands

```text
event <copilot-done|copilot-input|build-ok|build-failed|installed|crash|breakpoint>
clear <copilot-input|breakpoint|crash|all>
copilot <hookEvent>          entry point for Copilot CLI hooks
write "<program>"            raw LEDS.LED program, for example "#ff00ff 1s pulse\nrepeat"
off | status | demo | start | stop | agent
install [--dry-run] [--studio-dir DIR] | uninstall [--studio-dir DIR]
```

## Security notes

- **No network, no listening sockets, no shell, no `eval`.** Processes start from argument lists.
  The only shell is Copilot's own hook runner, which runs one fixed, quoted command.
- **Writes are narrow.** The tool rewrites `LEDS.LED` in place and never creates, renames or deletes
  it. It refuses links and reparse points, and refuses files over 64 KB. Programs are checked against
  the device limits (512 bytes, 20 lines) and a character allowlist before writing.
  Other writes: its own state folder, the optional `keepalive` file, and the four integration files
  on `install`. `install` will not overwrite a file it did not create, and `uninstall` only deletes its own.
- **Auto-detection is strict.** A drive must be FAT-formatted, have a SIDEPULS* or PULSEDOT* label,
  contain `LEDS.LED`, and be at most 64 MB. Ordinary SD cards and USB sticks are skipped.
- **No prompt data.** The Copilot hook input (prompts, tool arguments) is never read. The agent log
  holds only timestamps, errors and crashed package names.
- **Isolated Python.** Every integration runs `python -I`, which ignores `PYTHON*` environment
  variables, user site-packages and modules planted next to the script.
- **Fails open.** Hooks always exit 0 and take about 70 ms. The Gradle hook can never fail a build.
  The IDE bridge catches its own errors and logs them to `idea.log`. State updates use a lock with
  a 2-second timeout, so nothing hangs if the device stops responding.
- **adb input is untrusted.** Log lines are capped at 2,000 characters and only matched against two
  anchored patterns. Device serials are validated before use.

## Compliance

This is an independent implementation of the public `LEDS.LED` format described in
[`LEDS_FORMAT.md`](https://github.com/inteliwear/sidepulse/blob/main/LEDS_FORMAT.md)
(MIT license, © 2026 Peter Kuhar). No SidePulse source code was used or copied. This project is
not affiliated with or endorsed by SidePulse; the name is used only to describe compatibility.
The integrations use documented extension points: Copilot CLI hooks, Gradle init scripts and
dataflow actions, and IntelliJ IDE startup scripts.

## Verification status

Tested:

- 29 unit tests on Python 3.9 and 3.13 against a simulated device folder:
  `py -3.13 -m unittest discover -s tests`.
- Real builds on Gradle 9.4.1 and 8.14.4 with the configuration cache on: green on success
  (also when the configuration cache is reused), red blinks on failure, and dark for a simulated
  cancellation, `help` and sync.
- The Android Studio bridge compiles and type-checks against the Android Studio 2026.1.1 jars.
- Agent auto-start, flash-then-restore, and stop.

Not yet tested: the real light (not delivered yet), a live Android Studio debug or install session,
live Copilot hook delivery, and live crash detection from an emulator.

## Troubleshooting

- `status` shows the detected device, agent and held states.
- The light blinks red six times: the device rejected a program. Check any custom colors in `config.json`.
- Nothing in Android Studio: search `idea.log` (Help > Show Log in Explorer) for `slidepulse`.
- Uninstall everything:

  ```bash
  py -3.13 slidepulse.py uninstall
  ```
