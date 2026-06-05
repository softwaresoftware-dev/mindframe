// Mindframe dashboard SPA.
//
// Two views:
//   /        → home: vaults + data sources
//   /system  → structured overview of the whole bundle (events, agents,
//              mindframes, skills+MCPs, knowledge bases)
//
// A mindframe is a surface (the agent owns one index.html it rewrites). Block-
// stream rendering was removed 2026-06-04; per-mindframe viewing is rebuilt on
// the surface model in a later migration step.

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
  if (!mtime_ms) return "—";
  const ms = Date.now() - mtime_ms;
  if (ms < 60_000) return "just now";
  if (ms < 3_600_000) return Math.floor(ms / 60_000) + " min ago";
  if (ms < 86_400_000) return Math.floor(ms / 3_600_000) + "h ago";
  return Math.floor(ms / 86_400_000) + "d ago";
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}

// Tiny inline markdown — enough for text blocks. Handles: # headings,
// **bold**, *italic*, `inline code`, ```fenced code```, - lists, paragraphs,
// [text](url). Not GFM-complete; agents writing complex markdown should use
// a code block.
function renderMarkdown(src) {
  const lines = String(src ?? "").split("\n");
  const out = [];
  let inCode = false, codeLang = "", codeBuf = [];
  let inList = false, paraBuf = [];

  const flushPara = () => {
    if (paraBuf.length) {
      out.push("<p>" + inlineMd(paraBuf.join(" ")) + "</p>");
      paraBuf = [];
    }
  };
  const flushList = () => {
    if (inList) { out.push("</ul>"); inList = false; }
  };

  for (const raw of lines) {
    if (inCode) {
      if (raw.startsWith("```")) {
        out.push(`<pre class="md-code"><code class="lang-${escapeHtml(codeLang)}">${escapeHtml(codeBuf.join("\n"))}</code></pre>`);
        inCode = false; codeLang = ""; codeBuf = [];
      } else {
        codeBuf.push(raw);
      }
      continue;
    }
    if (raw.startsWith("```")) {
      flushPara(); flushList();
      inCode = true; codeLang = raw.slice(3).trim();
      continue;
    }
    const h = raw.match(/^(#{1,4})\s+(.+)$/);
    if (h) {
      flushPara(); flushList();
      const lvl = h[1].length;
      out.push(`<h${lvl} class="md-h md-h${lvl}">${inlineMd(h[2])}</h${lvl}>`);
      continue;
    }
    const li = raw.match(/^[-*]\s+(.+)$/);
    if (li) {
      flushPara();
      if (!inList) { out.push("<ul class=\"md-list\">"); inList = true; }
      out.push(`<li>${inlineMd(li[1])}</li>`);
      continue;
    }
    if (raw.trim() === "") {
      flushPara(); flushList();
      continue;
    }
    flushList();
    paraBuf.push(raw);
  }
  flushPara(); flushList();
  if (inCode) {
    out.push(`<pre class="md-code"><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`);
  }
  return out.join("\n");
}

function inlineMd(s) {
  // Escape first, then re-introduce only the markers we recognize.
  let html = escapeHtml(s);
  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code class="md-icode">$1</code>');
  // Bold
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // Italic (single * not adjacent to space)
  html = html.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
  // Links [text](url) — url is escaped already
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" rel="noopener">$1</a>');
  return html;
}

// ----- window.mindframe.postEvent — called by button-row blocks -----

window.mindframe = {
  async postEvent(event_type, data) {
    if (!event_type || typeof event_type !== "string") {
      showToast("postEvent: event_type required", "err");
      return { ok: false };
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
      showToast(`dispatched → ${event_type}`, "ok");
      return { ok: true, ...j };
    } catch (e) {
      showToast(`event failed: ${e}`, "err");
      return { ok: false };
    }
  },
};

// ----- el(): generic DOM builder helper -----

function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k === "onClick") e.addEventListener("click", v);
    else if (v != null) e.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return e;
}

// ----- Boards index view -----

// ----- Knowledge base panel (single vault) -----

