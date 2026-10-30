#!/usr/bin/env bash
# Bili2Music 卸载脚本：停止并移除开机自启服务（不删除 ~/.bili2music 数据）
set -euo pipefail
LABEL="com.bili2music.bridge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "已停止并移除 LaunchAgent: $LABEL"
echo "如需彻底清除程序与配置： rm -rf ~/.bili2music"
echo "并到 chrome://extensions 手动移除该扩展。"
