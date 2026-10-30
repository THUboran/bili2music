const $ = (s) => document.querySelector(s);
const VERSION = "1.1";
let currentUrl = "";
let pollTimer = null;
let seasonInfo = { in_season: false, count: 0, season_title: "" };

function api(path, body) {
  if (typeof B2M === "undefined" || !B2M.base || !B2M.token) {
    return Promise.reject(new Error("缺少 config.js（token/base 未配置）"));
  }
  const opt = { headers: { "X-Bili-Token": B2M.token, "Content-Type": "application/json" } };
  if (body !== undefined) { opt.method = "POST"; opt.body = JSON.stringify(body); }
  return fetch(B2M.base + path, opt).then((r) => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  });
}

function setConn(state, text) {
  const el = $("#conn");
  el.className = "conn " + state;
  el.textContent = text || (state === "ok" ? "本机桥已连接" : "本机桥未启动");
}

function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }

function updateModeInfo() {
  const el = $("#modeinfo");
  if (!el) return;
  const mode = (document.querySelector('input[name="mode"]:checked') || {}).value || "single";
  if (mode === "collection") {
    el.textContent = seasonInfo.in_season
      ? "将下载：整个合集《" + seasonInfo.season_title + "》 共 " + seasonInfo.count + " 首"
      : "当前视频不在合集中，将只下载当前单条";
  } else {
    el.textContent = "将下载：仅当前视频";
  }
}

async function init() {
  console.log("Bili2Music popup", VERSION);
  try {
    await api("/health");
    setConn("ok", "已连接 v" + VERSION);
  } catch (e) {
    setConn("bad", "未连接：" + e.message);
    $("#go").disabled = true;
    return;
  }
  chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
    currentUrl = (tabs[0] || {}).url || "";
    if (!/BV[0-9A-Za-z]{10}/.test(currentUrl)) { show($("#notpage")); return; }
    try {
      const info = await api("/resolve", { url: currentUrl });
      if (info.error) { $("#notpage").textContent = info.error; show($("#notpage")); return; }
      seasonInfo = { in_season: !!info.in_season, count: info.count || 0, season_title: info.season_title || "" };
      $("#vtitle").textContent = info.title || "(无标题)";
      $("#vowner").textContent = "UP：" + (info.owner || "?") + "　共 " + info.count + " 个视频";
      const t = $("#vseason");
      if (info.in_season && info.count > 1) {
        t.textContent = "合集《" + info.season_title + "》";
        show(t);
        document.querySelector('input[value="collection"]').checked = true;
      } else {
        hide(t);
        document.querySelector('input[value="single"]').checked = true;
      }
      document.querySelectorAll('input[name="mode"]').forEach((r) => { r.disabled = false; });
      show($("#detect")); show($("#opts")); show($("#modeinfo"));
      updateModeInfo();
      $("#go").disabled = false;
    } catch (e) {
      $("#notpage").textContent = "读取视频信息失败：" + e.message;
      show($("#notpage"));
    }
  });
}

document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", updateModeInfo));

$("#go").addEventListener("click", async () => {
  let mode = (document.querySelector('input[name="mode"]:checked') || {}).value || "single";
  if (mode === "collection" && !seasonInfo.in_season) mode = "single";
  const wantLyrics = document.getElementById("wantLyrics").checked;
  $("#go").disabled = true;
  hide($("#detect")); hide($("#opts")); hide($("#modeinfo"));
  show($("#prog"));
  $("#msg").textContent = "提交中…";
  $("#errs").textContent = "";
  $("#bar").style.width = "0%";
  try {
    const r = await api("/download", { url: currentUrl, mode, lyrics: wantLyrics });
    if (r.error) throw new Error(r.error);
    poll(r.job_id);
  } catch (e) {
    $("#msg").textContent = "提交失败：" + e.message;
    $("#go").disabled = false;
  }
});

function poll(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const j = await api("/job/" + jobId);
      const total = j.total || 0;
      const done = j.done || 0;
      $("#bar").style.width = total ? Math.round((done / total) * 100) + "%" : "8%";
      $("#msg").textContent = j.message || j.status;
      if (j.errors && j.errors.length) {
        $("#errs").textContent = j.errors.slice(-6).join("\n");
      }
      if (j.status === "done" || j.status === "error") {
        clearInterval(pollTimer); pollTimer = null;
        $("#bar").style.width = "100%";
        $("#go").disabled = false;
      }
    } catch (e) {
      $("#msg").textContent = "查询进度失败：" + e.message;
    }
  }, 900);
}

init();
