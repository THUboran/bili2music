# Bili2Music · B 站音频一键进「音乐」

在 B 站视频页点一下浏览器插件，就把该视频（或整个合集）的音频下载成 MP3、写好标签与封面、可选带上歌词，并自动导入 macOS「音乐」App。适合把 B 站上的歌、翻唱、现场存档到本地曲库。

> 仅支持 **macOS**（依赖系统「音乐」App 与 Vision OCR）。仅供个人学习、备份与欣赏使用，请遵守 B 站服务条款与相关版权规定，不要用于传播或商业用途。

---

## 功能

- 一键把当前 B 站视频下载为 MP3（`yt-dlp` + `ffmpeg`）
- 识别视频所属 **合集**，可整包批量下载
- 自动写入 ID3：标题、艺人（翻唱者）、专辑（合集名）、曲序、原唱（写进备注/COMM）、封面
- **可选歌词**（默认开）：
  - 优先对**画面烧录字幕**做 OCR（音乐/翻唱类视频通常只有烧录字幕，质量最好）
  - 烧录字幕拿不到时才用 B 站 **CC 字幕轨**
  - 结果内嵌为 `USLT` 歌词 + 同名 `.lrc`
- 自动导入 macOS「音乐」App；配合 iCloud「同步资料库」可上手机

## 工作原理

```
┌───────────────┐   HTTP(127.0.0.1:8765)   ┌────────────────────────────┐
│ Chrome 扩展    │ ── 读当前 BV / 点下载 ──▶ │ 本机桥 bridge.py            │
│ (popup 按钮)   │ ◀── 进度轮询 job ─────── │ yt-dlp / ffmpeg / osascript │
└───────────────┘   带 X-Bili-Token 鉴权    └────────────┬───────────────┘
                                                          │
                       ┌──────────────────────────────────┼─────────────────────────┐
                       ▼                                  ▼                         ▼
              MP3 + ID3/封面/歌词               macOS「音乐」App 资料库        OCR(Vision) / CC 字幕
```

- 浏览器插件只负责取当前页的 BV 号、显示按钮与进度；真正下载/转码/入库由本机桥完成（浏览器本身就碰不到「音乐」App，也做不了 mp3 转码，所以必须两件套）。
- 本机桥只监听 `127.0.0.1`，所有请求要带随机 token，避免网页乱调。

## 环境要求

- macOS（Apple Silicon 或 Intel）
- Python 3.9+，并安装依赖：`mutagen`、`Pillow`、`pyobjc-framework-Vision`、`pyobjc-framework-Quartz`
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- ffmpeg（放到 `~/.bili2music/bin/ffmpeg`，或在 PATH 里）

## 安装

### 方式一：脚本安装（推荐）

```bash
# 1) 依赖（示例，按需替换 python/包管理器）
python3 -m pip install -r requirements.txt
brew install yt-dlp ffmpeg        # 或自行放置 ffmpeg 到 ~/.bili2music/bin/ffmpeg

# 2) 运行安装脚本
bash install.sh
```

`install.sh` 会：把 `bridge.py` 与扩展复制到 `~/.bili2music/`、生成 `config.json` 与随机 token、
写扩展的 `config.js`、安装并加载一个开机自启的 LaunchAgent（`com.bili2music.bridge`）。

### 方式二：手动

```bash
mkdir -p ~/.bili2music/{bin,logs}
cp bridge.py ~/.bili2music/
cp -R extension ~/.bili2music/extension
cp config.example.json ~/.bili2music/config.json   # 按需修改 music_base 等
python3 -c "import uuid;open('$HOME/.bili2music/token.txt','w').write(uuid.uuid4().hex)"
TOKEN=$(cat ~/.bili2music/token.txt)
sed "s/__TOKEN__/$TOKEN/" extension/config.example.js > ~/.bili2music/extension/config.js
# 再参照 install.sh 里的 plist 模板创建 LaunchAgent 并 launchctl bootstrap
```

## 在 Chrome 里加载插件

