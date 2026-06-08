// Mindframe dashboard SPA.
//
// One view:
//   /        → home: a hub graph — a central "New" node ringed by satellites
//              (Mindframes, Knowledge base, Agents, Connections, Events).
//              A satellite opens a drawer; the center spawns a launchpad
//              mindframe (KB-grounded suggestions) in a new tab. Reached via
//              /mindframe:open ("open up mindframe"). The old /system overview
//              was deprecated 2026-06-08 — the drawers replaced its panels.
//
// A mindframe is a surface (the agent owns one index.html it rewrites). Block-
// stream rendering was removed 2026-06-04; per-mindframe viewing is rebuilt on
// the surface model in a later migration step.

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
  if (!v.last_modified) return "no notes yet";
  return relativeTime(new Date(v.last_modified).getTime());
}

// ----- Drawer renderers -----
//
// Each hub satellite opens a drawer over the graph; these fill its body from the
// the read-only system APIs (/api/events, /api/agents, /api/connections, …).
// They reuse the sys-* row markup and helpers stateDot / statusBadge / parseTs.

async function drawerMindframes(body) {
  try {
    const j = await (await fetch("/api/frames")).json();
    const frames = j.frames || [];
    const head = `<div class="drawer-actions"><button class="btn btn-primary btn-sm" id="drawer-new">+ new mindframe</button></div>`;
    if (!frames.length) {
      body.innerHTML = head + `<div class="empty"><p>No mindframes yet — create one to spin up an agent on a live page.</p></div>`;
    } else {
      body.innerHTML = head + `<div class="frame-list">` + frames.map(f => `
        <a class="frame-row" href="/m/${encodeURIComponent(f.id)}">
          <span class="frame-marker frame-marker-${escapeHtml(f.status)}"></span>
          <span class="frame-title-wrap">
            <span class="frame-title">${escapeHtml(f.title)}</span>
            <span class="frame-sub"><span class="mono">${escapeHtml(f.id)}</span></span>
          </span>
          <span class="frame-meta"><span class="frame-time">${relativeTime(f.modified)}</span></span>
          <span class="frame-open">→</span>
        </a>`).join("") + `</div>`;
    }
    const nb = $("drawer-new");
    if (nb) nb.addEventListener("click", openCreateOverlay);
  } catch (e) {
    body.innerHTML = `<div class="empty"><p>couldn't load mindframes: ${escapeHtml(String(e))}</p></div>`;
  }
}

async function drawerKnowledge(body) {
  try {
    const r = await fetch("/api/vault");
    const v = await r.json();
    if (!r.ok || v.error || !v.exists) {
      body.innerHTML = `<div class="empty"><p>No knowledge base yet.
        Run <code>/mindframe:setup</code> to create one at <code>~/.mindframe/vault</code>.</p></div>`;
      return;
    }
    const types = Object.entries(v.entry_counts || {})
      .sort((a, b) => b[1] - a[1])
      .map(([t, n]) => `<span class="vault-type-chip">${escapeHtml(t)}: ${n}</span>`)
      .join("");
    body.innerHTML = `
      <div class="drawer-actions"><button class="btn btn-primary btn-sm" id="drawer-graph">open graph</button></div>
      <div class="kb-summary">
        <div class="kb-big">${v.total_entries}<span>entries</span></div>
        <div class="kb-touch">${escapeHtml(v.name)} · last touched ${vaultLastTouched(v)}</div>
      </div>
      <div class="vault-type-chips">${types || '<span class="vault-empty-note">empty</span>'}</div>`;
    $("drawer-graph").addEventListener("click", openBrowseDialog);
  } catch (e) {
    body.innerHTML = `<div class="empty"><p>knowledge base error: ${escapeHtml(String(e))}</p></div>`;
  }
}

