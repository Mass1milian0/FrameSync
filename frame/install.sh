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
if ! python3 -c 'import dbus_next' >/dev/null 2>&1; then
    echo "The dbus-next Python package is required for Desktop Mode." >&2
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
bin_dir="$HOME/.local/bin"
unit_dir="$HOME/.config/systemd/user"

mkdir -p "$config_dir"
chmod 700 "$config_dir"

if ! command -v xclip >/dev/null 2>&1 && [[ ! -x "$install_dir/bin/xclip" ]]; then
    for tool in git autoreconf make gcc pkg-config; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            echo "$tool is required to build xclip for Game Mode." >&2
            exit 1
        fi
    done
    build_dir=$(mktemp -d)
    trap 'rm -rf "$build_dir"' EXIT
    git clone --quiet --depth 1 --branch 0.13 https://github.com/astrand/xclip.git "$build_dir/source"
    (cd "$build_dir/source" && autoreconf -fi && ./configure --quiet && make)
    mkdir -p "$install_dir/bin"
    install -m 755 "$build_dir/source/xclip" "$install_dir/bin/xclip"
fi

mkdir -p "$install_dir" "$unit_dir" "$bin_dir" "$HOME/.local/state/framesync"
install -m 644 "$source_dir/framesync.py" "$install_dir/framesync.py"
rm -rf "$install_dir/framesync"
cp -R "$source_dir/framesync" "$install_dir/framesync"
install -m 700 "$source_dir/uninstall.sh" "$install_dir/uninstall.sh"
chmod -R u=rwX,go= "$install_dir"
install -m 600 "$source_dir/token" "$config_dir/token"

cat > "$bin_dir/framesync-frame" <<EOF
#!/usr/bin/env bash
export PATH="$install_dir/bin:$HOME/.local/bin:/usr/bin:/bin"
exec /usr/bin/python3 "$install_dir/framesync.py" frame --backend x11 --secret-file "$config_dir/token" "\$@"
EOF
chmod 700 "$bin_dir/framesync-frame"

cat > "$unit_dir/framesync.service" <<EOF
[Unit]
Description=FrameSync Game Mode clipboard agent
PartOf=gamescope-session.target
After=gamescope-session.service

[Service]
Environment=DISPLAY=:0
ExecStart=$bin_dir/framesync-frame --frame-port $frame_port
Restart=on-failure
RestartSec=2

[Install]
WantedBy=gamescope-session.target
EOF
chmod 600 "$unit_dir/framesync.service"
rm -f "$HOME/.config/autostart/framesync.desktop"
systemctl --user daemon-reload
systemctl --user enable framesync.service
systemctl --user restart framesync.service
rm -rf "$source_dir/framesync"
rm -f "$source_dir/framesync.py" "$source_dir/install.sh" "$source_dir/uninstall.sh" "$source_dir/token"
rmdir "$source_dir" 2>/dev/null || true

echo "FrameSync installed for the Game Mode session on DISPLAY=:0."
