#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: install.sh <host-port> <frame-port>" >&2
    exit 2
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    echo "Python 3.11 or newer is required on the Frame." >&2
    exit 1
fi

host_port="$1"
frame_port="$2"
case "$host_port:$frame_port" in
    *[!0-9:]* | :* | *:) echo "Ports must be numeric." >&2; exit 2 ;;
esac
if (( host_port < 1024 || host_port > 65535 || frame_port < 1024 || frame_port > 65535 )); then
    echo "Ports must be between 1024 and 65535." >&2
    exit 2
fi

source_dir="$HOME/.cache/framesync-setup"
install_dir="$HOME/.local/share/framesync"
config_dir="$HOME/.config/framesync"
autostart_dir="$HOME/.config/autostart"
bin_dir="$HOME/.local/bin"

mkdir -p "$config_dir"
chmod 700 "$config_dir"

if ! command -v wl-paste >/dev/null 2>&1 || ! command -v wl-copy >/dev/null 2>&1; then
    echo "wl-paste and wl-copy are required (provided by wl-clipboard)." >&2
    exit 1
fi

if ! command -v clipboard-sync >/dev/null 2>&1 && [[ ! -x "$HOME/.cargo/bin/clipboard-sync" ]]; then
    if ! command -v cargo >/dev/null 2>&1; then
        echo "Rust/Cargo is required to build the upstream clipboard-sync bridge." >&2
        echo "Install the Rust toolchain for steamos, then rerun this installer." >&2
        exit 1
    fi
    cargo install --locked --version 0.2.0 clipboard-sync
    touch "$config_dir/installed-clipboard-sync"
fi

mkdir -p "$install_dir" "$autostart_dir" "$bin_dir" "$HOME/.local/state/framesync"
install -m 644 "$source_dir/framesync.py" "$install_dir/framesync.py"
rm -rf "$install_dir/framesync"
cp -R "$source_dir/framesync" "$install_dir/framesync"
install -m 700 "$source_dir/uninstall.sh" "$install_dir/uninstall.sh"
chmod -R u=rwX,go= "$install_dir"
install -m 600 "$source_dir/token" "$config_dir/token"

cat > "$install_dir/run-frame.sh" <<EOF
#!/usr/bin/env bash
set -u
if command -v clipboard-sync >/dev/null 2>&1; then
    bridge_bin="\$(command -v clipboard-sync)"
else
    bridge_bin="$HOME/.cargo/bin/clipboard-sync"
fi
while true; do
    if ! pgrep -x clipboard-sync >/dev/null 2>&1; then
        "\$bridge_bin" >> "$HOME/.local/state/framesync/clipboard-sync.log" 2>&1 &
    fi
    "$HOME/.local/bin/framesync-frame" --frame-port "$frame_port"
    status=\$?
    printf '%s FrameSync exited with status %s; retrying\\n' "\$(date -Is)" "\$status" >> "$HOME/.local/state/framesync/framesync.log"
    sleep 2
done
EOF
chmod 700 "$install_dir/run-frame.sh"

cat > "$bin_dir/framesync-frame" <<EOF
#!/usr/bin/env bash
exec /usr/bin/python3 "$install_dir/framesync.py" frame --secret-file "$config_dir/token" "\$@"
EOF
chmod 700 "$bin_dir/framesync-frame"

cat > "$autostart_dir/framesync.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=FrameSync
Comment=Synchronize Steam Frame and Windows text clipboards
Exec=$install_dir/run-frame.sh
X-KDE-autostart-phase=3
NoDisplay=true
EOF
chmod 600 "$autostart_dir/framesync.desktop"
rm -rf "$source_dir/framesync"
rm -f "$source_dir/framesync.py" "$source_dir/install.sh" "$source_dir/uninstall.sh" "$source_dir/token"
rmdir "$source_dir" 2>/dev/null || true

echo "FrameSync installed for the KDE nested desktop session."
echo "It will start the upstream clipboard-sync bridge and FrameSync at KDE login."
