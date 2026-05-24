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
      const r = await fetch("/api/frames");
      const j = await r.json();
      const frames = j.frames || [];
      setConn("ok", `live · ${frames.length} mindframe${frames.length === 1 ? "" : "s"}`);
      $("frame-count").textContent = frames.length;
      const list = $("frame-list");
      if (!frames.length) {
        list.innerHTML = `
          <div class="empty">
            <p>No mindframes yet.</p>
            <p class="empty-hint">Create one via the MCP:</p>
            <pre>python3 -c "
import sys, os
sys.path.insert(0, '<path>/mindframe/mcp')
os.environ['MINDFRAME_ID'] = 'my-frame'
import server
server.write_block({'type': 'text', 'markdown': 'hello'})
"</pre>
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
