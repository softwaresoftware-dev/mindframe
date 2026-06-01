// Mindframe dashboard SPA — boards-index + per-mindframe block-stream renderer.
//
// Two views:
//   /        → boards index (polled from /api/frames)
//   /m/<id>  → one mindframe, blocks streamed live via SSE (EventSource)

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

// ----- Block renderers: one function per type, returns an HTMLElement -----

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

const RENDERERS = {
  text(b) {
    return el("div", { class: "block block-text" }, [
      el("div", { class: "block-body md", html: renderMarkdown(b.markdown) }),
    ]);
  },

  code(b) {
    const pre = el("pre", { class: "block block-code" });
    const code = el("code", { class: `lang-${escapeHtml(b.lang || "text")}` });
    code.textContent = b.content ?? "";
    pre.appendChild(code);
    if (b.lang) {
      pre.appendChild(el("span", { class: "code-lang" }, b.lang));
    }
    return pre;
  },

  image(b) {
    const src = b.src || "";
    const img = el("img", { class: "block-image-img", src, alt: b.alt || "" });
    return el("figure", { class: "block block-image" }, [
      img,
      b.caption ? el("figcaption", {}, b.caption) : null,
    ]);
  },

  "url-card"(b) {
    const a = el("a", { class: "block block-urlcard", href: b.url, rel: "noopener", target: "_blank" });
    if (b.favicon) a.appendChild(el("img", { class: "uc-favicon", src: b.favicon, alt: "" }));
    a.appendChild(el("div", { class: "uc-body" }, [
      el("div", { class: "uc-title" }, b.title || b.url),
      b.summary ? el("div", { class: "uc-summary" }, b.summary) : null,
      el("div", { class: "uc-url" }, b.url),
    ]));
    return a;
  },

  table(b) {
    const t = el("table", { class: "block block-table" });
    if (Array.isArray(b.headers)) {
      const thead = el("thead", {}, el("tr", {}, b.headers.map(h => el("th", {}, String(h)))));
      t.appendChild(thead);
    }
    const tbody = el("tbody");
    for (const row of (b.rows || [])) {
      tbody.appendChild(el("tr", {}, (row || []).map(c => el("td", {}, String(c)))));
    }
    t.appendChild(tbody);
    return t;
  },

  "button-row"(b) {
    const row = el("div", { class: "block block-btnrow" });
    for (const btn of (b.buttons || [])) {
      const style = btn.style || "default";
      row.appendChild(el("button", {
        class: `btn btn-${style}`,
        type: "button",
        onClick: () => window.mindframe.postEvent(btn.event_type, btn.data || {}),
      }, btn.label || btn.event_type));
    }
    return row;
  },

  input(b) {
    const wrap = el("form", { class: "block block-input" });
    const field = b.field === "textarea"
      ? el("textarea", { name: b.name || "value", placeholder: b.placeholder || "", rows: "3" })
      : b.field === "select"
        ? el("select", { name: b.name || "value" }, (b.options || []).map(o => el("option", { value: o }, o)))
        : el("input", { type: b.field === "number" ? "number" : "text", name: b.name || "value", placeholder: b.placeholder || "" });
    if (b.label) wrap.appendChild(el("label", {}, b.label));
    wrap.appendChild(field);
    wrap.appendChild(el("button", { class: "btn btn-primary", type: "submit" }, b.submit_label || "Send"));
    wrap.addEventListener("submit", (ev) => {
      ev.preventDefault();
      window.mindframe.postEvent(b.submit_event_type, { name: b.name || "value", value: field.value });
    });
    return wrap;
  },

  summary(b) {
    const tone = b.tone || "info";
    return el("div", { class: `block block-summary tone-${tone}` }, [
      b.title ? el("div", { class: "summary-title" }, b.title) : null,
      b.body ? el("div", { class: "summary-body" }, b.body) : null,
    ]);
  },

  divider() {
    return el("hr", { class: "block block-divider" });
  },

  "custom-html"(b) {
    const src = `/artifacts/${encodeURIComponent(currentFrameId)}/${b.src}`;
    return el("iframe", {
      class: "block block-customhtml",
      src,
      sandbox: "allow-scripts allow-same-origin",
      style: `height: ${parseInt(b.height || 400, 10)}px`,
    });
  },

  "user-action"(b) {
    return el("div", { class: "block block-useraction" }, [
      el("span", { class: "ua-marker" }, "→"),
      el("span", {}, `you clicked: ${b.label || b.event_type || "(action)"}`),
    ]);
  },

  supersedes(b) {
    // Render the replacement block, with an "edited" badge.
    const inner = b.block ? renderBlock(b.block) : el("div", {}, "(empty supersedes)");
    inner.classList.add("superseded-wrap");
    inner.appendChild(el("span", { class: "edited-badge", title: `replaces ${b.supersedes_id}` }, "edited"));
    return inner;
  },

  redact(b) {
    return el("div", { class: "block block-redact" }, `[redacted: ${b.reason || "no reason"}]`);
  },

  close(b) {
    const node = el("div", { class: "block block-close" }, [
      el("div", { class: "close-bar" }),
      el("div", { class: "close-reason" }, b.reason || "marked complete"),
    ]);
    if (Array.isArray(b.links) && b.links.length) {
      const links = el("ul", { class: "close-links" });
      for (const lid of b.links) {
        links.appendChild(el("li", {}, el("code", {}, lid)));
      }
      node.appendChild(links);
    }
    return node;
  },
};