function vaultLastTouched(v) {
  if (!v.last_commit?.committed_at) return "no commits yet";
  return relativeTime(new Date(v.last_commit.committed_at).getTime());
}

async function refreshVault() {
  const list = $("vault-list");
  try {
    const r = await fetch("/api/vault");
    const v = await r.json();
    if (!r.ok || v.error || !v.exists) {
      $("vault-count").textContent = "—";
      list.innerHTML = `<div class="empty"><p>No knowledge base yet.
        Run <code>/mindframe:setup</code> to create one at <code>~/.mindframe/vault</code>.</p></div>`;
      return;
    }
    $("vault-count").textContent = v.total_entries;
    const typeCounts = Object.entries(v.entry_counts || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([t, n]) => `<span class="vault-type-chip">${escapeHtml(t)}: ${n}</span>`)
      .join("");
    const remoteBadge = v.remote
      ? `<span class="vault-remote-badge" title="${escapeHtml(v.remote)}">⇄ remote</span>`
      : `<span class="vault-remote-badge vault-remote-local">● local only</span>`;
    list.innerHTML = `
      <div class="vault-tile">
        <div class="vault-tile-header">
          <span class="vault-name">${escapeHtml(v.name)}</span>
          ${remoteBadge}
        </div>
        <div class="vault-tile-meta">
          <span class="vault-total">${v.total_entries} entries</span>
          <span class="vault-touched">last touched ${vaultLastTouched(v)}</span>
        </div>
        <div class="vault-type-chips">${typeCounts || '<span class="vault-empty-note">empty</span>'}</div>
        <div class="vault-tile-actions">
          <button class="btn btn-sm btn-default vault-action-browse" type="button">browse</button>
        </div>
      </div>
    `;
    const browse = list.querySelector(".vault-action-browse");
    if (browse) browse.addEventListener("click", () => openBrowseDialog());
  } catch (e) {
    list.innerHTML = `<div class="empty"><p>knowledge base error: ${escapeHtml(String(e))}</p></div>`;
  }
}

// ----- Data sources panel (v0.8.2) -----
//
// Mirrors the vaults panel. Each tile shows a known source mindframe can
// ingest from — connected sources first, then the catalog of "you could
// connect this." The connect/disconnect flow is intentionally minimal: a
// connect click pops a modal with the exact slash command to run in Claude
// Code, because OAuth/credential flows are agent-driven and shouldn't
// be re-implemented in the dashboard JS.

async function refreshSources() {
  try {
    const r = await fetch("/api/sources");
    const j = await r.json();
    const sources = j.sources || [];
    $("source-count").textContent = `${j.connected}/${j.total} connected`;
    const list = $("source-list");
    if (!sources.length) {
      list.innerHTML = `<div class="empty"><p>No data sources defined.</p></div>`;
      return;
    }
    // Connected first, then catalog.
    sources.sort((a, b) => (b.connected ? 1 : 0) - (a.connected ? 1 : 0));
    list.innerHTML = sources.map(s => {
      const statusBadge = s.connected
        ? `<span class="source-status source-connected">● connected</span>`
        : `<span class="source-status source-disconnected">○ not connected</span>`;
      const accountLine = s.account
        ? `<div class="source-account">${escapeHtml(s.account)}</div>`
        : "";
      const touched = s.credential_mtime
        ? `<span class="source-touched">creds: ${relativeTime(new Date(s.credential_mtime).getTime())}</span>`
        : "";
      const action = s.connected
        ? `<button class="btn btn-sm btn-default source-action-disconnect" type="button">disconnect</button>`
        : `<button class="btn btn-sm btn-primary source-action-connect" type="button">connect</button>`;
      return `
        <div class="source-tile ${s.connected ? 'source-tile-on' : 'source-tile-off'}" data-source="${escapeHtml(s.id)}">
          <div class="source-tile-header">
            <span class="source-name">${escapeHtml(s.name)}</span>
            ${statusBadge}
          </div>
          ${accountLine}
          <div class="source-tile-desc">${escapeHtml(s.description)}</div>
          <div class="source-tile-meta">${touched}</div>
          <div class="source-tile-actions">${action}</div>
        </div>
      `;
    }).join("");

    list.querySelectorAll(".source-action-connect").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const tile = e.target.closest(".source-tile");
        openConnectSourceDialog(tile.dataset.source);
      });
    });
    list.querySelectorAll(".source-action-disconnect").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        const tile = e.target.closest(".source-tile");
        const id = tile.dataset.source;
        if (!confirm(`Disconnect ${id}? This removes the stored credentials but does not revoke remote-side access.`)) return;
        e.target.disabled = true;
        e.target.textContent = "disconnecting…";
        try {
          const r = await fetch(`/api/sources/${encodeURIComponent(id)}/disconnect`, { method: "POST" });
          const j = await r.json();
          if (r.ok) {
            showToast(`disconnected ${id}`, "ok");
            refreshSources();
          } else {
            showToast(`disconnect failed: ${j.error || r.statusText}`, "err");
            e.target.disabled = false;
            e.target.textContent = "disconnect";
          }
        } catch (err) {
          showToast(`network error: ${err.message}`, "err");
        }
      });
    });
  } catch (e) {
    $("source-list").innerHTML = `<div class="empty"><p>source list error: ${escapeHtml(String(e))}</p></div>`;
  }
}

