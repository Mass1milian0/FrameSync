#!/usr/bin/env bash
set -euo pipefail

systemctl --user disable --now framesync.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/framesync.service"
systemctl --user daemon-reload
rm -f "$HOME/.config/autostart/framesync.desktop"
rm -f "$HOME/.local/bin/framesync-frame"
rm -rf "$HOME/.local/share/framesync"
rm -f "$HOME/.config/framesync/token"

if [[ -f "$HOME/.config/framesync/installed-clipboard-sync" ]]; then
    if command -v cargo >/dev/null 2>&1; then
        cargo uninstall clipboard-sync
    fi
    rm -f "$HOME/.config/framesync/installed-clipboard-sync"
fi

echo "FrameSync removed. Its configuration directory was retained at ~/.config/framesync."
