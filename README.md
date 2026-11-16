# FrameSync

FrameSync is an experimental, text-only clipboard bridge between a Windows
computer and the Steam Frame's nested KDE desktop. It is not yet a verified
solution for a Frame that is running only Game Mode. The repository and device
were not available together for real-headset testing, and native Steam UI
clipboard access has not been established by the sources reviewed here.

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
Frame-specific module; the Frame installer uses Cargo to build the published
crate for the device instead.

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
2. In the Frame's nested KDE session, `wl-paste --watch` reports Wayland text
   changes and `wl-copy` applies received text. The upstream
   `clipboard-sync` process bridges this Wayland clipboard to accessible X11
   and Wayland clipboards on the Frame.
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
5. On reconnect, both sides publish their current clipboard snapshot. The
   snapshot timestamp is the time it is observed, not the original copy time;
   this makes reconnect reconciliation deterministic but cannot recover the
   actual chronology of changes made while disconnected.

This design deliberately follows the Frame-specific project's KDE autostart
choice instead of a generic `systemd --user` unit: the nested desktop has its
own runtime directory, D-Bus, and Wayland environment, and is not guaranteed
to reach the outer Steam user manager. KDE autostart provides the clipboard
environment that `wl-paste` needs.

## Installation

### Prerequisites

- Windows 10/11 with Python 3.11+ and the OpenSSH client available on `PATH`.
- A trusted SSH host key and working key-based SSH login from Windows to the
  Frame; the target must not require an interactive password on every start.
- On the Frame, `wl-clipboard`, Cargo, and permission to build a user-level
  Rust crate. The installer does not change the read-only system image or use
  `sudo`.

The Frame's clipboard agent starts from KDE autostart. The `clipboard-sync`
bridge is built from upstream source with `cargo install` if it is not already
installed. If Cargo is not available on the Frame, install the Rust toolchain
for the `steamos` user and rerun setup.

### Install on Windows and the Frame

From PowerShell in this repository:

```powershell
.\windows\install.ps1 -Target steamos@frame
```

Use the SSH host alias or `user@host` that already works from Windows. The
installer copies the files over SSH, creates a random token with a
user-restricted ACL, installs KDE autostart on the Frame, and adds a Windows
Startup-folder shortcut. The Frame opens no LAN listening port; it connects
to a loopback-only reverse-forwarded SSH port.

The installer does not configure SSH keys or disable SteamOS filesystem
protections. No SSH password is stored by FrameSync. For a first installation,
open Desktop Mode once so KDE autostart can launch the bridge.

### Inspect status

- Windows log: `%LOCALAPPDATA%\FrameSync\framesync.log`
- Frame log: `~/.local/state/framesync/framesync.log`
- Upstream bridge log: `~/.local/state/framesync/clipboard-sync.log`
- On the Frame, inspect `pgrep -af 'framesync|clipboard-sync'` and
  `~/.config/autostart/framesync.desktop`.
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
- **KDE reports `wl-paste` or `wl-copy` errors:** verify `wl-clipboard` is
  installed and that `framesync.desktop` is launched from the nested KDE
  session. Starting the process from a plain SSH shell does not supply the
  Wayland session environment.
- **No Game Mode updates:** confirm KDE Desktop Mode has been opened and both
  `clipboard-sync` and `framesync` are running. This does not prove native Steam
  dashboard clipboard support; that behavior remains a device-test blocker.
- **Cargo build fails on the Frame:** install the Rust toolchain for the
  `steamos` user and rerun `windows/install.ps1`. The installer does not use
  `sudo` or disable the read-only filesystem.
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

The Frame uninstaller removes the FrameSync autostart, binary scripts, and
token. It uninstalls `clipboard-sync` only if this installer installed it.
Configuration/log directories are retained for inspection; remove
`~/.config/framesync` and `~/.local/state/framesync` manually if desired.
Log out of the KDE desktop session after removal to terminate any already
running Frame-side process.

## Limitations and security notes

- **Game Mode remains unverified.** The Frame-specific source establishes that
  X11 and Wayland clipboards can be bridged when `clipboard-sync` is running,
  but it does not establish that the native Steam dashboard or every game
  exposes its clipboard through those selections. FrameSync is started by KDE
  autostart, not by the Steam session. A cold boot directly into Game Mode,
  native Steam UI copy/paste, and operation after KDE exits must be tested on
  the target device before calling this requirement satisfied.
- The supported Frame-side clipboard API is the current KDE Wayland
  clipboard. On-screen Android/SDL applications may use different clipboard
  paths; Frame Control's clipboard patch is not used.
- Text only; images, files, rich text, and non-text MIME types are ignored.
- The maximum text payload is 1 MiB. Clipboard updates made while disconnected
  are reconciled from each side's current clipboard on reconnect.