async function openConnectSourceDialog(sourceId) {
  const existing = document.getElementById("connect-dialog");
  if (existing) existing.remove();
  const dialog = document.createElement("div");
  dialog.id = "connect-dialog";
  dialog.className = "modal-overlay";
  dialog.innerHTML = `
    <div class="modal">
      <h3 class="modal-title">Connect ${escapeHtml(sourceId)}</h3>
      <div class="modal-form">
        <p class="modal-hint">Loading connect instructions…</p>
        <div class="modal-actions">
          <button type="button" class="btn btn-default" id="connect-close">close</button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(dialog);
  dialog.querySelector("#connect-close").addEventListener("click", () => dialog.remove());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.remove(); });
  try {
    const r = await fetch(`/api/sources/${encodeURIComponent(sourceId)}/connect`, { method: "POST" });
    const j = await r.json();
    const body = dialog.querySelector(".modal-form");
    if (r.ok) {
      body.innerHTML = `
        <p class="modal-hint">The agent-driven OAuth flow for <strong>${escapeHtml(j.source.name)}</strong> isn't shipped yet. For now, create this file manually:</p>
        <pre class="modal-codeblock">${escapeHtml(j.credential_path || '')}</pre>
        <p class="modal-hint">…with this shape:</p>
        <pre class="modal-codeblock">${escapeHtml(j.example_blob || '{}')}</pre>
        <p class="modal-hint">Then click <em>refresh</em>.</p>
        <div class="modal-actions">
          <button type="button" class="btn btn-default" id="connect-close">close</button>
          <button type="button" class="btn btn-primary" id="connect-refresh">refresh</button>
        </div>
      `;
      dialog.querySelector("#connect-close").addEventListener("click", () => dialog.remove());
      dialog.querySelector("#connect-refresh").addEventListener("click", () => {
        refreshSources();
        dialog.remove();
      });
    } else {
      body.innerHTML = `<p class="modal-hint">Error: ${escapeHtml(j.error || r.statusText)}</p>
        <div class="modal-actions">
          <button type="button" class="btn btn-default" id="connect-close">close</button>
        </div>`;
      dialog.querySelector("#connect-close").addEventListener("click", () => dialog.remove());
    }
  } catch (err) {
    dialog.querySelector(".modal-form").innerHTML = `<p class="modal-hint">Network error: ${escapeHtml(err.message)}</p>`;
  }
}

// Type → color palette for graph nodes. Stable per type so the same kind
// of entity always looks the same.
const TYPE_COLORS = [
  "#ffb86c", "#6fb1ff", "#c792ea", "#90ee90", "#ff79c6",
  "#8be9fd", "#f1fa8c", "#ff6e6e", "#bd93f9", "#50fa7b",
];
function colorForType(type, allTypes) {
  const idx = allTypes.indexOf(type);
  return TYPE_COLORS[(idx >= 0 ? idx : 0) % TYPE_COLORS.length];
}

// Lazy-load vis-network from CDN once; cache the promise.
let _visLoaderPromise = null;
function loadVisNetwork() {
  if (_visLoaderPromise) return _visLoaderPromise;
  _visLoaderPromise = new Promise((resolve, reject) => {
    if (window.vis && window.vis.Network) return resolve(window.vis);
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/vis-network/styles/vis-network.css";
    document.head.appendChild(css);
    const s = document.createElement("script");
    s.src = "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js";
    s.onload = () => resolve(window.vis);
    s.onerror = () => reject(new Error("failed to load vis-network from CDN"));
    document.head.appendChild(s);
  });
  return _visLoaderPromise;
}

async function openBrowseDialog() {
  const existing = document.getElementById("browse-dialog");
  if (existing) existing.remove();
  const dialog = document.createElement("div");
  dialog.id = "browse-dialog";
  dialog.className = "modal-overlay";
  dialog.innerHTML = `
    <div class="modal modal-graph">
      <div class="modal-graph-header">
        <h3 class="modal-title">Knowledge base graph</h3>
        <div class="modal-graph-meta" id="graph-meta">loading…</div>
        <button type="button" class="btn btn-default btn-sm" id="browse-close">close</button>
      </div>
      <div id="graph-legend" class="graph-legend"></div>
      <div id="graph-canvas" class="graph-canvas"></div>
      <div id="graph-detail" class="graph-detail">click a node to see details</div>
    </div>
  `;
  document.body.appendChild(dialog);
  dialog.querySelector("#browse-close").addEventListener("click", () => dialog.remove());
  // ESC to close
  const escHandler = (e) => { if (e.key === "Escape") { dialog.remove(); document.removeEventListener("keydown", escHandler); }};
  document.addEventListener("keydown", escHandler);

  try {
    const [g, vis] = await Promise.all([
      fetch("/api/vault/graph").then(r => r.json()),
      loadVisNetwork(),
    ]);

    if (g.error) {
      dialog.querySelector("#graph-canvas").innerHTML =
        `<p class="empty">graph error: ${escapeHtml(g.error)}</p>`;
      return;
    }

    const allTypes = (g.types || []).map(([t]) => t);
    $("graph-meta").textContent =
      `${g.node_count} nodes · ${g.edge_count} edges${g.truncated ? " (truncated)" : ""}`;

    // Legend
    const legend = $("graph-legend");
    legend.innerHTML = allTypes.map(t => {
      const count = (g.types.find(([type]) => type === t) || [])[1] || 0;
      return `<span class="graph-legend-item">
        <span class="graph-legend-swatch" style="background:${colorForType(t, allTypes)}"></span>
        ${escapeHtml(t)} (${count})
      </span>`;
    }).join("");

    if (!g.nodes.length) {
      $("graph-canvas").innerHTML =
        `<p class="empty">vault is empty.</p>`;
      return;
    }

    const nodes = new vis.DataSet(g.nodes.map(n => {
      const c = colorForType(n.type, allTypes);
      return {
        id: n.id,
        label: n.label,
        // No `group:` — vis assigns auto-colors per group that fight our
        // explicit color, leaving stray red/green/etc. dots that don't
        // appear in the legend. Full color object pins background +
        // border + highlight states so vis never falls back to defaults.
        color: { background: c, border: c, highlight: { background: c, border: "#ffd166" }, hover: { background: c, border: "#ffd166" } },
        title: `${n.type} · ${n.label}${n.dangling_count ? ` · ${n.dangling_count} dangling` : ""}`,
        font: { color: "#e8e8e8", size: 11, face: "JetBrains Mono, monospace" },
        borderWidth: 1.5,
        _meta: n,
      };
    }));
    const edges = new vis.DataSet(g.edges.map((e, i) => ({
      id: `e${i}`,
      from: e.source, to: e.target,
      color: { color: "rgba(232,232,232,0.18)", highlight: "#ffb86c" },
      width: 0.8,
      smooth: { type: "continuous" },
      arrows: { to: { enabled: true, scaleFactor: 0.4 } },
    })));

    const network = new vis.Network($("graph-canvas"), { nodes, edges }, {
      nodes: { shape: "dot", size: 10 },
      edges: { selectionWidth: 2 },
      interaction: { hover: true, dragNodes: true, zoomView: true, navigationButtons: false },
      physics: {
        solver: "forceAtlas2Based",
        forceAtlas2Based: { gravitationalConstant: -80, centralGravity: 0.005, springLength: 80, springConstant: 0.18, avoidOverlap: 0.6 },
        stabilization: { iterations: 200, fit: true },
      },
    });

    network.on("click", (params) => {
      if (params.nodes.length === 0) {
        $("graph-detail").innerHTML = "click a node to see details";
        return;
      }
      const nodeId = params.nodes[0];
      const node = g.nodes.find(n => n.id === nodeId);
      if (!node) return;
      const inboundEdges = g.edges.filter(e => e.target === nodeId);
      const outboundEdges = g.edges.filter(e => e.source === nodeId);
      $("graph-detail").innerHTML = `
        <div class="graph-detail-header">
          <span class="browse-type">${escapeHtml(node.type)}</span>
          <strong>${escapeHtml(node.label)}</strong>
        </div>
        <div class="graph-detail-meta">
          <span>id: <code>${escapeHtml(node.id)}</code></span>
          <span>→ ${outboundEdges.length} outbound</span>
          <span>← ${inboundEdges.length} inbound</span>
          ${node.dangling_count ? `<span class="graph-dangling">${node.dangling_count} dangling links</span>` : ""}
        </div>
        ${outboundEdges.length ? `
          <div class="graph-detail-section">
            <p class="graph-detail-label">links to:</p>
            <ul class="graph-detail-list">
              ${outboundEdges.slice(0, 10).map(e => `<li>→ ${escapeHtml(e.target)}</li>`).join("")}
              ${outboundEdges.length > 10 ? `<li>… ${outboundEdges.length - 10} more</li>` : ""}
            </ul>
          </div>
        ` : ""}
        ${inboundEdges.length ? `
          <div class="graph-detail-section">
            <p class="graph-detail-label">linked from:</p>
            <ul class="graph-detail-list">
              ${inboundEdges.slice(0, 10).map(e => `<li>← ${escapeHtml(e.source)}</li>`).join("")}
              ${inboundEdges.length > 10 ? `<li>… ${inboundEdges.length - 10} more</li>` : ""}
            </ul>
          </div>
        ` : ""}
      `;
    });

    network.on("stabilizationIterationsDone", () => {
      network.setOptions({ physics: { enabled: false } });
    });
  } catch (e) {
    dialog.querySelector("#graph-canvas").innerHTML =
      `<p class="empty">graph error: ${escapeHtml(String(e))}</p>`;
  }
}


async function renderBoardsIndex() {
  root().innerHTML = `
    <div class="index-wrap">
      <section class="home-chat">
        <p class="home-eyebrow">mindframe</p>
        <h1 class="home-headline">What should I look into?</h1>
        <form id="create-form" class="chat-form" autocomplete="off">
          <textarea
            id="create-input"
            class="chat-input"
            rows="3"
            placeholder="e.g. give me a live overview of this machine, or review the open PRs on my main repo and flag anything risky."
          ></textarea>
          <div class="chat-form-row">
            <span class="chat-hint">⌘/Ctrl + Enter to create</span>
            <button type="submit" class="btn btn-primary chat-submit">Create mindframe</button>
          </div>
        </form>
        <p class="home-sub">A mindframe is an agent that works for you on a live page it composes. Or see the whole bundle on the <a href="/system">System overview</a>.</p>
      </section>

      <section class="frame-section">
        <div class="index-header">
          <h2>Your mindframes</h2>
          <span id="frame-count" class="count">…</span>
        </div>
        <div id="frame-list" class="frame-list">
          <div class="loading">loading…</div>
        </div>
      </section>

      <section class="vaults-section">
        <div class="index-header">
          <h2>Your knowledge base</h2>
          <span id="vault-count" class="count">…</span>
        </div>
        <div id="vault-list" class="vault-list">
          <div class="loading">loading knowledge base…</div>
        </div>
      </section>

      <section class="sources-section">
        <div class="index-header">
          <h2>Data sources</h2>
          <span id="source-count" class="count">…</span>
        </div>
        <div id="source-list" class="source-list">
          <div class="loading">loading sources…</div>
        </div>
      </section>
    </div>
  `;

  refreshVault();
  refreshSources();
  refreshHomeFrames();
  setConn("ok", "ready");

  const form = $("create-form");
  const input = $("create-input");
  form.addEventListener("submit", (e) => { e.preventDefault(); createMindframe(input.value); });
  input.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); createMindframe(input.value); }
  });
}

async function refreshHomeFrames() {
  try {
    const j = await (await fetch("/api/frames")).json();
    const frames = j.frames || [];
    $("frame-count").textContent = frames.length;
    const list = $("frame-list");
    if (!frames.length) {
      list.innerHTML = `<div class="empty"><p>No mindframes yet — describe one above and I'll spin up an agent for it.</p></div>`;
      return;
    }
    list.innerHTML = frames.map(f => `
      <a class="frame-row" href="/m/${encodeURIComponent(f.id)}">
        <span class="frame-marker frame-marker-${escapeHtml(f.status)}"></span>
        <span class="frame-title-wrap">
          <span class="frame-title">${escapeHtml(f.title)}</span>
          <span class="frame-sub"><span class="mono">${escapeHtml(f.id)}</span></span>
        </span>
        <span class="frame-meta"><span class="frame-time">${relativeTime(f.modified)}</span></span>
        <span class="frame-open">→</span>
      </a>`).join("");
  } catch (e) {
    $("frame-list").innerHTML = `<div class="empty"><p>couldn't load mindframes: ${escapeHtml(String(e))}</p></div>`;
  }
}

