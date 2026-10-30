#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bili -> Music 本机桥
接收 Chrome 插件发来的 B 站视频/合集地址，用 yt-dlp+ffmpeg 下载 mp3、写 ID3+封面，
并（可选）通过 AppleScript 导入 macOS「音乐」App 资料库。
仅监听 127.0.0.1，用 X-Bili-Token 头鉴权。
"""
import os, sys, json, re, time, uuid, threading, subprocess, urllib.request, traceback, glob, shutil, difflib, queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
ROOT = os.path.join(HOME, ".bili2music")
TMP = os.path.join(ROOT, "tmp")
CONFIG_FILE = os.path.join(ROOT, "config.json")
def _load_cfg():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
CFG = _load_cfg()
def _cfg(k, default):
    return CFG.get(k, default)
BIN = os.path.abspath(os.path.expanduser(_cfg("ffmpeg_dir", os.path.join(ROOT, "bin"))))
_ytdlp = _cfg("ytdlp", "yt-dlp")
YTDLP = os.path.expanduser(_ytdlp) if _ytdlp else "yt-dlp"
if not os.path.isabs(YTDLP):
    YTDLP = shutil.which(os.path.basename(YTDLP)) or YTDLP
MUSIC_BASE = os.environ.get("B2M_MUSIC_BASE") or os.path.expanduser(_cfg("music_base", os.path.join(HOME, "Music", "Bili2Music")))
PORT = int(os.environ.get("B2M_PORT") or _cfg("port", 8765))
def _truth(v, d=True):
    if v is None:
        return d
    return str(v).lower() in ("1", "true", "yes", "on")
DO_IMPORT = _truth(os.environ.get("B2M_MUSIC_IMPORT", _cfg("import_music", True)), True)
try:
    CONCURRENCY = max(1, min(4, int(os.environ.get("B2M_CONCURRENCY") or _cfg("concurrency", 1))))
except Exception:
    CONCURRENCY = 1
IMPORT_LOCK = threading.Lock()
TOKEN_FILE = os.path.join(ROOT, "token.txt")
DONE_FILE = os.path.join(ROOT, "done.json")
LOG_FILE = os.path.join(ROOT, "logs", "bridge.log")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

os.makedirs(TMP, exist_ok=True)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

try:
    from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TRCK, APIC, COMM, TXXX, USLT
    from PIL import Image
    HAVE_TAG = True
except Exception:
    HAVE_TAG = False

# 画面烧录字幕 OCR（无 CC 字幕轨时的兜底）
OCR_ENABLED = _truth(os.environ.get("B2M_OCR", _cfg("ocr", True)), True)
try:
    import Quartz, Vision
    from Foundation import NSURL
    HAVE_OCR = True
except Exception:
    HAVE_OCR = False

_OCR_WATER = ['bilibili', '哔哩', 'noomi', '李诺', '音乐私藏馆', '望言', '关注', '点赞', '投币',
              '收藏', '转发', '订阅', '弹幕', '直播间', '公众号', 'qq', '扫码', '字幕由',
              '字幕提供', '字幕组', '翻译', '校对']


def log(*a):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_token():
    if os.path.exists(TOKEN_FILE):
        return open(TOKEN_FILE).read().strip()
    tok = uuid.uuid4().hex
    open(TOKEN_FILE, "w").write(tok)
    return tok


TOKEN = get_token()

# ---------- persisted state ----------
_lock = threading.Lock()
def load_done():
    try:
        return json.load(open(DONE_FILE))
    except Exception:
        return {}
def save_done(d):
    with _lock:
        json.dump(d, open(DONE_FILE, "w"), ensure_ascii=False, indent=1)

DONE = load_done()
JOBS = {}
JOB_QUEUE = queue.Queue()

# ---------- network ----------
def _curl_get_json(url):
    cmd = ["curl", "-sS", "--noproxy", "*", "--max-time", "25", "--compressed",
           "-A", UA, "-e", "https://www.bilibili.com/",
           "-H", "Accept: application/json,text/plain,*/*", url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError((r.stderr or "curl empty").strip()[:200])
    return json.loads(r.stdout)


def http_get_json(url):
    """取 B 站接口 JSON：curl 优先（直连、稳定），失败再 urllib，带重试。"""
    last = None
    for i in range(4):
        try:
            return _curl_get_json(url)
        except Exception as e:
            last = e
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": "https://www.bilibili.com/",
                "Accept": "application/json,text/plain,*/*",
            })
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
        time.sleep(1.2 * (i + 1))
    raise last


def extract_bvid(s):
    m = re.search(r"(BV[0-9A-Za-z]{10})", s or "")
    return m.group(1) if m else None


def resolve(url):
    bv = extract_bvid(url)
    if not bv:
        raise ValueError("没识别到 BV 号，请在 B 站视频页使用")
    d = http_get_json("https://api.bilibili.com/x/web-interface/view?bvid=" + bv)
    if d.get("code") != 0:
        raise ValueError("B 站接口返回 %s: %s" % (d.get("code"), d.get("message")))
    data = d["data"]
    owner = (data.get("owner") or {}).get("name") or "未知UP主"
    season = data.get("ugc_season")
    p_m = re.search(r"[?&]p=(\d+)", url or "")
    p_cur = int(p_m.group(1)) if p_m else 1
    multi_part = False
    pages = []
    if season:
        for sec in season.get("sections", []):
            for ep in sec.get("episodes", []):
                pic = (ep.get("arc") or {}).get("pic") or data.get("pic")
                pages.append({"bvid": ep["bvid"], "title": ep.get("title"),
                              "pic": pic,
                              "url": "https://www.bilibili.com/video/" + ep["bvid"]})
        season_title = season.get("title")
    elif len(data.get("pages") or []) > 1:
        multi_part = True
        for i, p in enumerate(data["pages"], 1):
            pages.append({"bvid": bv, "title": p.get("part") or ("P%d" % i),
                          "pic": data.get("pic"),
                          "url": "https://www.bilibili.com/video/%s?p=%d" % (bv, i)})
        season_title = None
    else:
        pages.append({"bvid": bv, "title": data.get("title"),
                      "pic": data.get("pic"),
                      "url": "https://www.bilibili.com/video/" + bv})
        season_title = None
    return {
        "bvid": bv, "title": data.get("title"), "owner": owner,
        "in_season": bool(season),
        "multi_part": multi_part,
        "p_current": p_cur,
        "season_title": season_title,
        "season_count": (season or {}).get("ep_count", 0),
        "count": len(pages), "pages": pages,
    }


# ---------- 原唱识别（只用平台标签，不臆造） ----------
_TAG_STOP = set("""唱歌 音乐 歌曲 听歌 翻唱 COVER cover Cover 女声 男声 清唱 好听 流行 流行音乐
粤语 粤语歌 粤语翻唱 粤语歌曲 时代曲 老歌 经典老歌 经典 情歌 日常 生活记录 记录 睡前 发疯 治愈 声音
打卡挑战 音乐分享官 音乐分享官2.0 翻唱总动员 翻唱挑战赛 新人 新人向 小视频 竖屏音乐会
全能音乐挑战赛 全能音乐挑战赛5.0 新世代音乐人计划女生季 暑期翻唱红人计划 秋日翻唱挑战赛
夏日音浪大作战 粉丝音乐安利大赛2.0 属于我的音乐时间 我的生活随手拍 淘出一万种生活
和我一起唱歌吧 十月打卡挑战W4 音乐UP主培养计划 bilibili校园十佳歌手大赛 B站声乐总动员
哔莱美 音乐节 声乐 十佳歌手 翻唱总动员 音乐区 原创音乐 华语经典歌曲翻唱 弹唱 吉他 钢琴""".split())
_TAG_SKIP_SUB = ("翻唱", "cover", "COVER", "Cover", "打卡", "挑战", "计划", "音乐会", "分享官",
                 "总动员", "音乐节", "哔莱美", "声乐", "十佳", "随手拍", "生活", "记录", "翻唱赛")

def get_tags(bvid):
    try:
        j = http_get_json("https://api.bilibili.com/x/tag/archive/tags?bvid=" + bvid)
        return [t.get("tag_name") or "" for t in (j.get("data") or [])]
    except Exception:
        return []

def derive_original(tags, title):
    title = title or ""
    m = re.search(r"《([^》]+)》", title)
    song = m.group(1).strip() if m else ""
    names, seen = [], set()
    for t in tags:
        tt = (t or "").strip()
        if not tt or tt in _TAG_STOP:
            continue
        if any(s in tt for s in _TAG_SKIP_SUB):
            continue
        # “歌名+歌手”合并标签：剥掉歌名留歌手
        if song and tt.startswith(song) and len(tt) > len(song):
            rem = tt[len(song):].strip()
            if rem and rem not in seen and 1 <= len(rem) <= 6:
                seen.add(rem); names.append(rem)
            continue
        if song and tt == song:
            continue
        if tt in seen:
            continue
        seen.add(tt); names.append(tt)
    return "、".join(names) if names else "待核实"

# ---------- media helpers ----------
def safe_name(t, maxlen=80):
    t = re.sub(r'[\\/:\*\?"<>\|\r\n\t]', " ", t or "").strip()
    t = re.sub(r"\s+", " ", t)
    return (t[:maxlen] or "untitled").strip()


def ffmpeg_env():
    env = os.environ.copy()
    ytdlp_dir = os.path.dirname(YTDLP) or ""
    env["PATH"] = BIN + ":" + (ytdlp_dir + ":" if ytdlp_dir else "") + "/usr/bin:/bin:/usr/sbin:/sbin:" + env.get("PATH", "")
    return env


def parse_srt(text):
    out = []
    for b in re.split(r"\n\s*\n", (text or "").strip()):
        ls = [l for l in b.splitlines() if l.strip()]
        if not ls:
            continue
        ts = 0
        for l in ls:
            m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", l)
            if m:
                ts = ((int(m.group(1)) * 60 + int(m.group(2))) * 60 + int(m.group(3))) * 1000 + int(m.group(4)[:3].ljust(3, "0"))
                break
        txt = " ".join(l for l in ls if "-->" not in l and not re.match(r"^\d+$", l.strip()))
        txt = re.sub(r"<[^>]+>", "", txt).replace("♪", "").strip()
        if txt:
            out.append((ts, txt))
    ded = []
    for ts, t in out:
        if ded and ded[-1][1] == t:
            continue
        ded.append((ts, t))
    return ded


def to_lrc(items):
    lines = []
    for ts, t in items:
        lines.append(f"[{ts // 60000:02d}:{(ts // 1000) % 60:02d}.{(ts % 1000) // 10:02d}]{t}")
    return "\n".join(lines)


def fetch_subtitle_srt(url, workdir):
    """用登录态(Chrome cookies)取该视频的中文字幕轨；没有则返回 None。"""
    out = os.path.join(workdir, "sub.%(ext)s")
    cmd = [YTDLP, "--cookies-from-browser", "chrome", "--skip-download", "--write-subs",
           "--sub-langs", "zh-CN,zh-Hans,zh,zh-Hant,ai-zh", "--sub-format", "srt",
           "--no-warnings", "--ignore-errors", "-o", out, url]
    try:
        subprocess.run(cmd, capture_output=True, text=True, env=ffmpeg_env(), timeout=150)
    except Exception:
        return None
    srts = [os.path.join(workdir, x) for x in os.listdir(workdir) if x.lower().endswith(".srt")]
    if not srts:
        return None

    def rank(p):
        n = os.path.basename(p).lower()
        for i, lang in enumerate(["zh-cn", "zh-hans", "zh-hant", "zh", "ai-zh"]):
            if lang in n:
                return i
        return 99
    srts.sort(key=rank)
    return srts[0]


def embed_lyrics(mp3_path, srt_path):
    items = parse_srt(open(srt_path, encoding="utf-8", errors="ignore").read())
    plain = "\n".join(t for _, t in items)
    if len(plain) < 5:
        return False
    tags = ID3(mp3_path)
    tags.delall("USLT")
    tags.add(USLT(encoding=3, lang="chi", desc="", text=plain))
    tags.save(mp3_path, v2_version=3)
    if any(ts for ts, _ in items):
        try:
            with open(mp3_path[:-4] + ".lrc", "w", encoding="utf-8") as f:
                f.write(to_lrc(items))
        except Exception:
            pass
    return True


def _ocr_img(path):
    try:
        url = NSURL.fileURLWithPath_(path)
        src = Quartz.CGImageSourceCreateWithURL(url, None)
        if src is None:
            return []
        im = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
        if im is None:
            return []
        h = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(im, None)
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setRecognitionLanguages_(["zh-Hans", "en-US"])
        req.setUsesLanguageCorrection_(True)
        ok, _ = h.performRequests_error_([req], None)
        return [o.topCandidates_(1)[0].string() for o in req.results()] if ok else []
    except Exception:
        return []


def _clean_ocr(lines):
    keep = []
    for t in lines:
        t = (t or "").strip()
        if len(t) < 2:
            continue
        cjk = sum(1 for c in t if '\u4e00' <= c <= '\u9fff')
        if cjk < 2:
            continue
        if any(w in t.lower() for w in _OCR_WATER):
            continue
        keep.append(t)
    return "".join(keep)


def _nrm(s):
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", s or "")


def ocr_video(video, workdir, fps=1.5, crop=0.25):
    """对烧录字幕视频抽帧(底部字幕区)+Vision OCR+模糊去重，返回 [(起始毫秒, 文本)]"""
    fdir = os.path.join(workdir, "ocrframes")
    os.makedirs(fdir, exist_ok=True)
    for f in glob.glob(fdir + "/*.jpg"):
        try:
            os.remove(f)
        except Exception:
            pass
    ff = os.path.join(BIN, "ffmpeg")
    vf = "crop=iw:ih*%s:0:ih*%s,fps=%s" % (crop, 1 - crop, fps)
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", video, "-vf", vf,
                    "-q:v", "2", os.path.join(fdir, "f%05d.jpg"), "-y"], capture_output=True)
    frames = sorted(glob.glob(fdir + "/f*.jpg"))
    if not frames:
        return []
    raw = [[i / fps, _clean_ocr(_ocr_img(fr))] for i, fr in enumerate(frames)]
    segs = [(raw[i][0], (raw[i + 1][0] if i + 1 < len(raw) else len(frames) / fps), raw[i][1])
            for i in range(len(raw))]

    def sim(a, b):
        return difflib.SequenceMatcher(None, _nrm(a), _nrm(b)).ratio()

    merged = []
    for s, e, tx in segs:
        if not tx:
            continue
        if merged and (merged[-1][2] == tx or sim(merged[-1][2], tx) >= 0.72
                       or _nrm(tx) in _nrm(merged[-1][2]) or _nrm(merged[-1][2]) in _nrm(tx)):
            keep = tx if len(_nrm(tx)) > len(_nrm(merged[-1][2])) else merged[-1][2]
            merged[-1] = (merged[-1][0], e, keep)
        else:
            merged.append([s, e, tx])
    return [(int(s * 1000), tx) for s, e, tx in merged]


def fetch_video_and_ocr(url, workdir):
    vp = os.path.join(workdir, "ocrvid.%(ext)s")
    cmd = [YTDLP, "--cookies-from-browser", "chrome", "-f", "30064/30032/bv*[height<=720]/b",
           "--no-warnings", "-o", vp, url]
    try:
        subprocess.run(cmd, capture_output=True, text=True, env=ffmpeg_env(), timeout=600)
    except Exception:
        return None
    vids = [os.path.join(workdir, x) for x in os.listdir(workdir)
            if x.startswith("ocrvid.") and x.split(".")[-1] in ("mp4", "mkv", "webm", "flv")]
    if not vids:
        return None
    items = ocr_video(vids[0], workdir)
    if sum(len(t) for _, t in items) < 8:
        return None

    def fmt(x):
        return "%02d:%02d:%02d,%03d" % (x // 3600000, (x % 3600000) // 60000, (x % 60000) // 1000, x % 1000)

    srt = os.path.join(workdir, "ocr.srt")
    with open(srt, "w", encoding="utf-8") as f:
        for k, (ms, tx) in enumerate(items, 1):
            e = items[k][0] if k < len(items) else ms + 2500
            f.write("%d\n%s --> %s\n%s\n\n" % (k, fmt(ms), fmt(e), tx))
    return srt


def download_one(page, folder, idx, total, artist, album, workdir, original="", have_lyrics=True, album_artist=None):
    os.makedirs(workdir, exist_ok=True)
    tmpl = os.path.join(workdir, "%(title)s.%(ext)s")
    cmd = [YTDLP, "-f", "bestaudio", "--extract-audio", "--audio-format", "mp3",
           "--audio-quality", "5", "--no-warnings", "--ignore-errors",
           "--retries", "6", "--fragment-retries", "6",
           "--ffmpeg-location", BIN, "-o", tmpl, page["url"]]
    r = subprocess.run(cmd, capture_output=True, text=True, env=ffmpeg_env())
    cands = [os.path.join(workdir, x) for x in os.listdir(workdir)
             if x.lower().endswith(".mp3")]
    if not cands:
        tail = (r.stderr or r.stdout or "")[-400:]
        raise RuntimeError("yt-dlp 未产出 mp3: " + tail)
    src = max(cands, key=os.path.getmtime)
    # tag
    if HAVE_TAG:
        try:
            tag_mp3(src, page.get("title") or "untitled", artist, album, idx, total, page.get("pic"), original, album_artist)
        except Exception as e:
            log("tag fail", page.get("url"), repr(e))
    final = os.path.join(folder, "%03d_%s.mp3" % (idx, safe_name(page.get("title") or "untitled")))
    os.replace(src, final) if os.path.exists(final) is False else _replace(final, src)
    # 歌词（需要时）：优先 OCR 画面烧录字幕（音乐视频CC轨准确率低）；OCR 拿不到才用 CC 字幕轨
    if have_lyrics and HAVE_TAG:
        try:
            srt = None
            if OCR_ENABLED and HAVE_OCR:
                srt = fetch_video_and_ocr(page["url"], workdir)  # 烧录字幕 OCR 优先
            if not srt:
                log("无烧录字幕(OCR空), 改用CC字幕轨 ->", page.get("url"))
                srt = fetch_subtitle_srt(page["url"], workdir)
            if srt and embed_lyrics(final, srt):
                log("lyrics embedded", page.get("url"), os.path.basename(srt))
        except Exception as e:
            log("lyrics fail", page.get("url"), repr(e))
    return final


def _replace(dst, src):
    try:
        os.remove(dst)
    except Exception:
        pass
    os.replace(src, dst)


def tag_mp3(path, title, artist, album, idx, total, pic_url, original="", album_artist=None):
    tmp_cover = os.path.join(TMP, "cover_%s.jpg" % uuid.uuid4().hex)
    cover_bytes = None
    if pic_url:
        try:
            req = urllib.request.Request(pic_url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = r.read()
            if len(raw) > 1000:
                im = Image.open(__import__("io").BytesIO(raw)).convert("RGB")
                w, h = im.size
                s = min(w, h)
                l = (w - s) // 2; t = (h - s) // 2
                im = im.crop((l, t, l + s, t + s)).resize((640, 640), Image.LANCZOS)
                im.save(tmp_cover, "JPEG", quality=85)
                cover_bytes = open(tmp_cover, "rb").read()
        except Exception as e:
            log("cover fail", path, repr(e))
    try:
        try:
            tags = ID3(path)
        except Exception:
            tags = ID3()
        for k in ("TIT2", "TPE1", "TPE2", "TALB", "TRCK", "APIC", "COMM", "TXXX:原唱"):
            tags.delall(k)
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))
        tags.add(TPE2(encoding=3, text=(album_artist or artist)))
        tags.add(TALB(encoding=3, text=album or "单曲"))
        tags.add(TRCK(encoding=3, text="%d/%d" % (idx, total)))
        if original:
            tags.add(COMM(encoding=3, lang="chi", desc="", text=["原唱：" + original]))
            tags.add(TXXX(encoding=3, desc="原唱", text=[original]))
        if cover_bytes:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_bytes))
        tags.save(path, v2_version=3)
    finally:
        if os.path.exists(tmp_cover):
            try: os.remove(tmp_cover)
            except Exception: pass


def import_to_music(paths):
    """把若干 mp3 导入 macOS 音乐 App 资料库。返回 (imported_count, error_str)."""
    if not paths:
        return 0, None
    def esc(p):
        return p.replace("\\", "\\\\").replace('"', '\\"')
    lines = ['tell application "Music"'] + ['add POSIX file "%s"' % esc(p) for p in paths] + ['end tell']
    script = "\n".join(lines)
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=600)
    except Exception as e:
        return 0, "osascript 调用失败: %r" % e
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "-1743" in err or "not allowed" in err or "不允许" in err:
            return 0, "没有获得「控制音乐」权限：请在 系统设置>隐私与安全性>自动化 里允许，然后再试。"
        return 0, "导入失败: " + err[-300:]
    return len(paths), None


# ---------- job ----------
def run_job(job_id, url, mode):
    job = JOBS[job_id]
    try:
        info = resolve(url)
    except Exception as e:
        job.update(status="error", message=str(e))
        log("resolve error", url, repr(e))
        return
    job["info"] = {k: info[k] for k in ("title", "owner", "in_season", "season_title", "season_count", "count")}
    if not job.get("title"):
        job["title"] = info.get("title") or ""
    pages = info["pages"]
    if mode == "single":
        if info.get("multi_part"):
            _idx = info.get("p_current", 1)
            pages = pages[_idx - 1:_idx] if 1 <= _idx <= len(pages) else pages[:1]
        else:
            same = [p for p in pages if p["bvid"] == info["bvid"]]
            pages = same[:1] if same else pages[:1]
    job["total"] = len(pages)
    job["status"] = "running"
    if mode != "single" and info["in_season"]:
        album = info["season_title"]
    elif mode != "single" and info.get("multi_part"):
        album = info["title"]
    else:
        album = "单曲"
    owner = info["owner"]
    folder = os.path.join(MUSIC_BASE, "UP 主 " + safe_name(owner, 60))
    os.makedirs(folder, exist_ok=True)
    job["folder"] = folder
    new_files = []
    for i, p in enumerate(pages, 1):
        if job.get("cancel"):
            job["message"] = "已取消"; break
        key = p["url"]
        prev = DONE.get(key)
        if prev and os.path.exists(prev.get("path", "")):
            job["done"] = i
            job["message"] = "跳过(已下载) %d/%d: %s" % (i, len(pages), p.get("title"))
            if DO_IMPORT and not prev.get("imported"):
                new_files.append(prev["path"])
            continue
        # 已存在同名成品（例如之前手动下过）→ 跳过下载，但仍列入待导入
        existing = os.path.join(folder, "%03d_%s.mp3" % (i, safe_name(p.get("title") or "untitled")))
        if os.path.exists(existing) and os.path.getsize(existing) > 10240:
            DONE[key] = {"path": existing, "imported": False, "title": p.get("title"), "owner": owner}
            save_done(DONE)
            new_files.append(existing)
            job["done"] = i
            job["message"] = "已存在，跳过下载 %d/%d: %s" % (i, len(pages), p.get("title"))
            continue
        job["message"] = "下载 %d/%d: %s" % (i, len(pages), p.get("title"))
        workdir = os.path.join(TMP, job_id, "%03d" % i)
        try:
            tags = get_tags(p["bvid"])
            title = p.get("title") or ""
            orig = derive_original(tags, title)
            tl = title.lower()
            tagsl = [x.lower() for x in tags]
            is_cover = ("翻唱" in title) or ("cover" in tl) or ("翻唱" in tags) or ("cover" in tagsl)
            # 翻唱 → 艺人=翻唱者(UP主)；疑似原唱原声且能识别 → 艺人=原唱，不用 UP 主
            _bad = ["音乐", "现场", "LIVE", "live", "cover", "COVER", "翻唱", "演唱会", "合唱", "大赛", "电台", "神级"]
            clean = orig if (orig and orig != "待核实" and 2 <= len(orig) <= 8 and "、" not in orig
                             and not any(w in orig for w in _bad)) else None
            artist = clean if (not is_cover and clean) else owner
            orig_tag = clean or "待核实"
            f = download_one(p, folder, i, len(pages), artist, album, workdir, orig_tag,
                             job.get("lyrics", True), owner)
            DONE[key] = {"path": f, "imported": False, "title": p.get("title"), "owner": owner}
            save_done(DONE)
            new_files.append(f)
            job["done"] = i
            log("downloaded", p["url"], "->", f)
        except Exception as e:
            job["errors"].append("%s: %s" % (p.get("title"), e))
            job["done"] = i
            log("download error", p["url"], repr(e))
    # import new files
    if DO_IMPORT and new_files:
        job["message"] = "导入「音乐」资料库 %d 首…" % len(new_files)
        with IMPORT_LOCK:  # 串行导入，避免多个 osascript 同时控制「音乐」冲突
            cnt, err = import_to_music(new_files)
        job["imported"] = cnt
        if err:
            job["errors"].append("音乐库导入: " + err)
        else:
            for f in new_files:
                for k, v in DONE.items():
                    if v.get("path") == f:
                        v["imported"] = True
            save_done(DONE)
    job["status"] = "done"
    job["message"] = "完成：下载 %d，导入 %d，失败 %d" % (
        len(new_files), job.get("imported", 0), len(job["errors"]))
    try:
        shutil.rmtree(os.path.join(TMP, job_id))
    except Exception:
        pass
    log("job done", job_id, job["message"])


# ---------- http ----------
class H(BaseHTTPRequestHandler):
    server_version = "Bili2Music/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-Bili-Token, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        # Chrome 私有网络访问(PNA)：扩展(chrome-extension://)访问 127.0.0.1 的预检需要此头
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        return self.headers.get("X-Bili-Token", "") == TOKEN

    def log_message(self, *a):
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True, "service": "bili2music", "version": "1.0",
                                    "music_base": MUSIC_BASE, "import": DO_IMPORT})
        if not self._auth():
            return self._json(401, {"error": "unauthorized"})
        if self.path == "/jobs":
            lst = sorted(JOBS.values(), key=lambda j: j.get("created", 0), reverse=True)
            out = [{k: j.get(k) for k in ("id", "title", "url", "status", "done", "total", "message", "created")} for j in lst[:60]]
            return self._json(200, {"jobs": out, "concurrency": CONCURRENCY})
        m = re.match(r"^/job/([0-9a-f]+)$", self.path)
        if m:
            j = JOBS.get(m.group(1))
            if not j:
                return self._json(404, {"error": "no such job"})
            return self._json(200, j)
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth():
            return self._json(401, {"error": "unauthorized"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return self._json(400, {"error": "bad json"})
        if self.path == "/resolve":
            try:
                info = resolve(body.get("url", ""))
                return self._json(200, {k: info[k] for k in
                                 ("title", "owner", "in_season", "season_title", "season_count", "count")})
            except Exception as e:
                return self._json(200, {"error": str(e)})
        if self.path == "/download":
            url = body.get("url", "")
            mode = body.get("mode", "auto")
            if not extract_bvid(url):
                return self._json(400, {"error": "没识别到 BV 号"})
            jid = uuid.uuid4().hex[:12]
            JOBS[jid] = {"id": jid, "url": url, "mode": mode, "lyrics": bool(body.get("lyrics", True)),
                         "status": "queued", "done": 0, "total": 0, "message": "排队中…",
                         "errors": [], "imported": 0, "title": "", "created": time.time()}
            JOB_QUEUE.put(jid)
            return self._json(200, {"job_id": jid})
        return self._json(404, {"error": "not found"})


def worker_loop():
    while True:
        jid = JOB_QUEUE.get()
        try:
            job = JOBS.get(jid)
            if job and job.get("status") == "queued":
                job["status"] = "running"
                job["message"] = "准备中…"
                run_job(jid, job["url"], job["mode"])
        except Exception as e:
            log("worker error", jid, repr(e))
        finally:
            JOB_QUEUE.task_done()


def start_workers(n):
    for _ in range(max(1, n)):
        threading.Thread(target=worker_loop, daemon=True).start()


def main():
    log("bridge starting on 127.0.0.1:%d  music_base=%s import=%s concurrency=%d" % (PORT, MUSIC_BASE, DO_IMPORT, CONCURRENCY))
    start_workers(CONCURRENCY)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