let currentFrameId = null;
const renderedBlocks = new Map(); // id -> element

function renderBlock(block) {
  const fn = RENDERERS[block.type];
  if (!fn) {
    return el("div", { class: "block block-unknown" }, [
      el("strong", {}, `unknown block type: ${block.type}`),
      el("pre", {}, JSON.stringify(block, null, 2)),
    ]);
  }
  return fn(block);
}

function appendBlock(block, stream) {
  if (renderedBlocks.has(block.id)) return;
  const node = renderBlock(block);
  node.dataset.blockId = block.id;
  node.dataset.blockType = block.type;
  stream.appendChild(node);
  renderedBlocks.set(block.id, node);
  // Auto-scroll if user is near the bottom (within 200px).
  const nearBottom = window.scrollY + window.innerHeight >= document.body.scrollHeight - 200;
  if (nearBottom) {
    requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }));
  }
  // Brief glow animation on new blocks (CSS handles).
  requestAnimationFrame(() => node.classList.add("just-arrived"));
  setTimeout(() => node.classList.remove("just-arrived"), 900);
}

// ----- Boards index view -----

// ----- Vaults panel (v0.8.0) -----

function vaultLastTouched(v) {
  if (!v.last_commit?.committed_at) return "no commits yet";
  return relativeTime(new Date(v.last_commit.committed_at).getTime());
}

async function refreshVaults() {
  try {
    const r = await fetch("/api/vaults");
    const j = await r.json();
    const vaults = j.vaults || [];
    $("vault-count").textContent = vaults.length;
    const list = $("vault-list");
    if (!vaults.length) {
      list.innerHTML = `<div class="empty"><p>No vaults configured yet.
        Run <code>/mindframe:setup</code> to create one.</p></div>`;
      return;
    }
    list.innerHTML = vaults.map(v => {
      const typeCounts = Object.entries(v.entry_counts || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 4)
        .map(([t, n]) => `<span class="vault-type-chip">${escapeHtml(t)}: ${n}</span>`)
        .join("");
      const remoteBadge = v.remote
        ? `<span class="vault-remote-badge" title="${escapeHtml(v.remote)}">⇄ shared</span>`
        : `<span class="vault-remote-badge vault-remote-local">● local only</span>`;
      const defaultBadge = v.is_default
        ? `<span class="vault-default-badge">default</span>` : "";
      return `
        <div class="vault-tile" data-vault="${escapeHtml(v.name)}">
          <div class="vault-tile-header">
            <span class="vault-name">${escapeHtml(v.name)}</span>
            ${defaultBadge}
            ${remoteBadge}
          </div>
          <div class="vault-tile-meta">
            <span class="vault-total">${v.total_entries} entries</span>
            <span class="vault-touched">last touched ${vaultLastTouched(v)}</span>
          </div>
          <div class="vault-type-chips">${typeCounts || '<span class="vault-empty-note">empty</span>'}</div>
          <div class="vault-tile-actions">
            <button class="btn btn-sm btn-default vault-action-browse" type="button">browse</button>
            <button class="btn btn-sm btn-default vault-action-share" type="button">share</button>
          </div>
        </div>
      `;
    }).join("");

    // Wire share buttons
    list.querySelectorAll(".vault-action-share").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const tile = e.target.closest(".vault-tile");
        openShareDialog(tile.dataset.vault);
      });
    });
    list.querySelectorAll(".vault-action-browse").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const tile = e.target.closest(".vault-tile");
        openBrowseDialog(tile.dataset.vault);
      });
    });
  } catch (e) {
    $("vault-list").innerHTML = `<div class="empty"><p>vault list error: ${escapeHtml(String(e))}</p></div>`;
  }
}