async function createMindframe(text) {
  text = (text || "").trim();
  if (!text) { showToast("describe what the mindframe should do", "warn"); return; }
  const btn = document.querySelector("#create-form .chat-submit");
  if (btn) { btn.disabled = true; btn.textContent = "Spawning agent…"; }
  try {
    const r = await fetch("/api/frames/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text }),
    });
    const j = await r.json();
    if (!r.ok) {
      showToast(`couldn't create mindframe: ${j.error || r.statusText}`, "err");
      if (btn) { btn.disabled = false; btn.textContent = "Create mindframe"; }
      return;
    }
    if (j.spawn !== "ok") {
      showToast(`frame created, but the agent didn't spawn: ${j.spawn_result?.error || "see logs"}`, "warn");
    }
    location.href = j.url;   // open the surface shell at /m/<id>
  } catch (e) {
    showToast(`network error: ${e.message}`, "err");
    if (btn) { btn.disabled = false; btn.textContent = "Create mindframe"; }
  }
}

// ----- Health probe -----

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

// ----- System overview — structured map of the whole bundle -----

const sysEmpty = (msg) => `<div class="sys-empty">${escapeHtml(msg)}</div>`;
const sysErr = (e) => `<div class="sys-empty sys-empty-err">error: ${escapeHtml(String(e))}</div>`;