- Authentication protects the forwarded TCP protocol, but SSH must be
  configured with a trusted host key. Keep the token readable only by the
  Windows user and `steamos`.
- Clipboard data is sensitive. Do not install on an untrusted computer or
  share the FrameSync logs/token.

## Verification report

| Area | Status |
|---|---|
| Protocol framing, UTF-8 round-trip, 1 MiB limit, authentication, and conflict ordering | Covered by local unit tests |
| Windows clipboard listener/API | Source implemented; not executable on this Linux build host |
| Linux Wayland watcher and writer | Source implemented; not tested in a live Wayland session here |
| `dnut/clipboard-sync` behavior | Source and Frame-specific integration read; not tested on this Frame |
| SSH reverse tunnel to Frame | Loopback forwarding passes with a Linux TCP peer; Windows host not tested |
| Game Mode ↔ Windows and Desktop Mode ↔ Windows | Not verified on hardware |
| Sleep/wake, Wi-Fi reconnect, session restart, and large real clipboard transfers | Not verified on hardware |

Do not treat the table's unverified items as working guarantees. The current
implementation is a research-backed prototype for the nested KDE session, not
a validated claim of seamless synchronization in every Game Mode situation.

### Live Frame checks (2026-11-16)

The connected device reported SteamOS kernel `7.2.0-g344f27110f05` on
`aarch64` and was running the Gamescope Steam/VR session. Gamescope had
XWayland displays `:0` and `:1`; its user manager reported `DISPLAY=:0` and
`XDG_SESSION_TYPE=x11`. The checks made no persistent changes to the Frame.

| Check | Result |
|---|---|
| Read-only inspection of clipboard tools and services | `wl-paste`, `wl-copy`, `cargo`, `rustc`, and `clipboard-sync` were not installed; no FrameSync files or processes were present |
| Initial `CLIPBOARD` and `PRIMARY` selection owners on `:0` and `:1` | All four selections were empty |
| Temporary UTF-8/emoji clipboard offer, then request/read, on XWayland `:0` | Pass; exact 49-byte test text round-tripped. The temporary owner released the selection afterward |
| Same temporary round-trip on XWayland `:1` | Pass; exact 49-byte test text round-tripped. The temporary owner released the selection afterward |
| SSH reverse forwarding, bound to Frame loopback | Pass; a test payload and response traversed the authenticated reverse tunnel. No port was opened on the LAN interface |
| FrameSync install or Windows-to-Frame clipboard transfer | Not run. The Frame lacks the current installer's required `wl-clipboard` and Cargo prerequisites, and this agent host is Linux |
| Steam dashboard/game copy behavior and Desktop Mode | Not verified; XWayland interoperability tests use a test client and do not establish native Steam UI behavior |

These checks confirm that both Game Mode XWayland servers can participate in
ordinary X11 text clipboard selections and that the SSH reverse-forward path
is available. They do **not** make the current `wl-paste`-based FrameSync
client Game Mode compatible: the required Wayland clipboard commands were
absent, and native Steam UI integration remains unproven. No clipboard
contents belonging to the user were read; the selections were empty before
the test and the temporary test owners released them afterward.

| Test case | Expected behavior | Result |
|---|---|---|
| Windows ↔ KDE Desktop Mode, plain text | Copy propagates automatically both ways | Not run on a device pair |
| Windows ↔ Game Mode dashboard | Copy propagates automatically both ways | XWayland `:0`/`:1` test-client selections pass; native Steam dashboard remains unverified |
| Windows ↔ Game Mode-launched apps | Text clipboard propagates both ways | Not verified on Frame |
| Game Mode ↔ Desktop Mode | Upstream bridge mirrors accessible clipboard selections | Upstream source reviewed; no live Frame test |
| Multiline, accents, CJK, emoji, trailing newlines | Exact UTF-8 text is retained | Protocol unit test passes |
| Large payload (1 MiB) | Accepted; payloads above 1 MiB rejected | Protocol boundary tests pass; no OS clipboard test |
| Repeated updates and loop suppression | No echo loop; each new text is applied once | In-process peer test passes |
| Near-simultaneous updates | Stable timestamp/origin ordering | State-order unit test passes; no wall-clock skew test |
| SSH/Wi-Fi disconnect and reconnect | Tunnel restarts; current clipboard snapshots reconcile | Reconnect loop implemented; not hardware tested |
| Sleep/wake, Windows restart, KDE/Gamescope restart | Service returns without manual restart | Not hardware tested |

## Development

The project has no third-party Python dependencies. Run its local tests with:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q framesync framesync.py
bash -n frame/install.sh frame/uninstall.sh
```
