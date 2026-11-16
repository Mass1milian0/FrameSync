# VIBECODE WARNING!!!!
This project is 100% fully vibecoded, every single line and feature comes from a LLM, all the tests, EVERYTHING!!!!

This was initially made for me and me alone, i just released it publicly in case someone else finds it useful too!

I REPEAT **NO THING HERE IS HAND-WRITTEN!!!!**
please i cannot stress this enough, the only hand written lines here are this warning in the readme you are seeing right now! 

Take precautions, do not execute random code from the internet, there is 100% a chance that there is some security vulnerability and risk involved

there is a chance something does not work as expected, and you should be prepared for potential bugs and edge cases

you have been warned

# FrameSync

FrameSync is an experimental, text-only clipboard bridge between Windows and
the Steam Frame. Synchronization between Windows, Game Mode, and the nested
Plasma desktop has been tested on a real device. Games using other clipboard
paths remain unverified.

## What the research establishes

The Frame has separate clipboards for its outer Steam/SteamVR session and its
nested Plasma desktop; the latter also runs a separate D-Bus session. The
`steam-frame-nix` clipboard module explicitly describes the Steam-session X
displays and nested desktop XWayland `:2` as separate clipboards. It starts
[`dnut/clipboard-sync`](https://github.com/dnut/clipboard-sync) from KDE
autostart, after the nested display exists. Upstream enumerates X11 displays
and Wayland display sockets and mirrors text between distinct clipboard
instances. Its Wayland backend uses `wl-clipboard-rs` and the
`zwlr_data_control_manager_v1` protocol; its X11 backend uses
`terminal-clipboard`'s X11 implementation. It reads the regular clipboard as
plain text and checks for changes every 100 ms. It is a same-device bridge,
not a network or Windows client.

The relevant implementation is
[`modules/clipboard-sync.nix`](https://github.com/lhns/steam-frame-nix/blob/main/modules/clipboard-sync.nix),
[`docs/session.md`](https://github.com/lhns/steam-frame-nix/blob/main/docs/session.md),
and upstream
[`src/sync.rs`](https://github.com/dnut/clipboard-sync/blob/master/src/sync.rs)
and
[`src/clipboard.rs`](https://github.com/dnut/clipboard-sync/blob/master/src/clipboard.rs).
`clipboard-sync` supports X11 and Wayland text, but does not support Windows or
network transport. Its upstream Nix flake is described as x86-only by the
Frame-specific module, which builds the crate for the device with Cargo.
FrameSync does not install this bridge.

The
[`awesome-steam-frame`](https://github.com/kosantosbik/awesome-steam-frame)
catalog and
[`frametop-customized`](https://github.com/JaredWyns/frametop-customized)
were also checked for an existing always-on Windows clipboard bridge. No
additional bidirectional clipboard service for both Frame sessions and
Windows was identified there.

Frame Control is useful for device administration, but it does not meet the
continuous-sync requirement. Its clipboard read path calls Klipper's
`getClipboardContents` over the KDE session bus and checks that
`plasmashell` is running; writes call Klipper's `setClipboardContents`. Its
tests explicitly cover the "desktop isn't open" condition. That is a manual
desktop clipboard path, not a Game Mode clipboard bridge:
[`tests/test_clipboard.py`](https://github.com/saphid/frame-control/blob/main/tests/test_clipboard.py).

## Architecture

1. On Windows, FrameSync listens for `WM_CLIPBOARDUPDATE` through
   `AddClipboardFormatListener`; it does not poll the Windows clipboard.
2. On the Frame, a user service polls Game Mode's XWayland `:0` clipboard using
  `xclip`. When the nested Plasma desktop is active, it discovers the desktop
  session bus and polls Klipper. Changes are mirrored between both sessions.
3. Windows opens an SSH reverse tunnel to the Frame. The Frame client connects
   to the forwarded port; both socket ends bind to loopback only. SSH encrypts
   the stream and authenticates the Frame using the user's existing SSH
   identity. A separate random 256-bit secret authenticates the clipboard
   peer; it is never passed in a command-line argument.
4. Clipboard updates are length-framed UTF-8 data (up to 1 MiB), with unique
   origin IDs and timestamps. The winner is the greater
   `(timestamp_ns, origin_id)` pair. Applying an update does not echo it back:
   the resulting local clipboard notification has the same text and is
   ignored. Logs contain lengths and errors, never clipboard contents.
5. On reconnect, both sides publish their current clipboard snapshot. Known
  updates retain their original timestamp; newly observed text is timestamped
  when observed. This cannot recover the actual chronology of changes that
  could not be observed while disconnected.

The service is tied to `gamescope-session.target` and detects the nested
desktop when `plasmashell` starts. It does not start the upstream
`clipboard-sync` bridge. The old Wayland backend remains available for
development but is not installed by the current installer.

## Installation

### Prerequisites

- Windows 10/11 with Python 3.11+ and the OpenSSH client available on `PATH`.
- A trusted SSH host key and working key-based SSH login from Windows to the
  Frame; the target must not require an interactive password on every start.
- On the Frame, Python 3.11+ with `dbus-next`, a Game Mode user session, and either `xclip` or
  Git, Autoconf, Make, GCC, pkg-config, X11 development libraries, and network
  access to build it. The installer uses a user-local build and does not use
  `sudo` or change the read-only system image.

### Install on Windows and the Frame

From PowerShell in this repository:

```powershell
.\windows\install.ps1 -Target steamos@frame
```

Use the SSH host alias or `user@host` that already works from Windows. The
installer copies the files over SSH, creates a random token with a
user-restricted ACL, installs a Game Mode service on the Frame, and adds a Windows
Startup-folder shortcut. The Frame opens no LAN listening port; it connects
to a loopback-only reverse-forwarded SSH port.

The installer does not configure SSH keys or disable SteamOS filesystem
protections. No SSH password is stored by FrameSync. If Python is installed
without the `py` launcher, supply its executable with `-PythonPath`:

```powershell
.\windows\install.ps1 -Target steamos@frame -PythonPath 'C:\path\to\python.exe'
```

### Inspect status

- Windows log: `%LOCALAPPDATA%\FrameSync\framesync.log`
- Frame log: `~/.local/state/framesync/framesync.log`
- On the Frame, run `systemctl --user status framesync.service`.
- Windows SSH tunnel errors are written to the Windows log. Confirm that
  `ssh <target>` works without an interactive password before troubleshooting
  FrameSync.

Logs intentionally exclude clipboard contents.

## Troubleshooting

- **No connection on Windows:** check the Windows log, then run `ssh
  steamos@frame` interactively and verify that it does not prompt for a
  password. Confirm the Frame's SSH host key is trusted and `ssh.exe` is on
  `PATH`.
- **Reverse forwarding is rejected:** the Frame's SSH server must permit
  remote TCP forwarding (`AllowTcpForwarding remote`). FrameSync's forwarded
  listener is explicitly bound to `127.0.0.1`; do not change it to a wildcard
  address.
- **No Game Mode updates:** check the Frame service and logs. The current
  installation uses display `:0`; applications using a different clipboard
  are not covered by this setup.
- **No Desktop Mode updates:** check that `plasmashell` and Klipper are running
  in the nested session and that `python3 -c 'import dbus_next'` works.
- **The xclip build fails:** check the build prerequisites above and rerun
  setup. The installer does not use `sudo` or disable the read-only filesystem.
- **A token was exposed or copied to another account:** stop FrameSync on both
  endpoints, remove the token files, rerun setup to generate a fresh secret,
  and review account-level SSH access.

## Removal

On Windows, run:

```powershell
.\windows\uninstall.ps1
```

Then remove the Frame component over SSH:

```sh
ssh steamos@frame 'bash ~/.local/share/framesync/uninstall.sh'
```

The Frame uninstaller stops and removes the Game Mode service, scripts, and
token. It uninstalls `clipboard-sync` only if a previous installer installed it.
Configuration/log directories are retained for inspection; remove
`~/.config/framesync` and `~/.local/state/framesync` manually if desired.

## Limitations and security notes

- Game Mode XWayland `:0` and the nested Plasma desktop are tested, but
  individual games, other displays, and startup after reboot remain unverified.
  On-screen Android/SDL applications may use different clipboard paths; Frame
  Control's clipboard patch is not used.
- Text only; images, files, rich text, and non-text MIME types are ignored.
- The maximum text payload is 1 MiB. Clipboard updates made while disconnected
  are reconciled from each side's current clipboard on reconnect.
- Authentication protects the forwarded TCP protocol, but SSH must be
  configured with a trusted host key. Keep the token readable only by the
  Windows user and `steamos`.
- Clipboard data is sensitive. Do not install on an untrusted computer or
  share the FrameSync logs/token.

## Verification report

Live testing on 2026-11-16 used Windows and a Steam Frame running Game Mode
with XWayland `:0` and the nested Plasma desktop. The installed agents
transferred multiline text, accents, CJK, emoji, and trailing newlines. The
user confirmed Steam UI copy and paste in both directions.

| Area | Status |
|---|---|
| Windows clipboard ↔ Game Mode XWayland `:0` | Passed on hardware |
| Windows clipboard ↔ nested Desktop Mode Klipper | Passed on hardware |
| Game Mode ↔ nested Desktop Mode | Passed on hardware |
| Native Steam UI copy and paste | Confirmed by user |
| Individual games and other displays | Not verified |
| Frame service restart and SSH tunnel reconnection | Passed on hardware |
| Protocol framing, authentication, 1 MiB limit, conflict ordering, shutdown | Covered by local tests |
| Large real clipboard transfers, Wi-Fi loss, sleep/wake, reboot | Not verified on hardware |

## Development

The Frame Desktop Mode backend requires `dbus-next`; the local tests can run
without it. Run the tests with:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q framesync framesync.py
bash -n frame/install.sh frame/uninstall.sh
```