const stateDot = (state) => {
  const s = state === "connected" ? "ok"
    : state === "needs-auth" ? "warn"
    : state === "unknown" ? "faint" : "faint";
  return `<span class="sys-dot sys-dot-${s}" title="${escapeHtml(state)}"></span>`;
};

const statusBadge = (status) => {
  const map = { running: "ok", completed: "ok", pending: "warn",
    crashed: "err", killed: "faint" };
  const s = map[status] || "faint";
  return `<span class="sys-badge sys-badge-${s}">${escapeHtml(status)}</span>`;
};

async function fillEvents() {
  try {
    const j = await (await fetch("/api/events")).json();
    $("sys-events-count").textContent = j.route_count;
    const body = $("sys-events-body");
    if (!j.dispatcher_present) {
      body.innerHTML = sysEmpty("dispatcher not configured — no event routes.");
      return;
    }
    if (!j.sources.length) {
      body.innerHTML = sysEmpty("no routes yet. Add one with /dispatcher:route.");
      return;
    }
    body.innerHTML = j.sources.map(s => `
      <div class="sys-group">
        <div class="sys-group-head">${escapeHtml(s.source)}</div>
        ${s.routes.map(rt => `
          <div class="sys-row">
            <span class="sys-row-main">${escapeHtml(rt.event_type)}</span>
            <span class="sys-row-sub">
              <span class="sys-tgt sys-tgt-${escapeHtml(rt.target_kind)}">${escapeHtml(rt.target_kind)}</span>
              <span class="mono">${escapeHtml(rt.target_name)}</span>
            </span>
          </div>`).join("")}
      </div>`).join("");
  } catch (e) { $("sys-events-body").innerHTML = sysErr(e); }
}