async function refreshIncomingShares() {
  try {
    const r = await fetch("/api/shares/incoming");
    const j = await r.json();
    const invites = j.invitations || [];
    const el = $("incoming-shares");
    if (!invites.length) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    el.innerHTML = `
      <p class="incoming-eyebrow">pending invitations (${invites.length})</p>
      <div class="incoming-list">
        ${invites.map(inv => `
          <div class="incoming-tile ${inv.looks_like_vault ? '' : 'incoming-non-vault'}">
            <div class="incoming-header">
              <span class="incoming-repo">${escapeHtml(inv.repo)}</span>
              ${inv.looks_like_vault ? '<span class="incoming-vault-badge">vault</span>' : '<span class="incoming-other-badge">non-vault repo</span>'}
            </div>
            <div class="incoming-meta">
              from <strong>${escapeHtml(inv.inviter || '?')}</strong> ·
              ${escapeHtml(inv.permissions || '?')} ·
              ${inv.created_at ? relativeTime(new Date(inv.created_at).getTime()) : ''}
            </div>
            <div class="incoming-actions">
              ${inv.looks_like_vault
                ? `<button class="btn btn-sm btn-primary" data-invite-accept="${inv.id}">accept</button>`
                : `<span class="incoming-note">not a mindframe vault — accept via GitHub if you want it</span>`}
            </div>
          </div>
        `).join("")}
      </div>
    `;
    el.querySelectorAll("[data-invite-accept]").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        const id = parseInt(e.target.dataset.inviteAccept, 10);
        e.target.disabled = true;
        e.target.textContent = "accepting…";
        try {
          const r = await fetch("/api/shares/accept", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ invitation_id: id }),
          });
          const j = await r.json();
          if (r.ok) {
            showToast(`accepted: ${j.vault_name} (${j.repo})`, "ok");
            setTimeout(refreshIncomingShares, 8000);
            setTimeout(refreshVaults, 8000);
          } else {
            showToast(`accept failed: ${j.error || r.statusText}`, "err");
            e.target.disabled = false;
            e.target.textContent = "accept";
          }
        } catch (err) {
          showToast(`network error: ${err.message}`, "err");
          e.target.disabled = false;
          e.target.textContent = "accept";
        }
      });
    });
  } catch (e) {
    /* silent — incoming is best-effort */
  }
}

function openShareDialog(vaultName) {
  const existing = document.getElementById("share-dialog");
  if (existing) existing.remove();
  const dialog = document.createElement("div");
  dialog.id = "share-dialog";
  dialog.className = "modal-overlay";
  dialog.innerHTML = `
    <div class="modal">
      <h3 class="modal-title">Share vault: ${escapeHtml(vaultName)}</h3>
      <form id="share-form" class="modal-form">
        <label class="modal-label">Recipient (email or GitHub username)</label>
        <input name="recipient" type="text" required class="modal-input" placeholder="e.g. friend@team.com or githubuser" autofocus>
        <label class="modal-label">Permission</label>
        <select name="permission" class="modal-input">
          <option value="push">read + write</option>
          <option value="pull">read-only</option>
          <option value="admin">admin</option>
        </select>
        <label class="modal-label">GitHub owner (optional — defaults to your gh user)</label>
        <input name="owner" type="text" class="modal-input" placeholder="leave blank for default">
        <div class="modal-actions">
          <button type="button" class="btn btn-default" id="share-cancel">cancel</button>
          <button type="submit" class="btn btn-primary" id="share-submit">share</button>
        </div>
        <p class="modal-hint">Creates a private GitHub repo under the owner, pushes the vault contents, and sends the recipient a collaborator invite. They never see git/ssh — agent handles it.</p>
      </form>
    </div>
  `;
  document.body.appendChild(dialog);
  dialog.querySelector("#share-cancel").addEventListener("click", () => dialog.remove());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.remove(); });
  dialog.querySelector("#share-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const body = {
      recipient: form.recipient.value.trim(),
      permission: form.permission.value,
    };
    if (form.owner.value.trim()) body.owner = form.owner.value.trim();
    const submitBtn = form.querySelector("#share-submit");
    submitBtn.disabled = true;
    submitBtn.textContent = "queuing…";
    try {
      const r = await fetch(`/api/vaults/${encodeURIComponent(vaultName)}/share`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (r.ok) {
        showToast(`share queued → ${j.repo}; waiting on agent`, "ok");
        dialog.remove();
        setTimeout(refreshVaults, 12000);  // agent typically takes ~10s
      } else {
        showToast(`share failed: ${j.error || r.statusText}`, "err");
        submitBtn.disabled = false;
        submitBtn.textContent = "share";
      }
    } catch (err) {
      showToast(`network error: ${err.message}`, "err");
      submitBtn.disabled = false;
      submitBtn.textContent = "share";
    }
  });
}

