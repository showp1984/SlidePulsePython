# slidepulse

A small, auditable controller for SidePulse-compatible LED lights (the SD-card
"SidePulse Pro" and the USB-C "Dot"). It lights them up for AI coding agents (GitHub Copilot,
Claude Code, ChatGPT's Codex and Grok) and Android Studio events. One Python file, standard library only.

Each agent has its own color: Copilot blue, Claude orange, ChatGPT `#10a37f`, Grok white.

| Event | Detected from | Light |
|---|---|---|
| `<agent>-working` | You sent a prompt, or the agent finished a tool call | running light one way round, in the agent's color, while it works |
| `<agent>-input` | The agent asks for a permission or a question | light sweeping back and forth until you answer, then back to working |
| `<agent>-done` | The agent finished its turn | pulse, 2 s, then dark |
| `build-ok` | A Gradle build with an assemble/bundle/build/install task succeeded | green pulse, 2 s |
| `build-failed` | Such a build failed (cancelled builds are ignored) | quick red blinks, 2 s |
| `installed` | Android Studio reports "Install successfully finished" | green light sweeping there and back once, 2 s |
| `crash` | The device crash log shows an app crash (Java or native) | fast red pulse until the next build/install (max 60 s) |
| `breakpoint` | The Android Studio debugger is paused | red light sweeping back and forth while paused |

`<agent>` is `copilot`, `claude`, `chatgpt` or `grok`. A 2-second flash plays over a held state
and then returns to it. Held states rank crash > breakpoint > an agent's input > an agent's working;
between two agents, the one active most recently shows. A finished turn clears the agent's lights.
An interrupt, error or closed session turns them off, and a new build (passed or failed) or install
clears a crash.

## Quick start (Windows)

1. Plug the light in. It shows up as a small drive whose name starts with `SIDEPULSE` or `PULSEDOT`.
2. Check that it reacts:

   ```bash
   py -3.13 slidepulse.py demo
   ```

3. Install the integrations (agent hooks, Gradle init script, Android Studio bridge):

   ```bash
   py -3.13 slidepulse.py install
   ```

4. Restart Android Studio and start new agent sessions. Codex skips hooks you have not trusted,
   and the ChatGPT app does not ask: start the Codex CLI (on macOS the app's copy is
   `/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex`), type `/hooks` and trust
   the six slidepulse hooks. The app reads the same `~/.codex`, so this covers it too. Trust is
   tied to each hook's exact command, so do it again after `install` from another folder or Python.

`install` stores the absolute path of the Python that ran it, so use the Python
you want to keep (3.9 or newer). Run `install` again after moving the folder.
`py -3.13 slidepulse.py install --dry-run` shows the files without writing them. On macOS, run
the same commands with `python3`.

## How it works

Hooks only record what happened in a small state file and exit, in about 60 ms and
never later than 3 s. A background agent is the only process that writes to the light:
it draws each change within 0.1 s, returns to a held state after a flash, expires stale
states, redraws after the light is re-plugged and watches for app crashes. Any event
starts the agent; it runs detached and without a window, and exits after 30 idle minutes
until the next event starts it again.

**AI agents.** Every agent's hooks call `slidepulse.py hook <agent> <event>`. A prompt or a finished
tool call means working, a permission prompt or question means input, and the end of a turn means done.

| Agent | Hook file | Started by |
|---|---|---|
| Copilot | `~/.copilot/hooks/slidepulse.json` (own file) | Python directly |
| Claude Code | `~/.claude/settings.json` (shared: slidepulse adds and removes only its own entries) | Python directly |
| ChatGPT (Codex) | `~/.codex/hooks.json` (shared, as above) | a quoted command line |
| Grok Build | `~/.grok/hooks/slidepulse.json` (own file) | a quoted command line |

`~` is `%USERPROFILE%` on Windows; `COPILOT_HOME`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME` and `GROK_HOME`
move them. Claude's hooks also fire in the Claude desktop app's Code tab and the IDE extensions.
Codex's fire in the Codex inside the ChatGPT app and in the Codex CLI, once you trust them with `/hooks`.
The ChatGPT and Claude chat apps themselves have no hooks.
Claude has no interrupt hook: after you stop a turn, its idle notification (about a minute later)
turns the light off. Grok also runs Claude's hooks from `~/.claude/settings.json`; slidepulse's
Claude hook ignores those calls, because Grok's own file reports them.

**Copilot.** The hook file `%USERPROFILE%\.copilot\hooks\slidepulse.json`
uses Copilot's native `exec` hooks, so Copilot starts Python directly, with no shell.
It is used by the Copilot CLI in a terminal and by the **Copilot CLI** agent inside Android Studio.
The Copilot plugin's other agent mode (plugin 1.11.1) only offers session, prompt, tool and error hooks
from a project's `.github/hooks` folder. It has no turn-finished or needs-input hook, so pick
"Copilot CLI" in the Copilot chat's agent picker. If a Copilot light never shows up, run `copilot update`.

**Build passed or failed.** `%USERPROFILE%\.gradle\init.d\slidepulse.gradle` loads
`%USERPROFILE%\.gradle\slidepulse\build-done.gradle` on Gradle 8.1 or newer. It uses Gradle
dataflow actions, so it works with the configuration cache. It reacts only to builds whose task
is `assemble*`, `bundle*`, `install*` or `build`, so Android Studio sync, `clean`, tests and
`uninstallDebug` stay dark. A build you stop is not reported as a failure. Command-line builds count too.

**Installed and breakpoint.** Android Studio runs `slidepulse.groovy` from
`<settings>\extensions\com.intellij\startup\` once per start. It reads the debugger state every
300 ms and waits 600 ms before reporting a resume, so stepping does not flicker. It also listens
for the "Install successfully finished" notification (Android Studio's English text). It runs its
commands in order on one background thread and does not change projects or files.

**Crash.** The agent follows `logcat -b crash` for each connected device by talking to the adb
server that Android Studio already runs, through adb's local protocol on `127.0.0.1:5037`
(or `ANDROID_ADB_SERVER_PORT`). It never runs or starts an `adb` program, so it cannot restart a
server of another version and drop Android Studio's devices. System processes and native daemons
are ignored.

```bash
py -3.13 slidepulse.py status
```

```bash
py -3.13 slidepulse.py stop
```

The agent's log is `%LOCALAPPDATA%\slidepulse\agent.log`. With `"agent": false`, hooks draw
directly instead; flashes then no longer return to a held state and crashes are not watched.

## Configuration

Optional `config.json` next to `slidepulse.py`. Every key is optional; these are the defaults:

```json
{
  "device": null,
  "brightness": 255,
  "colors": {"copilot": "#0064ff", "claude": "#ff5000", "chatgpt": "#10a37f", "grok": "#ffffff",
             "build": "#00e040", "alert": "#ff0000"},
  "agent": true,
  "crash_watch": true,
  "crash_seconds": 60,
  "crash_ignore": ["android.", "com.android.", "com.google.android.", "system_server"],
  "working_seconds": 900,
  "hold_max_seconds": 3600,
  "keepalive": null
}
```

- `device`: absolute drive (for example `"E:\\"`) or `LEDS.LED` path, if auto-detection does not
  find it. The `SLIDEPULSE_DEVICE` environment variable does the same.
- `colors.alert` is used for crash, breakpoint and failed builds.
- `working_seconds`: an agent's working light ends this long after its last prompt or tool call,
  in case the agent quits without a hook.
- `hold_max_seconds`: held states expire after this, in case nobody clears them.
- `keepalive`: touch a `keepalive` file on the drive every minute. MacBook SD readers power the
  card off after 3 minutes of inactivity, so this defaults to on for macOS only.

## Commands

```text
event <agent-working|agent-input|agent-done|build-ok|build-failed|installed|crash|breakpoint>
clear <agent-working|agent-input|agent|breakpoint|crash|all>     agent: copilot, claude, chatgpt, grok
hook <agent> <hookEvent>     entry point for the agents' hooks (copilot <hookEvent> still works)
write "<program>"            raw LEDS.LED program, for example "#ff00ff 1s pulse\nrepeat"
off | status | demo | start | stop | agent
install [--dry-run] [--studio-dir DIR] | uninstall [--studio-dir DIR]
```

## Security notes

- **No `eval`, no listening sockets, and a shell only where an agent requires one.** Processes start
  from an argument list, including the Copilot and Claude hooks. Codex and Grok accept only a command
  line, so slidepulse quotes every part of it; on Windows it refuses paths that would need quoting.
  The only network use is a connection to the local adb server on `127.0.0.1`.
- **Writes are narrow.** The tool rewrites `LEDS.LED` in place and never creates, renames or deletes
  it. It refuses links, reparse points, hard-linked files and files over 64 KB. Programs are checked
  against the device limits (512 bytes, 20 lines) and a character allowlist before writing.
  Other writes: its own state folder, the optional `keepalive` file, and the integration files on
  `install`. `install` will not overwrite a file it did not create, and `uninstall` only deletes its own.
  In the shared Claude and Codex files it replaces only entries that run `slidepulse.py` and leaves the
  rest as it was; it leaves a file it cannot read as JSON untouched.
- **Auto-detection is strict.** A drive must be FAT-formatted, have a SIDEPULS* or PULSEDOT* label,
  contain `LEDS.LED`, and be at most 64 MB. Ordinary SD cards and USB sticks are skipped.
- **No prompt data.** The agents' hook input (prompts, tool output) is read only to drain the pipe and
  is discarded unparsed. The agent log holds only timestamps, errors, device serials and crashed
  package names.
- **Isolated processes.** Every integration runs `python -I`, which ignores `PYTHON*` environment
  variables, user site-packages and modules planted next to the script. The agent keeps only an
  allowlisted environment, so tokens or signing passwords of the process that started it are dropped.
- **Fails open.** Hooks always exit 0, print nothing on stdout and never touch the device, so a stalled
  drive cannot hold up an agent or Gradle, and a hook can never make Claude, Codex or Grok keep going. The Gradle hook can never fail a build. The IDE bridge logs its own errors to
  `idea.log`. A corrupt or hostile state file is ignored and replaced.
- **Untrusted input is bounded.** Log lines are read at most 4 KB at a time and matched against three
  simple patterns. Device serials and adb replies are validated before use.

## Compliance

This is an independent implementation of the public `LEDS.LED` format described in
[`LEDS_FORMAT.md`](https://github.com/inteliwear/sidepulse/blob/main/LEDS_FORMAT.md)
(MIT license, © 2026 Peter Kuhar). No SidePulse source code was used or copied. This project is
not affiliated with or endorsed by SidePulse; the name is used only to describe compatibility.
The integrations use documented extension points: the hooks of Copilot CLI, Claude Code, Codex
and Grok Build, Gradle init scripts and
dataflow actions, IntelliJ IDE startup scripts, and adb's host protocol.

## Verification status

Tested:

- 54 unit tests on Python 3.10 to 3.14 on macOS (and the earlier set on 3.9 and 3.13 on Windows)
  against a simulated device folder and a fake adb server: `py -3.13 -m unittest discover -s tests`.
  They cover the state machine, write safety, hostile state files, crash parsing and detection, the
  agent started by a hook, large agent payloads, hooks whose stdin stays open, and merging into
  shared hook files.
- `demo` and the back-and-forth light on a SidePulse Pro (firmware 27270.3399) on macOS.
- Grok Build 1.0.46 loads the generated hook file (`grok inspect`), and the Codex in ChatGPT
  26.1013 knows every hook event the Codex file uses.
- Real builds on Gradle 9.4.1 and 8.14.4 with the configuration cache on: green on success
  (also when the configuration cache is reused), red blinks on failure, and dark for a simulated
  cancellation, `help`, `uninstallDebug`, `buildEnvironment` and sync.
- The Android Studio bridge compiles and type-checks against the Android Studio 2026.1.1 jars.
- An independent security review; its findings are fixed.

Not yet tested: a live Android Studio debug or install session, live hook delivery from Copilot,
Claude, Codex and Grok, the agent colors on the light, the Dot, and crash detection from a real emulator.

Known limits: a re-plug faster than 2 seconds is not noticed, so a held light may need the next
event to come back. Lights are kept per agent, not per session: when one of two Claude sessions
finishes, Claude's working light goes dark until the other one's next tool call. After the agent has idled out, crash watching resumes with the next event
(normally the build before you run the app).

## Troubleshooting

- `status` shows the detected device, agent, adb server and held states.
- The light blinks red six times: the device rejected a program. Check any custom colors in `config.json`.
- Nothing in Android Studio: search `idea.log` (Help > Show Log in Explorer) for `slidepulse`.
- Uninstall everything:

  ```bash
  py -3.13 slidepulse.py uninstall
  ```