const parseTs = (s) => {
  if (!s) return Date.now();
  const t = new Date(s.replace(" ", "T") + (s.includes("Z") ? "" : "Z")).getTime();
  return Number.isFinite(t) ? t : Date.now();
};

async function fillAgents() {
  try {
    const j = await (await fetch("/api/agents")).json();
    $("sys-agents-count").textContent = `${j.running_count} live · ${j.definition_count} def`;
    const defs = (j.definitions || []).map(d => `
      <div class="sys-row sys-row-stack">
        <span class="sys-row-main">${escapeHtml(d.name)}
          <span class="sys-tag">${escapeHtml(d.kind)}${d.model ? " · " + escapeHtml(d.model) : ""}</span>
        </span>
        <span class="sys-trigger-line">${(d.triggered_by || []).map(t =>
          `<span class="sys-chip">↯ ${escapeHtml(t)}</span>`).join("") || '<span class="sys-faint">manual trigger</span>'}</span>
      </div>`).join("") || sysEmpty("no recipes installed.");
    const live = (j.live || []).map(a => `
      <div class="sys-row">
        <span class="sys-row-main">${a.live ? '<span class="sys-dot sys-dot-ok" title="tmux session live"></span>' : ""}${escapeHtml(a.name)}</span>
        <span class="sys-row-sub">${statusBadge(a.status)}<span class="sys-faint">${relativeTime(parseTs(a.updated_at))}</span></span>
      </div>`).join("") || sysEmpty("nothing running right now.");
    $("sys-agents-body").innerHTML = `
      <div class="sys-subhead">Definitions <span>${j.definition_count}</span></div>
      ${defs}
      <div class="sys-subhead">Live tasks <span>${j.running_count} live · ${j.live_count} shown</span></div>
      ${live}`;
  } catch (e) { $("sys-agents-body").innerHTML = sysErr(e); }
}