async function drawerAgents(body) {
  try {
    const j = await (await fetch("/api/agents")).json();
    const defs = (j.definitions || []).map(d => `
      <div class="sys-row sys-row-stack">
        <span class="sys-row-main">${escapeHtml(d.name)}
          <span class="sys-tag">${escapeHtml(d.kind)}${d.model ? " · " + escapeHtml(d.model) : ""}</span></span>
        <span class="sys-trigger-line">${(d.triggered_by || []).map(t =>
          `<span class="sys-chip">↯ ${escapeHtml(t)}</span>`).join("") || '<span class="sys-faint">manual trigger</span>'}</span>
      </div>`).join("") || `<div class="sys-empty">no recipes installed.</div>`;
    const live = (j.live || []).map(a => `
      <div class="sys-row">
        <span class="sys-row-main">${a.live ? '<span class="sys-dot sys-dot-ok"></span>' : ""}${escapeHtml(a.name)}</span>
        <span class="sys-row-sub">${statusBadge(a.status)}<span class="sys-faint">${relativeTime(parseTs(a.updated_at))}</span></span>
      </div>`).join("") || `<div class="sys-empty">nothing running right now.</div>`;
    body.innerHTML = `
      <div class="sys-subhead">Definitions <span>${j.definition_count}</span></div>${defs}
      <div class="sys-subhead">Live <span>${j.running_count} live · ${j.live_count} shown</span></div>${live}`;
  } catch (e) {
    body.innerHTML = `<div class="empty"><p>agents error: ${escapeHtml(String(e))}</p></div>`;
  }
}

async function drawerConnections(body) {
  try {
    const j = await (await fetch("/api/connections")).json();
    const conns = j.connections || [];
    if (!conns.length) {
      body.innerHTML = `<div class="empty"><p>No connections discovered. Authenticate a CLI
        (<code>gh</code>, <code>gcloud</code>, <code>aws</code>) or connect an MCP, then reopen.</p></div>`;
      return;
    }
    body.innerHTML = `<div class="sys-subhead">Reachable <span>${j.reachable}/${conns.length}</span></div>` +
      conns.map(c => `
        <div class="sys-row">
          <span class="sys-row-main">${stateDot(c.state)}${escapeHtml(c.name)}<span class="sys-tag sys-tag-faint">${escapeHtml(c.kind)}</span></span>
          <span class="sys-row-sub">${c.account ? `<span class="sys-faint">${escapeHtml(c.account)}</span>` : ""}</span>
        </div>`).join("");
  } catch (e) {
    body.innerHTML = `<div class="empty"><p>connections error: ${escapeHtml(String(e))}</p></div>`;
  }
}

async function drawerEvents(body) {
  try {
    const j = await (await fetch("/api/events")).json();
    if (!j.dispatcher_present) {
      body.innerHTML = `<div class="empty"><p>dispatcher not configured — no event routes.</p></div>`;
      return;
    }
    if (!j.sources.length) {
      body.innerHTML = `<div class="empty"><p>no routes yet. Add one with <code>/dispatcher:route</code>.</p></div>`;
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
              <span class="mono">${escapeHtml(rt.target_name)}</span></span>
          </div>`).join("")}
      </div>`).join("");
  } catch (e) {
    body.innerHTML = `<div class="empty"><p>events error: ${escapeHtml(String(e))}</p></div>`;
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


// ===== Home: the mindframe hub graph =====
//
// The home is a node graph: a central "New" node ringed by satellites —
// Mindframes, Knowledge base, Agents, Connections, Events. Clicking a
// satellite opens a drawer over the graph; clicking the center spawns a
// launchpad mindframe — an agent that surveys the
// vault + connections and opens, in a new tab, a page of grounded suggestions
// (add a watch, create an agent, start a working mindframe), each a button that
// messages the agent to pursue it. Edges live in an SVG layer painted behind
// the nodes; layout is recomputed on resize.

const HUB_NODES = [
  { key: "mindframes",  label: "Mindframes",     hint: "live agent surfaces", render: drawerMindframes },
  { key: "knowledge",   label: "Knowledge base", hint: "what you know",       render: drawerKnowledge },
  { key: "agents",      label: "Agents",         hint: "what can run",        render: drawerAgents },
  { key: "connections", label: "Connections",    hint: "reachable sources",   render: drawerConnections },
  { key: "events",      label: "Events",         hint: "wired routes",        render: drawerEvents },
];

function renderHome() {
  root().innerHTML = `
    <div class="hub" id="hub">
      <svg class="hub-edges" id="hub-edges" aria-hidden="true"></svg>
      <div class="hub-nodes" id="hub-nodes"></div>
      <p class="hub-tagline">Open a node, or start something new.</p>
    </div>
    <aside class="drawer" id="drawer" aria-hidden="true">
      <div class="drawer-head">
        <h2 class="drawer-title" id="drawer-title">—</h2>
        <button class="drawer-close" id="drawer-close" type="button" aria-label="close">✕</button>
      </div>
      <div class="drawer-body" id="drawer-body"></div>
    </aside>
    <div class="drawer-scrim" id="drawer-scrim" hidden></div>
  `;

  buildHubNodes();
  layoutHub();
  window.addEventListener("resize", layoutHub);
  setConn("ok", "ready");
  loadHubCounts();

  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer-scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeDrawer(); closeCreateOverlay(); }
  });
}