1. 地址栏打开 `chrome://extensions`
2. 右上角打开「**开发者模式**」
3. 点「**加载已解压的扩展程序**」
4. 选择 `~/.bili2music/extension` 目录（安装脚本生成的目录，里面已有 `config.js`）
5. 建议点插件图标旁的图钉，固定到工具栏

> 改了插件代码后，回到 `chrome://extensions` 点该扩展的「刷新/重新加载」即可生效。

## 使用

1. 在本机跑起本机桥（`install.sh` 已设为开机自启；手动可用 `python3 ~/.bili2music/bridge.py`）
2. 打开任意 B 站视频页 → 点插件图标
3. 选择「整个合集 / 仅当前视频」，勾选/取消「需要歌词」→ 点「下载 MP3」
4. 进度走完：MP3 落在 `音乐目录/UP 主 <UP主名>/序号_标题.mp3`，并导入「音乐」

首次导入「音乐」时，macOS 会弹「…想要控制“音乐”」，点允许（或在 系统设置 › 隐私与安全性 › 自动化 里勾选）。

## 歌词说明（重要）

- 「需要歌词」打开时：**先 OCR 画面烧录字幕**，OCR 空才用 CC 字幕轨。原因是音乐视频的 CC（AI 语音识别）准确率低。
- 实测（以网易云官方歌词为基准）：画面 OCR 对字幕清晰的现场约 **CER 11%**、覆盖 93%；而同一首的 CC 字幕 CER 约 27%。对字幕零散的家庭翻唱，OCR 也只能覆盖约三分之一（源视频本身字幕不全）。
- OCR 需要下载视频并逐帧识别，较慢（约 2–4 分钟/首）。批量合集想快，就别勾歌词。
- 音视频下载/字幕获取会用到本机 Chrome 的登录 Cookie（`--cookies-from-browser chrome`），以便拿到 720p 视频流和 CC 字幕。

## 配置 `~/.bili2music/config.json`

| 字段 | 说明 | 默认 |
|---|---|---|
| `music_base` | MP3 输出根目录 | `~/Music/Bili2Music` |
| `ytdlp` | yt-dlp 可执行路径或命令名 | `yt-dlp` |
| `ffmpeg_dir` | 放 ffmpeg 的目录 | `~/.bili2music/bin` |
| `port` | 本机桥端口（改端口需同步改扩展 host 权限） | `8765` |
| `import_music` | 是否自动导入「音乐」App | `true` |
| `ocr` | 是否启用画面 OCR 兜底 | `true` |

对应环境变量（优先级高于配置）：`B2M_MUSIC_BASE`、`B2M_PORT`、`B2M_MUSIC_IMPORT`、`B2M_OCR`。

## 安全

- 本机桥只绑定 `127.0.0.1`，所有 `/resolve`、`/download`、`/job` 请求需带 `X-Bili-Token`（随机生成，存于 `~/.bili2music/token.txt` 与扩展 `config.js`）。
- `token.txt`、`config.json`、`done.json`、`logs/`、`extension/config.js`、`bin/` 均已被 `.gitignore` 排除，不会进仓库。

## 卸载

```bash
bash uninstall.sh          # 卸载 LaunchAgent 并停服务
rm -rf ~/.bili2music       # 彻底删除程序与配置
# 再到 chrome://extensions 移除该扩展
```

## 常见问题

- **插件显示「本机桥未连接」**：本机桥没起来。`launchctl kickstart -k gui/$(id -u)/com.bili2music.bridge`，或看 `~/.bili2music/logs/`。
- **导入「音乐」失败/无权限**：系统设置 › 隐私与安全性 › 自动化，给终端/程序勾选控制「音乐」。
- **没有歌词**：该视频既无烧录字幕也无 CC 字幕；或「需要歌词」未勾选。
- **下载失败**：yt-dlp 版本过旧，`yt-dlp -U` 升级；或该视频需要大会员画质。

## 许可

MIT License，见 [LICENSE](LICENSE)。

## 免责声明

本项目仅用于个人学习、备份与欣赏，下载内容版权归原作者与平台所有。请勿用于二次传播、商业用途或任何侵犯版权的行为。使用本项目产生的一切后果由使用者自行承担。