async function fillMindframes() {
  try {
    const j = await (await fetch("/api/frames")).json();
    const frames = j.frames || [];
    $("sys-frames-count").textContent = frames.length;
    const body = $("sys-frames-body");
    if (!frames.length) { body.innerHTML = sysEmpty("no surface mindframes yet."); return; }
    body.innerHTML = frames.map(f => `
      <a class="sys-row sys-row-link" href="/m/${encodeURIComponent(f.id)}">
        <span class="sys-row-main"><span class="frame-marker frame-marker-${escapeHtml(f.status)}"></span>${escapeHtml(f.title)}</span>
        <span class="sys-row-sub"><span class="sys-faint">${relativeTime(f.modified)}</span><span class="sys-open">→</span></span>
      </a>`).join("");
  } catch (e) { $("sys-frames-body").innerHTML = sysErr(e); }
}

async function fillCapabilities() {
  try {
    const j = await (await fetch("/api/capabilities")).json();
    $("sys-caps-count").textContent = `${j.mcp_count} MCPs · ${j.skill_count} skills`;
    const mcps = (j.mcps || []).map(m => `
      <div class="sys-row">
        <span class="sys-row-main">${stateDot(m.state)}${escapeHtml(m.name)}${m.bundle ? '<span class="sys-tag sys-tag-faint">bundle</span>' : ""}</span>
      </div>`).join("") || sysEmpty("no MCPs connected.");
    const skills = (j.skills || []).map(p => `
      <div class="sys-group">
        <div class="sys-group-head">${escapeHtml(p.plugin)} <span class="sys-faint">${escapeHtml(p.version)}</span></div>
        <div class="sys-skill-chips">${p.skills.map(s =>
          `<span class="sys-chip" title="${escapeHtml(s.description)}">${escapeHtml(s.name)}</span>`).join("")}</div>
      </div>`).join("") || sysEmpty("no plugin skills found.");
    $("sys-caps-body").innerHTML = `
      <div class="sys-subhead">MCPs <span>${j.mcp_count}</span></div>
      ${mcps}
      <div class="sys-subhead">Skills <span>${j.skill_count}</span></div>
      ${skills}`;
  } catch (e) { $("sys-caps-body").innerHTML = sysErr(e); }
}