// Type → color palette for graph nodes. Stable per type so the same kind
// of entity always looks the same across vaults.
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

async function openBrowseDialog(vaultName) {
  const existing = document.getElementById("browse-dialog");
  if (existing) existing.remove();
  const dialog = document.createElement("div");
  dialog.id = "browse-dialog";
  dialog.className = "modal-overlay";
  dialog.innerHTML = `
    <div class="modal modal-graph">
      <div class="modal-graph-header">
        <h3 class="modal-title">Vault graph: ${escapeHtml(vaultName)}</h3>
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
      fetch(`/api/vaults/${encodeURIComponent(vaultName)}/graph`).then(r => r.json()),
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
        `<p class="empty">vault is empty. vault-keeper writes here on its next tick.</p>`;
      return;
    }

    const nodes = new vis.DataSet(g.nodes.map(n => ({
      id: n.id,
      label: n.label,
      group: n.type,
      color: colorForType(n.type, allTypes),
      title: `${n.type} · ${n.label}${n.dangling_count ? ` · ${n.dangling_count} dangling` : ""}`,
      font: { color: "#e8e8e8", size: 11, face: "JetBrains Mono, monospace" },
      borderWidth: 1.5,
      _meta: n,
    })));
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


async function submitPrompt(text) {
  text = (text || "").trim();
  if (!text) {
    showToast("type a prompt first", "warn");
    return;
  }
  try {
    const r = await fetch("/api/prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, source: "home-chatbox" }),
    });
    const j = await r.json();
    if (!r.ok) {
      showToast(`couldn't create mindframe: ${j.error || r.statusText}`, "err");
      return;
    }
    if (j.dispatcher_status === "unreachable") {
      showToast("mindframe created (dispatcher offline — attach an agent manually)", "warn");
    } else if (j.dispatcher_status === "skipped") {
      showToast("mindframe created (no dispatcher bearer — frame is empty until an agent attaches)", "warn");
    } else if (j.dispatcher_status === "rejected") {
      showToast(`mindframe created, but dispatcher rejected the spawn (${j.dispatcher_error || "see logs"})`, "warn");
    } else {
      showToast("mindframe created — spinning up an agent", "ok");
    }
    history.pushState({}, "", `/m/${encodeURIComponent(j.id)}`);
    route();
  } catch (e) {
    showToast(`network error: ${e.message}`, "err");
  }
}