function buildHubNodes() {
  const wrap = $("hub-nodes");
  const center = el("button", { class: "hub-node hub-center", id: "hub-center", type: "button" }, [
    el("span", { class: "hub-center-plus" }, "+"),
    el("span", { class: "hub-node-label" }, "New"),
    el("span", { class: "hub-node-hint" }, "where to start"),
  ]);
  center.addEventListener("click", startLaunchpad);
  wrap.appendChild(center);

  for (const n of HUB_NODES) {
    const node = el("button", { class: "hub-node hub-sat", type: "button", "data-key": n.key }, [
      el("span", { class: "hub-node-label" }, n.label),
      // Navigate-only nodes (href) have no live count — skip the placeholder dot.
      el("span", { class: "hub-node-count", id: `hub-count-${n.key}` }, n.href ? "" : "·"),
      el("span", { class: "hub-node-hint" }, n.hint),
    ]);
    node.addEventListener("click", () => {
      if (n.href) { location.href = n.href; return; }
      openDrawer(n);
    });
    wrap.appendChild(node);
  }
}

function layoutHub() {
  const hub = $("hub");
  if (!hub) return;
  const rect = hub.getBoundingClientRect();
  const cx = rect.width / 2;
  const cy = rect.height / 2;

  const center = $("hub-center");
  if (center) { center.style.left = `${cx}px`; center.style.top = `${cy}px`; }

  const sats = [...document.querySelectorAll(".hub-sat")];
  const count = sats.length || 1;
  const radius = Math.max(150, Math.min(rect.width * 0.40, rect.height * 0.38));

  const svg = $("hub-edges");
  svg.setAttribute("viewBox", `0 0 ${rect.width} ${rect.height}`);
  let edges = "";
  sats.forEach((node, i) => {
    const ang = -Math.PI / 2 + (i * 2 * Math.PI) / count; // start at top, go clockwise
    const x = cx + radius * Math.cos(ang);
    const y = cy + radius * Math.sin(ang);
    node.style.left = `${x}px`;
    node.style.top = `${y}px`;
    edges += `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" class="hub-edge" data-key="${node.dataset.key}"/>`;
  });
  svg.innerHTML = edges;
}

async function loadHubCounts() {
  const set = (key, val) => { const e = $(`hub-count-${key}`); if (e) e.textContent = val; };
  const j = (r) => r.ok ? r.json() : Promise.reject(r.status);
  fetch("/api/frames").then(j).then(d => set("mindframes", (d.frames || []).length)).catch(() => {});
  fetch("/api/vault").then(j).then(d => set("knowledge", d.exists ? d.total_entries : 0)).catch(() => {});
  fetch("/api/agents").then(j).then(d => set("agents", `${d.running_count || 0}/${d.definition_count || 0}`)).catch(() => {});
  fetch("/api/connections").then(j).then(d => set("connections", `${d.reachable || 0}/${(d.connections || []).length}`)).catch(() => {});
  fetch("/api/events").then(j).then(d => set("events", d.route_count || 0)).catch(() => {});
}

