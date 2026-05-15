// Mindframe shell — instruction box, live activity log, iframe holder.
// Each user instruction opens an SSE to /api/run. The server streams
// `progress` events (thinking, tool calls, byte counter) while claude works,
// then a single `done` event with the artifact URL. The shell renders those
// events as a live activity log under the spinner.

const SID_KEY = 'mindframe.sid';

function getOrCreateSid(): string {
  let sid = localStorage.getItem(SID_KEY);
  if (!sid) {
    sid = crypto.randomUUID();
    localStorage.setItem(SID_KEY, sid);
  }
  return sid;
}

function el<T extends HTMLElement>(sel: string): T {
  const node = document.querySelector(sel);
  if (!node) throw new Error(`missing element: ${sel}`);
  return node as T;
}

function init() {
  const stage = el<HTMLDivElement>('#stage');
  const empty = el<HTMLDivElement>('#empty');
  const spinner = el<HTMLDivElement>('#spinner');
  const spinnerSub = el<HTMLDivElement>('#spinner-sub');
  const spinnerActivity = el<HTMLDivElement>('#spinner-activity');
  const iframe = el<HTMLIFrameElement>('#artifact');
  const input = el<HTMLTextAreaElement>('#input');
  const form = el<HTMLFormElement>('#composer');
  const sidBadge = el<HTMLSpanElement>('#sid');
  const taskBadge = el<HTMLSpanElement>('#task');

  const shareBtn = el<HTMLButtonElement>('#share');
  const toast = el<HTMLDivElement>('#toast');

  const sid = getOrCreateSid();
  sidBadge.textContent = sid.slice(0, 8);

  // In dev the Vite proxy eats the SSE done event, so we point at the backend
  // directly (`http://127.0.0.1:5174`). In production the SPA is served BY
  // the backend (or by nginx in front of it), so we use the build-time BASE_URL
  // — which is '/' for root deploys and '/demo/' for the subpath deploy at
  // mindframe.softwaresoftware.dev/demo/. All API URLs must respect that base
  // so requests land at /demo/api/run rather than /api/run.
  const isDevServer = location.port === '5173';
  const baseUrl = ((import.meta as any).env?.BASE_URL || '/').replace(/\/$/, '');
  const BACKEND = (import.meta as any).env?.VITE_BACKEND
    ?? (isDevServer ? 'http://127.0.0.1:5174' : baseUrl);

  let busy = false;
  let hasArtifact = false;

  function setShareEnabled(on: boolean) {
    hasArtifact = on;
    shareBtn.disabled = !on || busy;
    shareBtn.title = on
      ? (busy ? 'finishing the current run…' : 'save this page to a sharable URL')
      : 'run an instruction first';
  }

  let toastTimer: number | undefined;
  function showToast(text: string, kind: 'ok' | 'err' = 'ok', durationMs = 6000) {
    toast.textContent = text;
    toast.dataset.kind = kind;
    toast.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, durationMs);
  }
  toast.addEventListener('click', () => { toast.hidden = true; });

  async function copyToClipboard(text: string): Promise<boolean> {
    try { await navigator.clipboard.writeText(text); return true; }
    catch {
      // Fallback for non-secure contexts: temporary textarea + execCommand.
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    }
  }

  async function share() {
    if (busy || !hasArtifact) return;
    setShareEnabled(false);
    shareBtn.textContent = 'saving…';
    try {
      const res = await fetch(`${BACKEND}/api/save`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sid, label: taskBadge.textContent || '' }),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(body || `server ${res.status}`);
      }
      const meta = await res.json() as { id: string; url: string; expiresAt: number };
      const fullUrl = `${BACKEND}${meta.url}`;
      const copied = await copyToClipboard(fullUrl);
      const expires = new Date(meta.expiresAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
      showToast(copied
        ? `link copied: ${fullUrl}  ·  expires ${expires}`
        : `saved at ${fullUrl}  ·  expires ${expires}  (copy failed — select manually)`,
        'ok',
        10000);
    } catch (err: any) {
      showToast(`save failed: ${err?.message || err}`, 'err', 8000);
    } finally {
      shareBtn.textContent = 'share';
      setShareEnabled(hasArtifact);
    }
  }
  shareBtn.addEventListener('click', share);

  let elapsedTimer: number | undefined;
  let lastActivityLabel = '';

  function setTask(t: string) {
    taskBadge.textContent = t || '—';
  }

  function showSpinner() {
    empty.hidden = true;
    spinner.hidden = false;
    spinnerActivity.replaceChildren();
    spinnerSub.textContent = '';
    const started = Date.now();
    elapsedTimer = window.setInterval(() => {
      const s = Math.floor((Date.now() - started) / 1000);
      spinnerSub.textContent = `${s}s elapsed`;
    }, 250);
  }

  function hideSpinner() {
    spinner.hidden = true;
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = undefined;
    }
  }

  function appendActivityRow(label: string, kind: 'event' | 'tick') {
    const row = document.createElement('div');
    row.className = 'activity-row';
    row.dataset.kind = kind;
    row.innerHTML = '<span class="activity-tick">›</span><span class="activity-text"></span>';
    (row.querySelector('.activity-text') as HTMLElement).textContent = label;
    spinnerActivity.appendChild(row);
    // Keep last ~6 rows visible.
    while (spinnerActivity.children.length > 6) {
      spinnerActivity.firstElementChild?.remove();
    }
  }

  function logActivity(label: string) {
    // Skip duplicate consecutive labels.
    if (label === lastActivityLabel) return;
    lastActivityLabel = label;
    appendActivityRow(label, 'event');
  }

  function updateLastActivityLabel(label: string) {
    // Heartbeat ticks update the last row in place — but ONLY if that row is
    // itself a tick. A tick must never clobber a real event row (e.g. the
    // "agent picked up the instruction" line); in that case it appends.
    const last = spinnerActivity.lastElementChild as HTMLElement | null;
    if (last && last.dataset.kind === 'tick') {
      const text = last.querySelector('.activity-text') as HTMLElement | null;
      if (text) text.textContent = label;
    } else {
      appendActivityRow(label, 'tick');
    }
  }

  function showError(msg: string) {
    hideSpinner();
    empty.hidden = false;
    empty.innerHTML = `<h1 style="color: var(--color-danger)">Error</h1><p>${msg}</p>`;
  }

  function submit() {
    if (busy) return;
    const msg = input.value.trim();
    if (!msg) return;
    input.value = '';
    autoResize();
    busy = true;
    setShareEnabled(hasArtifact);   // disables while busy
    lastActivityLabel = '';
    setTask(msg.length > 80 ? msg.slice(0, 77) + '…' : msg);
    showSpinner();

    // Bypass Vite's dev-proxy for the SSE stream — it swallows the final
    // `done` event when the server calls res.end(). Hit the backend directly.
    const url = `${BACKEND}/api/run?sid=${encodeURIComponent(sid)}&msg=${encodeURIComponent(msg)}`;
    const es = new EventSource(url);

    es.addEventListener('progress', (e: MessageEvent) => {
      try {
        const evt = JSON.parse(e.data);
        const label = String(evt.label ?? '');
        if (!label) return;
        // Tick events (rapid byte counter) update the last row in place;
        // everything else appends a new row.
        if (evt.kind === 'tick') {
          updateLastActivityLabel(label);
        } else {
          logActivity(label);
        }
      } catch {}
    });

    es.addEventListener('done', (e: MessageEvent) => {
      es.close();
      try {
        const { url: artifactUrl } = JSON.parse(e.data);
        // Artifact URL is a backend-relative path; make it absolute too.
        const absolute = artifactUrl.startsWith('http')
          ? artifactUrl
          : `${BACKEND}${artifactUrl}`;
        iframe.src = `${absolute}?t=${Date.now()}`;
        iframe.hidden = false;
        hideSpinner();
        busy = false;
        setShareEnabled(true);
        return;
      } catch (err: any) {
        showError(err?.message || 'bad done payload');
      }
      busy = false;
      setShareEnabled(hasArtifact);
    });

    es.addEventListener('error', (e: MessageEvent) => {
      const data = (e && 'data' in e) ? e.data : '';
      es.close();
      showError(data ? String(data) : 'stream lost');
      busy = false;
      setShareEnabled(hasArtifact);
    });
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    submit();
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  });

  function autoResize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 180) + 'px';
  }
  input.addEventListener('input', autoResize);

  stage.addEventListener('click', (e) => {
    const t = e.target as HTMLElement;
    if (t.matches('button.starter[data-cmd]')) {
      input.value = t.getAttribute('data-cmd') || '';
      submit();
    }
  });

  // Iframe action-button bridge — artifacts can postMessage `{type:"mindframe:run", cmd:"..."}`
  window.addEventListener('message', (e) => {
    const data = e.data;
    if (data && typeof data === 'object' && data.type === 'mindframe:run' && typeof data.cmd === 'string') {
      input.value = data.cmd;
      submit();
    }
  });

  // Restore the last artifact for this session on reload. The artifact is
  // persisted server-side at artifacts/<sid>/latest.html; without this a page
  // refresh drops the user back to the empty state even though their tool is
  // sitting right there on disk.
  (async () => {
    try {
      const probe = `${BACKEND}/artifacts/${encodeURIComponent(sid)}/latest.html`;
      const r = await fetch(probe, { method: 'HEAD' });
      if (r.ok && busy === false && iframe.hidden) {
        iframe.src = `${probe}?t=${Date.now()}`;
        iframe.hidden = false;
        empty.hidden = true;
        setShareEnabled(true);
      }
    } catch { /* no prior artifact, or backend down — stay on empty state */ }
  })();

  input.focus();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
