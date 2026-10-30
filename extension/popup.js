const $ = (s) => document.querySelector(s);
const VERSION = "1.5";
let currentUrl = "";
let jobsTimer = null;
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
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function updateModeInfo() {
  const el = $("#modeinfo");
  if (!el) return;
  const mode = (document.querySelector('input[name="mode"]:checked') || {}).value || "single";
  if (mode === "collection") {
    if (seasonInfo.in_season) el.textContent = "将下载：整个合集《" + seasonInfo.season_title + "》 共 " + seasonInfo.count + " 首";
    else if (seasonInfo.count > 1) el.textContent = "将下载：全部 " + seasonInfo.count + " 段（多P视频）";
    else el.textContent = "当前视频只有 1 段，将只下载单条";
  } else el.textContent = "将下载：仅当前视频";
}

function renderJobs(data) {
  const list = $("#joblist");
  const jobs = (data.jobs || []).slice();
  $("#concurrency").textContent = "并发上限 " + (data.concurrency || 1);
  if (!jobs.length) { list.innerHTML = '<div class="js">暂无任务</div>'; return; }
  const rank = { running: 0, queued: 1, starting: 2, error: 3, done: 4 };
  jobs.sort((a, b) => ((rank[a.status] != null ? rank[a.status] : 5) - (rank[b.status] != null ? rank[b.status] : 5)) || ((b.created || 0) - (a.created || 0)));
  list.innerHTML = "";
  jobs.slice(0, 8).forEach((j) => {
    const total = j.total || 0, done = j.done || 0;
    const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
    const st = { queued: "排队中", running: "进行中", starting: "准备中", done: "完成", error: "出错" }[j.status] || j.status;
    let sub = st;
    if (j.status === "running" && total) sub = st + " · " + pct + "% (" + done + "/" + total + ")";
    else if (j.status === "done") sub = "完成 · " + (j.total ? total + " 项" : "");
    else if (j.status === "queued") sub = "排队中…";
    const el = document.createElement("div");
    el.className = "job " + (j.status === "done" ? "done" : (j.status === "error" ? "err" : ""));
    el.innerHTML = '<div class="jt">' + esc(j.title || j.url || "(任务)") + '</div>' +
      '<div class="js">' + esc(sub) + (j.status === "error" && j.message ? "：" + esc(j.message) : "") + '</div>' +
      '<div class="jbar"><i style="width:' + (j.status === "done" ? 100 : pct) + '%"></i></div>';
    list.appendChild(el);
  });
}

async function loadJobs() {
  try {
    const data = await api("/jobs");
    show($("#tasksec"));
    renderJobs(data);
  } catch (e) {
    $("#joblist").innerHTML = '<div class="js">读取任务失败：' + esc(e.message) + '</div>';
    show($("#tasksec"));
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
  loadJobs();
  if (jobsTimer) clearInterval(jobsTimer);
  jobsTimer = setInterval(loadJobs, 1200);

  chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
    currentUrl = (tabs[0] || {}).url || "";
    if (!/BV[0-9A-Za-z]{10}/.test(currentUrl)) { return; }
    try {
      const info = await api("/resolve", { url: currentUrl });
      if (info.error) return;
      seasonInfo = { in_season: !!info.in_season, count: info.count || 0, season_title: info.season_title || "" };
      $("#vtitle").textContent = info.title || "(无标题)";
      $("#vowner").textContent = "UP：" + (info.owner || "?") + "　共 " + info.count + " 个视频";
      const t = $("#vseason");
      if (info.count > 1) {
        t.textContent = info.in_season ? ("合集《" + info.season_title + "》") : ("多P视频（共 " + info.count + " 段）");
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
      $("#go").textContent = "加入下载队列";
    } catch (e) {}
  });
}

document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", updateModeInfo));

$("#go").addEventListener("click", async () => {
  let mode = (document.querySelector('input[name="mode"]:checked') || {}).value || "single";
  if (mode === "collection" && seasonInfo.count <= 1) mode = "single";
  $("#go").disabled = true;
  const old = $("#go").textContent;
  $("#go").textContent = "提交中…";
  try {
    const r = await api("/download", { url: currentUrl, mode });
    if (r.error) throw new Error(r.error);
    await loadJobs();
    $("#go").textContent = old;
    $("#go").disabled = false;
  } catch (e) {
    $("#go").textContent = old;
    $("#go").disabled = false;
    alert("提交失败：" + e.message);
  }
});

init();