// ----- Drawer open / close -----

function openDrawer(node) {
  $("drawer-title").textContent = node.label;
  const body = $("drawer-body");
  body.innerHTML = `<div class="loading">loading…</div>`;
  $("drawer").classList.add("open");
  $("drawer").setAttribute("aria-hidden", "false");
  $("drawer-scrim").hidden = false;
  document.querySelectorAll(".hub-sat").forEach(s =>
    s.classList.toggle("active", s.dataset.key === node.key));
  document.querySelectorAll(".hub-edge").forEach(e =>
    e.classList.toggle("active", e.dataset.key === node.key));
  node.render(body);
}

function closeDrawer() {
  const d = $("drawer");
  if (!d) return;
  d.classList.remove("open");
  d.setAttribute("aria-hidden", "true");
  $("drawer-scrim").hidden = true;
  document.querySelectorAll(".hub-sat.active, .hub-edge.active")
    .forEach(e => e.classList.remove("active"));
}

// ----- Create overlay (the center node) -----

function openCreateOverlay() {
  if ($("create-overlay")) return;
  const ov = el("div", { class: "create-overlay", id: "create-overlay" });
  ov.innerHTML = `
    <div class="create-card">
      <p class="home-eyebrow">new mindframe</p>
      <h2 class="create-headline">What should I look into?</h2>
      <form id="create-form" autocomplete="off">
        <textarea id="create-input" class="chat-input" rows="3"
          placeholder="e.g. give me a live overview of this machine, or review the open PRs on my main repo and flag anything risky."></textarea>
        <div class="chat-form-row">
          <span class="chat-hint">⌘/Ctrl + Enter to create · Esc to cancel</span>
          <button type="submit" class="btn btn-primary chat-submit">Create mindframe</button>
        </div>
      </form>
      <p class="home-sub">A mindframe is an agent that works for you on a live page it composes.</p>
    </div>`;
  document.body.appendChild(ov);
  ov.addEventListener("click", (e) => { if (e.target === ov) closeCreateOverlay(); });

  const input = $("create-input");
  input.focus();
  $("create-form").addEventListener("submit", (e) => { e.preventDefault(); createMindframe(input.value); });
  input.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); createMindframe(input.value); }
  });
}

function closeCreateOverlay() {
  const o = $("create-overlay");
  if (o) o.remove();
}

// ----- Launchpad (the center "New" node) -----
//
// Clicking "New" spawns a launchpad mindframe and opens it in a new tab. The
// launchpad agent surveys the operator's knowledge base, connections, and
// already-wired watches, then composes a page of concrete, grounded suggestions
// — add an event source, create an agent, or start a working mindframe — each a
// button that messages the same agent to pursue it. The whole brief rides in the
// spawn prompt; the generic surface brief (server-side) wraps it.