async function fillKnowledge() {
  try {
    const r = await fetch("/api/vault");
    const v = await r.json();
    const body = $("sys-kb-body");
    if (!r.ok || v.error || !v.exists) {
      $("sys-kb-count").textContent = "—";
      body.innerHTML = sysEmpty("no knowledge base. Run /mindframe:setup.");
      return;
    }
    $("sys-kb-count").textContent = v.total_entries;
    const types = Object.entries(v.entry_counts || {})
      .sort((a, b) => b[1] - a[1]).slice(0, 4)
      .map(([t, n]) => `<span class="sys-chip">${escapeHtml(t)}: ${n}</span>`).join("");
    body.innerHTML = `
      <div class="sys-group">
        <div class="sys-group-head">${escapeHtml(v.name)}
          ${v.remote ? '<span class="sys-tag sys-tag-faint">⇄ remote</span>' : ""}
        </div>
        <div class="sys-row-sub"><span class="sys-faint">${v.total_entries} entries · ${escapeHtml(vaultLastTouched(v))}</span></div>
        <div class="sys-skill-chips">${types || '<span class="sys-faint">empty</span>'}</div>
      </div>`;
  } catch (e) { $("sys-kb-body").innerHTML = sysErr(e); }
}

async function renderSystem() {
  root().innerHTML = `
    <div class="system-wrap">
      <div class="system-head">
        <a class="back" href="/">← home</a>
        <h1 class="system-title">System overview</h1>
        <p class="system-sub">the live shape of your mindframe bundle</p>
      </div>
      <div class="sys-grid">
        <section class="sys-card">
          <div class="sys-card-head"><h2>Event sources</h2><span id="sys-events-count" class="count">…</span></div>
          <div id="sys-events-body" class="sys-card-body"><div class="loading">loading…</div></div>
        </section>
        <section class="sys-card">
          <div class="sys-card-head"><h2>Agents</h2><span id="sys-agents-count" class="count">…</span></div>
          <div id="sys-agents-body" class="sys-card-body"><div class="loading">loading…</div></div>
        </section>
        <section class="sys-card">
          <div class="sys-card-head"><h2>Mindframes</h2><span id="sys-frames-count" class="count">…</span></div>
          <div id="sys-frames-body" class="sys-card-body"><div class="loading">loading…</div></div>
        </section>
        <section class="sys-card">
          <div class="sys-card-head"><h2>Skills + MCPs</h2><span id="sys-caps-count" class="count">…</span></div>
          <div id="sys-caps-body" class="sys-card-body"><div class="loading">loading…</div></div>
        </section>
        <section class="sys-card">
          <div class="sys-card-head"><h2>Knowledge base</h2><span id="sys-kb-count" class="count">…</span></div>
          <div id="sys-kb-body" class="sys-card-body"><div class="loading">loading…</div></div>
        </section>
      </div>
    </div>`;

  const refreshAll = () => {
    fillEvents(); fillAgents(); fillMindframes(); fillCapabilities(); fillKnowledge();
    setConn("ok", "system overview");
  };
  refreshAll();
  return setInterval(refreshAll, POLL_INTERVAL_MS);
}

// ----- Router -----

function route() {
  const path = location.pathname;
  if (path === "/system" || path === "/system/") {
    renderSystem();
  } else {
    renderBoardsIndex();
  }
}

pollHealth();
setInterval(pollHealth, HEALTH_POLL_MS);
route();
