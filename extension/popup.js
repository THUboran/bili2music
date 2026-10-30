const $ = (s) => document.querySelector(s);
let currentUrl = "";
let pollTimer = null;

function api(path, body) {
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

async function init() {
  try {
    const h = await api("/health");
    setConn("ok", "已连接");
  } catch (e) {
    setConn("bad", "未连接（先启动本机桥）");
    $("#go").disabled = true;
    return;
  }
  chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
    const tab = tabs[0] || {};
    currentUrl = tab.url || "";
    const m = currentUrl.match(/BV[0-9A-Za-z]{10}/);
    if (!m) { show($("#notpage")); return; }
    try {
      const info = await api("/resolve", { url: currentUrl });
      if (info.error) { $("#notpage").textContent = info.error; show($("#notpage")); return; }
      $("#vtitle").textContent = info.title || "(无标题)";
      $("#vowner").textContent = "UP：" + (info.owner || "?") + "　共 " + info.count + " 个视频";
      if (info.in_season && info.count > 1) {
        const t = $("#vseason");
        t.textContent = "合集《" + (info.season_title || "") + "》";
        show(t);
        document.querySelector('input[value="collection"]').checked = true;
      } else {
        document.querySelector('input[value="single"]').checked = true;
        document.querySelector('input[value="collection"]').disabled = true;
        document.querySelector('input[value="single"]').checked = true;
      }
      show($("#detect")); show($("#opts"));
      $("#go").disabled = false;
    } catch (e) {
      $("#notpage").textContent = "读取视频信息失败：" + e.message;
      show($("#notpage"));
    }
  });
}

$("#go").addEventListener("click", async () => {
  const mode = (document.querySelector('input[name="mode"]:checked') || {}).value || "single";
  const wantLyrics = document.getElementById("wantLyrics").checked;
  $("#go").disabled = true;
  hide($("#detect")); hide($("#opts"));
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