async function renderBoardsIndex() {
  root().innerHTML = `
    <div class="index-wrap">
      <section class="home-chat">
        <p class="home-eyebrow">tell mindframe what you want to know</p>
        <h1 class="home-headline">What should I look into?</h1>
        <form id="chat-form" class="chat-form" autocomplete="off">
          <textarea
            id="chat-input"
            class="chat-input"
            rows="3"
            placeholder="e.g. review my team's PRs every morning, or investigate why checkout latency spiked last week."
          ></textarea>
          <div class="chat-form-row">
            <span class="chat-hint">⌘/Ctrl + Enter to submit</span>
            <button type="submit" class="btn btn-primary chat-submit">Create mindframe</button>
          </div>
        </form>
        <div id="suggestions" class="suggestions">
          <div class="loading">loading suggestions…</div>
        </div>
      </section>

      <section class="vaults-section">
        <div class="index-header">
          <h2>Your vaults</h2>
          <span id="vault-count" class="count">…</span>
        </div>
        <div id="vault-list" class="vault-list">
          <div class="loading">loading vaults…</div>
        </div>
        <div id="incoming-shares" class="incoming-shares" hidden></div>
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
    </div>
  `;

  refreshVaults();
  refreshIncomingShares();

  const form = $("chat-form");
  const input = $("chat-input");
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    submitPrompt(input.value);
  });
  input.addEventListener("keydown", (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") {
      ev.preventDefault();
      submitPrompt(input.value);
    }
  });

  // Suggestions — pulled once on render; vault contents rarely change mid-session.
  try {
    const r = await fetch("/api/suggestions");
    const j = await r.json();
    const wrap = $("suggestions");
    const cards = (j.suggestions || []).map((s, i) => `
      <button class="suggest-card" type="button" data-i="${i}">
        <span class="suggest-tag tag-${escapeHtml(s.tag)}">${escapeHtml(s.tag)}</span>
        <span class="suggest-title">${escapeHtml(s.title)}</span>
        <span class="suggest-prompt">${escapeHtml(s.prompt)}</span>
      </button>
    `).join("");
    const groundedNote = j.vault_present
      ? `<p class="suggest-source">suggestions grounded in your vault at <code>${escapeHtml(j.vault_path || "")}</code></p>`
      : `<p class="suggest-source suggest-source-warn">no vault configured — suggestions are generic. Run <code>/mindframe:setup</code> to ground them in your org.</p>`;
    wrap.innerHTML = `
      <p class="suggest-eyebrow">try one of these</p>
      <div class="suggest-grid">${cards}</div>
      ${groundedNote}
    `;
    wrap.querySelectorAll(".suggest-card").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = parseInt(btn.dataset.i, 10);
        const s = (j.suggestions || [])[idx];
        if (!s) return;
        input.value = s.prompt;
        input.focus();
        input.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    });
  } catch {
    $("suggestions").innerHTML = `<p class="suggest-source suggest-source-warn">couldn't load suggestions.</p>`;
  }

  async function refresh() {
    try {
      const r = await fetch("/api/frames");
      const j = await r.json();
      const frames = j.frames || [];
      setConn("ok", `live · ${frames.length} mindframe${frames.length === 1 ? "" : "s"}`);
      $("frame-count").textContent = frames.length;
      const list = $("frame-list");
      if (!frames.length) {
        list.innerHTML = `
          <div class="empty">
            <p>No mindframes yet — type a prompt above, or pick one of the suggestions.</p>
          </div>`;
        return;
      }
      list.innerHTML = frames.map(f => `
        <a class="frame-row" href="/m/${encodeURIComponent(f.id)}">
          <span class="frame-marker frame-marker-${f.status}"></span>
          <span class="frame-title-wrap">
            <span class="frame-title">${escapeHtml(f.title)}</span>
            <span class="frame-sub">
              <span class="mono">${escapeHtml(f.id)}</span>
              <span class="frame-status">${escapeHtml(f.status)}</span>
              ${(f.tags || []).map(t => `<span class="frame-tag">${escapeHtml(t)}</span>`).join("")}
            </span>
          </span>
          <span class="frame-meta">
            <span class="frame-count">${f.block_count} block${f.block_count === 1 ? "" : "s"}</span>
            <span class="frame-time">${relativeTime(f.last_block_at)}</span>
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

// ----- Mindframe detail view — SSE-driven block stream -----

async function renderMindframeDetail(mid) {
  currentFrameId = mid;
  renderedBlocks.clear();

  // Fetch meta for title/status.
  let meta = {};
  try {
    const r = await fetch(`/api/frame/${encodeURIComponent(mid)}`);
    if (r.ok) meta = await r.json();
  } catch { /* ignore */ }

  root().innerHTML = `
    <div class="mf-wrap">
      <nav class="mf-nav">
        <a class="back" href="/">← all mindframes</a>
        <span class="mf-title-wrap">
          <span class="mf-title">${escapeHtml(meta.title || mid)}</span>
          <span class="mf-id mono">${escapeHtml(mid)}</span>
        </span>
        <span class="mf-actions">
          <span id="stream-state" class="stream-state" data-state="connecting">connecting…</span>
        </span>
      </nav>
      <div id="stream" class="mf-stream"></div>
    </div>
  `;

  const stream = $("stream");
  const stateEl = $("stream-state");

  // SSE — auto-reconnects, sends Last-Event-ID on its own.
  const es = new EventSource(`/api/frame/${encodeURIComponent(mid)}/stream`);
  es.onopen = () => { stateEl.dataset.state = "ok"; stateEl.textContent = "live"; setConn("ok", "streaming"); };
  es.onmessage = (ev) => {
    try {
      const block = JSON.parse(ev.data);
      appendBlock(block, stream);
    } catch (e) {
      console.error("bad block payload", e, ev.data);
    }
  };
  es.onerror = () => { stateEl.dataset.state = "warn"; stateEl.textContent = "reconnecting…"; };

  // Clean up when navigating away.
  window.addEventListener("popstate", () => es.close(), { once: true });
  return { stop: () => es.close() };
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

// ----- Router -----

function route() {
  const path = location.pathname;
  const m = path.match(/^\/m\/([A-Za-z0-9_-]+)\/?$/);
  if (m) {
    renderMindframeDetail(decodeURIComponent(m[1]));
  } else {
    renderBoardsIndex();
  }
}

pollHealth();
setInterval(pollHealth, HEALTH_POLL_MS);
route();
