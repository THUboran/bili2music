#!/usr/bin/env bash
# Bili2Music 安装脚本（macOS）
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HOME/.bili2music"
LA_DIR="$HOME/Library/LaunchAgents"
LABEL="com.bili2music.bridge"
PLIST="$LA_DIR/$LABEL.plist"

echo "== Bili2Music 安装 =="
echo "仓库目录: $REPO_DIR"
echo "安装目录: $APP_DIR"

# 1) 依赖检查
PY="${B2M_PYTHON:-$(command -v python3 || true)}"
if [ -z "$PY" ]; then echo "[错误] 找不到 python3"; exit 1; fi
echo "Python: $PY"
command -v yt-dlp >/dev/null 2>&1 || echo "[警告] 未找到 yt-dlp，请先安装（brew install yt-dlp）"
if ! command -v ffmpeg >/dev/null 2>&1 && [ ! -x "$APP_DIR/bin/ffmpeg" ]; then
  echo "[警告] 未找到 ffmpeg：请放到 $APP_DIR/bin/ffmpeg 或加入 PATH"
fi
"$PY" - <<'PYCHK' || echo "[警告] Python 依赖缺失，请运行: python3 -m pip install -r requirements.txt"
import mutagen, PIL
try:
    import Vision, Quartz  # noqa
except Exception:
    print("(注意: 无 Vision，OCR 功能不可用)")
PYCHK

# 2) 目录与代码
mkdir -p "$APP_DIR/bin" "$APP_DIR/logs" "$LA_DIR"
cp "$REPO_DIR/bridge.py" "$APP_DIR/bridge.py"
rm -rf "$APP_DIR/extension"
cp -R "$REPO_DIR/extension" "$APP_DIR/extension"

# 3) 配置（已存在则不覆盖，保留用户的 music_base）
if [ ! -f "$APP_DIR/config.json" ]; then
  cp "$REPO_DIR/config.example.json" "$APP_DIR/config.json"
  echo "已生成 $APP_DIR/config.json（默认输出到 ~/Music/Bili2Music，可按需修改）"
fi

# 4) token
TOKEN="$("$PY" -c 'import uuid;print(uuid.uuid4().hex)')"
printf '%s' "$TOKEN" > "$APP_DIR/token.txt"

# 5) 扩展 config.js
sed "s/__TOKEN__/$TOKEN/" "$APP_DIR/extension/config.example.js" > "$APP_DIR/extension/config.js"
echo "已写入扩展配置 $APP_DIR/extension/config.js"

# 6) LaunchAgent
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$APP_DIR/bridge.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$APP_DIR/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>StandardOutPath</key><string>$APP_DIR/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$APP_DIR/logs/launchd.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
sleep 0.5
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null || true
echo "已加载 LaunchAgent: $LABEL"

echo
echo "完成。下一步："
echo "  1. Chrome 打开 chrome://extensions → 开启开发者模式 → 加载已解压的扩展程序 → 选 $APP_DIR/extension"
echo "  2. 打开 B 站视频页，点插件图标 → 下载 MP3"
echo
echo "本机桥状态自检: curl -s http://127.0.0.1:8765/health"