const LAUNCHPAD_PROMPT = `You are the LAUNCHPAD — the operator just clicked "New" on the mindframe home and you opened in a fresh tab. Your job is to help them choose what to do next, grounded in what you actually know about them. Do NOT ask an open-ended "what do you want?" — survey their world first, then offer concrete, specific suggestions.

STEP 1 — Survey (do real work, never guess):
- Knowledge base: list ~/.mindframe/vault and read enough to know the entity types, rough counts, and a few notable nodes (real repos, people, projects, incidents, decisions).
- Connections: run "claude mcp list"; check "gh auth status", and "gcloud auth list" / "aws sts get-caller-identity" if those CLIs exist. Note what is actually reachable.
- Already wired (so you do not suggest duplicates): read ~/.dispatcher/channels.yaml (existing routes) and list ~/.dispatcher/recipes (existing agents).
- You may also GET {origin}/api/vault, {origin}/api/connections, {origin}/api/events, and {origin}/api/agents for a fast aggregated view of the same facts.

STEP 2 — Compose ONE page (the whole index.html):
- Open with a short, warm orientation line that names real things you found (their connected tools, their repos) — proof you actually looked, not a template.
- Then 3 to 6 suggestions as buttons, spanning these kinds. Ground EVERY one in a real fact; omit a kind if you cannot ground it. No generic placeholders.
  1. Add an event source (a "watch"): a connected tool that is not yet wired as a route. e.g. "Add GitHub pull requests as an event source so I get prepped when one opens."
  2. Create an agent (a recipe that runs on a trigger): e.g. if Google Calendar is connected and no meeting-prep recipe exists, "Create an agent that preps me before each calendar meeting."
  3. Start a working mindframe (real work now), grounded in the KB: e.g. "Review the open PRs on <a real repo> and flag anything risky", or "Summarize my last few incidents and what we changed."
- Group the suggestions clearly by kind. Keep copy in the operator's second person ("you"/"your"). Inline all CSS, calm and legible, no emoji.

STEP 3 — Make each suggestion a button that messages THIS mindframe so you pursue it. Use EXACTLY this pattern so the frame id is automatic (the page is served at /api/frame/<id>/page; swapping /page for /message hits this frame's message endpoint):

  <button onclick="fetch(location.pathname.replace('/page','/message'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'PUT THE ACTIVITY HERE, phrased as a clear instruction to you'})}).then(function(r){this.disabled=true;this.textContent='on it…'}.bind(this)).catch(function(){this.textContent='failed — use the message box below'}.bind(this))">Button label</button>

When the operator clicks one, you will receive its text as a message. Then actually pursue that activity on this same page: research and draft it, and for anything irreversible or outward-facing (creating a route, spawning an agent, sending anything) draw it as a pending action and wait for the operator to confirm in a message before doing it.

Never declare yourself done. The suggestions stand and the message box is always open for "or just tell me what you want to do." Compose your launchpad index.html now.`;

async function startLaunchpad() {
  // Open the tab synchronously (inside the click) so the popup isn't blocked,
  // then redirect it once the frame id comes back from the spawn.
  const tab = window.open("", "_blank");
  if (tab) {
    tab.document.write(
      "<!doctype html><meta charset=utf-8><title>composing…</title>" +
      "<body style='margin:0;height:100vh;display:grid;place-items:center;" +
      "font:16px system-ui;color:#8A8580;background:#0D0D0D'>" +
      "<div style='text-align:center'>Composing your launchpad…<br>" +
      "<small style='color:#5A5650'>surveying your knowledge base and connections</small></div>");
  }
  showToast("Spawning your launchpad…", "info");
  try {
    const r = await fetch("/api/frames/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: LAUNCHPAD_PROMPT.replace(/\{origin\}/g, location.origin),
        title: "Where to start",
      }),
    });
    const j = await r.json();
    if (!r.ok) {
      showToast(`couldn't open a launchpad: ${j.error || r.statusText}`, "err");
      if (tab) tab.close();
      return;
    }
    if (j.spawn !== "ok") {
      showToast(`launchpad created, but the agent didn't spawn: ${j.spawn_result?.error || "see logs"}`, "warn");
    }
    if (tab) tab.location = j.url;     // redirect the opened tab to /m/<id>
    else location.href = j.url;        // popup blocked — fall back to same tab
    loadHubCounts();                   // the new mindframe bumps the Mindframes count
  } catch (e) {
    showToast(`network error: ${e.message}`, "err");
    if (tab) tab.close();
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

// ----- shared row helpers (used by the satellite drawers) -----
//
// stateDot / statusBadge / parseTs back the Agents, Connections, and Events
// drawers. The standalone /system overview was deprecated 2026-06-08 — the
// hub's drawers replaced every panel it had.

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

const parseTs = (s) => {
  if (!s) return Date.now();
  const t = new Date(s.replace(" ", "T") + (s.includes("Z") ? "" : "Z")).getTime();
  return Number.isFinite(t) ? t : Date.now();
};

// ----- Router -----

function route() {
  // /system was deprecated 2026-06-08 (the hub's drawers replaced it). Normalize
  // any lingering /system link back to the home hub.
  if (location.pathname.startsWith("/system")) {
    history.replaceState(null, "", "/");
  }
  renderHome();
}

pollHealth();
setInterval(pollHealth, HEALTH_POLL_MS);
route();
