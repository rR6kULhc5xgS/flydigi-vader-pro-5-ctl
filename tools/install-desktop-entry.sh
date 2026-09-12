#!/usr/bin/env bash
# Add (or remove) a start-menu entry for the GUI.
#
#   tools/install-desktop-entry.sh            install
#   tools/install-desktop-entry.sh --uninstall
#
# Everything goes under ~/.local, so no root is needed and nothing is
# installed system-wide. The entry points at wherever this checkout lives,
# so re-run it if you move the project.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ID="flydigi-control"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
DESKTOP_FILE="$DESKTOP_DIR/$APP_ID.desktop"

refresh() {
    command -v update-desktop-database >/dev/null 2>&1 &&
        update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 &&
        gtk-update-icon-cache -f -t "$ICON_DIR" >/dev/null 2>&1 || true
}

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$DESKTOP_FILE"
    find "$ICON_DIR" -name "$APP_ID.png" -delete 2>/dev/null || true
    refresh
    echo "Removed $APP_ID from the application menu."
    exit 0
fi

if [ ! -x "$PROJECT_DIR/flydigi-gui" ]; then
    echo "error: $PROJECT_DIR/flydigi-gui is missing or not executable" >&2
    exit 1
fi

mkdir -p "$DESKTOP_DIR"
python3 "$PROJECT_DIR/tools/make-icon.py" "$ICON_DIR" >/dev/null

cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=Flydigi Control
GenericName=Game Controller Settings
Comment=Configure a Flydigi Vader 5 Pro: mappings, profiles, macros and power
Exec=$PROJECT_DIR/flydigi-gui
Path=$PROJECT_DIR
Icon=$APP_ID
Terminal=false
Categories=Settings;HardwareSettings;
Keywords=controller;gamepad;joystick;flydigi;vader;mapping;macro;
StartupNotify=true
StartupWMClass=flydigi-gui
DESKTOP

chmod +x "$DESKTOP_FILE"
refresh

echo "Installed:"
echo "  $DESKTOP_FILE"
echo "  icons under $ICON_DIR"
echo
echo "It should appear as \"Flydigi Control\" in your launcher."
echo "Re-run this after moving the project; remove it with --uninstall."
