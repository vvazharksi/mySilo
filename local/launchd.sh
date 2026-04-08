#!/bin/bash
# Manage mySilo launchd agent.
#
# Usage:
#   ./launchd.sh install    Install and start the daemon
#   ./launchd.sh uninstall  Stop and remove the daemon
#   ./launchd.sh start      Start (if already installed)
#   ./launchd.sh stop       Stop (without uninstalling)
#   ./launchd.sh restart    Restart the daemon
#   ./launchd.sh status     Show whether the daemon is running

LABEL="com.blackdird.mysilo"
PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/$LABEL.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/mySilo/.mysilo"

_require_plist() {
    if [ ! -f "$PLIST_SRC" ]; then
        echo "Error: $PLIST_SRC not found." >&2
        exit 1
    fi
}

case "$1" in
    install)
        _require_plist
        mkdir -p "$LOG_DIR"
        cp "$PLIST_SRC" "$PLIST_DEST"
        launchctl load -w "$PLIST_DEST"
        echo "mySilo daemon installed and started."
        echo "Logs: $LOG_DIR/"
        ;;
    uninstall)
        if [ -f "$PLIST_DEST" ]; then
            launchctl unload "$PLIST_DEST"
            rm "$PLIST_DEST"
            echo "mySilo daemon stopped and removed."
        else
            echo "Daemon is not installed."
        fi
        ;;
    start)
        launchctl start "$LABEL"
        echo "mySilo daemon started."
        ;;
    stop)
        launchctl stop "$LABEL"
        echo "mySilo daemon stopped."
        ;;
    restart)
        launchctl stop "$LABEL"
        sleep 1
        launchctl start "$LABEL"
        echo "mySilo daemon restarted."
        ;;
    status)
        if launchctl list | grep -q "$LABEL"; then
            PID=$(launchctl list | grep "$LABEL" | awk '{print $1}')
            if [ "$PID" = "-" ]; then
                echo "mySilo is installed but not running."
            else
                echo "mySilo is running (PID $PID)."
            fi
        else
            echo "mySilo is not installed."
        fi
        ;;
    *)
        echo "Usage: $0 {install|uninstall|start|stop|restart|status}"
        exit 1
        ;;
esac
