// Mindframe dashboard SPA — boards-index + per-mindframe detail.
//
// Two views, switched by URL:
//   /        → boards index: compact list of mindframes, click to open
//   /m/<id>  → one mindframe, focused fullscreen iframe
//
// Each mindframe is one piece of work. Operator picks one to work on,
// switches between them via the index (or browser tabs). The dashboard
// is the index; the mindframe IS the work.

const POLL_INTERVAL_MS = 3000;
const HEALTH_POLL_MS = 15000;

const $ = (id) => document.getElementById(id);
const root = () => $("root");

function setConn(state, label) {
  const el = $("conn");
  el.textContent = label;
  el.dataset.state = state;
}

function showToast(msg, kind = "info") {
  const t = $("toast");
  t.textContent = msg;
  t.dataset.kind = kind;
  t.hidden = false;
  clearTimeout(showToast._h);
  showToast._h = setTimeout(() => { t.hidden = true; }, 3500);
}

function relativeTime(mtime_ms) {
  const ms = Date.now() - mtime_ms;
  if (ms < 60_000) return "just now";
  if (ms < 3_600_000) return Math.floor(ms / 60_000) + " min ago";
  if (ms < 86_400_000) return Math.floor(ms / 3_600_000) + "h ago";
  return Math.floor(ms / 86_400_000) + "d ago";
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}

// ----- API the agent-authored iframe HTML calls (only on /m/<id>) -----

window.mindframe = {
  async postEvent(event_type, data) {
    if (!event_type || typeof event_type !== "string") {
      showToast("postEvent: event_type required", "err");
      return { ok: false, error: "event_type required" };
    }
    try {
      const r = await fetch("/api/dashboard-event", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_type, data: data ?? null }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        showToast(`event failed: ${j.error || r.statusText}`, "err");
        return { ok: false, ...j };
      }
      showToast(`event dispatched → ${event_type}`, "ok");
      return { ok: true, ...j };
    } catch (e) {
      showToast(`event failed: ${e}`, "err");
      return { ok: false, error: String(e) };
    }
  },
};

// ----- Boards index view -----

async function renderBoardsIndex() {
  root().innerHTML = `
    <div class="index-wrap">
      <p class="index-eyebrow">your work</p>
      <div class="index-header">
        <h1>Mindframes</h1>
        <span id="frame-count" class="count">…</span>
      </div>
      <div id="frame-list" class="frame-list">
        <div class="loading">loading…</div>
      </div>
    </div>
  `;

  async function refresh() {
    try {
      const r = await fetch("/api/panes");
      const j = await r.json();
      const frames = j.panes || [];
      setConn("ok", `live · ${frames.length} mindframe${frames.length === 1 ? "" : "s"}`);
      $("frame-count").textContent = frames.length;
      const list = $("frame-list");
      if (!frames.length) {
        list.innerHTML = `
          <div class="empty">
            <p>No mindframes yet.</p>
            <p class="empty-hint">Fire a dispatcher event to spawn one:</p>
            <pre>curl -X POST http://127.0.0.1:8911/api/event \\
  -H "Authorization: Bearer $(cat ~/.mindframe/secrets/dispatcher-bearer.token)" \\
  -H "Content-Type: application/json" \\
  -d '{"source":"manual","event_type":"test","data":{}}'</pre>
          </div>`;
        return;
      }
      list.innerHTML = frames.map(f => `
        <a class="frame-row" href="/m/${encodeURIComponent(f.sid)}">
          <span class="frame-marker"></span>
          <span class="frame-sid">${escapeHtml(f.sid)}</span>
          <span class="frame-meta">
            <span class="frame-size">${(f.bytes / 1024).toFixed(1)} KB</span>
            <span class="frame-time">${relativeTime(f.mtime_ms)}</span>
          </span>
          <span class="frame-open">→</span>
        </a>
      `).join("");
    } catch (e) {
      setConn("err", `connection lost (${e.message})`);
    }
  }

  refresh();
  return setInterval(refresh, POLL_INTERVAL_MS);
}

// ----- Mindframe detail view -----

async function renderMindframeDetail(sid) {
  root().innerHTML = `
    <div class="detail-wrap">
      <nav class="detail-nav">
        <a class="back" href="/">← all mindframes</a>
        <span class="detail-sid mono">${escapeHtml(sid)}</span>
        <span class="detail-actions">
          <button id="detail-share" type="button">share</button>
        </span>
      </nav>
      <div class="detail-stage">
        <iframe id="detail-frame" class="detail-frame"
                src="/artifacts/${encodeURIComponent(sid)}/latest.html"
                title="mindframe ${escapeHtml(sid)}"></iframe>
      </div>
    </div>
  `;

  $("detail-share").addEventListener("click", async () => {
    try {
      const r = await fetch("/api/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sid, label: `mindframe ${sid}` }),
      });
      const j = await r.json();
      if (j.url) {
        await navigator.clipboard?.writeText(location.origin + j.url).catch(() => {});
        showToast(`shared → ${location.origin}${j.url} (copied)`, "ok");
      } else {
        showToast(j.error || "share failed", "err");
      }
    } catch (e) {
      showToast(`share failed: ${e}`, "err");
    }
  });

  setConn("ok", "viewing");

  // Refresh iframe periodically by toggling its src; the agent writes
  // new content in place and we want the operator to see updates.
  const iframe = $("detail-frame");
  let lastMtime = 0;
  async function checkUpdate() {
    try {
      const r = await fetch("/api/panes");
      const j = await r.json();
      const f = (j.panes || []).find(p => p.sid === sid);
      if (f && f.mtime_ms !== lastMtime) {
        if (lastMtime) {
          // re-load only on subsequent updates (avoid reload-on-first-poll flash)
          iframe.src = `/artifacts/${encodeURIComponent(sid)}/latest.html?t=${f.mtime_ms}`;
        }
        lastMtime = f.mtime_ms;
      }
    } catch { /* ignore */ }
  }
  checkUpdate();
  return setInterval(checkUpdate, POLL_INTERVAL_MS);
}

// ----- Health probe (chrome dispatcher indicator) -----

async function pollHealth() {
  try {
    const r = await fetch("/api/health");
    const j = await r.json();
    const ds = $("dispatcher-state");
    if (j.dispatcher_bearer_present) {
      ds.textContent = `dispatcher: connected`;
      ds.dataset.state = "ok";
    } else {
      ds.textContent = `dispatcher: no bearer`;
      ds.dataset.state = "warn";
    }
  } catch {
    $("dispatcher-state").textContent = "dispatcher: down";
  }
}

// ----- Router -----

function route() {
  const path = location.pathname;
  const m = path.match(/^\/m\/([a-zA-Z0-9_-]+)\/?$/);
  if (m) {
    renderMindframeDetail(decodeURIComponent(m[1]));
  } else {
    renderBoardsIndex();
  }
}

pollHealth();
setInterval(pollHealth, HEALTH_POLL_MS);
route();
