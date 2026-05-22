// Mindframe dashboard SPA — panes feed.
//
// Polls /api/panes for ephemeral pane artifacts written by dispatcher-spawned
// agents under artifacts/<sid>/latest.html. Each pane renders as a same-origin
// iframe. Action buttons inside the agent-authored HTML can call
// `parent.mindframe.postEvent(event_type, data)` to fire a dispatcher event —
// the SPA forwards via /api/dashboard-event so the bearer token stays
// server-side.

const POLL_INTERVAL_MS = 3000;
const HEALTH_POLL_MS = 15000;

const $ = (id) => document.getElementById(id);

const panesById = new Map(); // sid -> { mtime_ms, el }

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

function makePaneEl(pane) {
  const wrap = document.createElement("section");
  wrap.className = "pane";
  wrap.dataset.sid = pane.sid;
  wrap.innerHTML = `
    <header class="pane-header">
      <span class="pane-sid mono">${pane.sid}</span>
      <span class="pane-mtime mono">${new Date(pane.mtime_ms).toLocaleTimeString()}</span>
      <button class="pane-share" type="button" title="Snapshot this pane">share</button>
    </header>
    <iframe class="pane-frame" src="${pane.url}?t=${pane.mtime_ms}" title="pane ${pane.sid}"></iframe>
  `;
  wrap.querySelector(".pane-share").addEventListener("click", async () => {
    try {
      const r = await fetch("/api/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sid: pane.sid, label: `pane ${pane.sid}` }),
      });
      const j = await r.json();
      if (j.url) {
        await navigator.clipboard?.writeText(location.origin + j.url).catch(() => {});
        showToast(`saved → ${j.url}`, "ok");
      } else {
        showToast(j.error || "save failed", "err");
      }
    } catch (e) {
      showToast(`save failed: ${e}`, "err");
    }
  });
  return wrap;
}

function updatePaneEl(el, pane) {
  el.querySelector(".pane-mtime").textContent = new Date(pane.mtime_ms).toLocaleTimeString();
  const iframe = el.querySelector(".pane-frame");
  iframe.src = `${pane.url}?t=${pane.mtime_ms}`;
}

function reconcilePanes(panes) {
  const container = $("panes");
  const seen = new Set();
  // Render newest first — panes already sorted desc by server.
  for (const pane of panes) {
    seen.add(pane.sid);
    const existing = panesById.get(pane.sid);
    if (!existing) {
      const el = makePaneEl(pane);
      panesById.set(pane.sid, { mtime_ms: pane.mtime_ms, el });
      container.prepend(el);
    } else if (pane.mtime_ms > existing.mtime_ms) {
      updatePaneEl(existing.el, pane);
      existing.mtime_ms = pane.mtime_ms;
      // Move to top — it just changed.
      container.prepend(existing.el);
    }
  }
  // Remove panes that disappeared.
  for (const [sid, entry] of panesById) {
    if (!seen.has(sid)) {
      entry.el.remove();
      panesById.delete(sid);
    }
  }
  $("pane-count").textContent = panes.length;
  $("empty").hidden = panes.length > 0;
}

async function pollPanes() {
  try {
    const r = await fetch("/api/panes");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    reconcilePanes(j.panes || []);
    setConn("ok", `live · ${j.panes?.length || 0} pane${j.panes?.length === 1 ? "" : "s"}`);
  } catch (e) {
    setConn("err", `connection lost (${e.message})`);
  }
}

async function pollHealth() {
  try {
    const r = await fetch("/api/health");
    const j = await r.json();
    const ds = $("dispatcher-state");
    if (j.dispatcher_bearer_present) {
      ds.textContent = `dispatcher: ${j.dispatcher_url}`;
      ds.dataset.state = "ok";
    } else {
      ds.textContent = `dispatcher: no bearer (${j.dispatcher_url})`;
      ds.dataset.state = "warn";
    }
  } catch {
    $("dispatcher-state").textContent = "dispatcher: unknown";
  }
}

// API the agent-authored HTML inside a pane can call via parent.mindframe.*
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

pollHealth();
pollPanes();
setInterval(pollPanes, POLL_INTERVAL_MS);
setInterval(pollHealth, HEALTH_POLL_MS);
