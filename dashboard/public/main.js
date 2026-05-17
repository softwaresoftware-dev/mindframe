// Mindframe shell — instruction box, live activity log, iframe holder.
// Each user instruction opens an SSE to api/run. The server streams
// `progress` events (thinking, tool calls, byte counter) while claude works,
// then a single `done` event with the artifact URL. The shell renders those
// events as a live activity log under the spinner.
//
// No build step: this file is served as-is. All API/asset URLs are relative
// to APP_BASE — the directory the page is served from — so the same files
// work whether the page is at / (local) or /demo/ (behind nginx).

const SID_KEY = 'mindframe.sid';

// The directory the current page lives in: '/' locally, '/demo/' behind nginx.
// Every API call and artifact URL is resolved against this.
const APP_BASE = location.pathname.replace(/[^/]*$/, '');

function getOrCreateSid() {
  let sid = localStorage.getItem(SID_KEY);
  if (!sid) {
    sid = crypto.randomUUID();
    localStorage.setItem(SID_KEY, sid);
  }
  return sid;
}

function el(sel) {
  const node = document.querySelector(sel);
  if (!node) throw new Error(`missing element: ${sel}`);
  return node;
}

function init() {
  const stage = el('#stage');
  const empty = el('#empty');
  const spinner = el('#spinner');
  const spinnerSub = el('#spinner-sub');
  const spinnerActivity = el('#spinner-activity');
  const iframe = el('#artifact');
  const input = el('#input');
  const form = el('#composer');
  const sidBadge = el('#sid');
  const taskBadge = el('#task');

  const shareBtn = el('#share');
  const toast = el('#toast');

  const sid = getOrCreateSid();
  sidBadge.textContent = sid.slice(0, 8);

  let busy = false;
  let hasArtifact = false;

  function setShareEnabled(on) {
    hasArtifact = on;
    shareBtn.disabled = !on || busy;
    shareBtn.title = on
      ? (busy ? 'finishing the current run…' : 'save this page to a sharable URL')
      : 'run an instruction first';
  }

  let toastTimer;
  function showToast(text, kind = 'ok', durationMs = 6000) {
    toast.textContent = text;
    toast.dataset.kind = kind;
    toast.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, durationMs);
  }
  toast.addEventListener('click', () => { toast.hidden = true; });

  async function copyToClipboard(text) {
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
      const res = await fetch(APP_BASE + 'api/save', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sid, label: taskBadge.textContent || '' }),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(body || `server ${res.status}`);
      }
      const meta = await res.json();
      // meta.url is a backend-relative path like /s/<id>; build a full URL.
      const fullUrl = location.origin + APP_BASE + String(meta.url).replace(/^\//, '');
      const copied = await copyToClipboard(fullUrl);
      const expires = new Date(meta.expiresAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
      showToast(copied
        ? `link copied: ${fullUrl}  ·  expires ${expires}`
        : `saved at ${fullUrl}  ·  expires ${expires}  (copy failed — select manually)`,
        'ok',
        10000);
    } catch (err) {
      showToast(`save failed: ${err?.message || err}`, 'err', 8000);
    } finally {
      shareBtn.textContent = 'share';
      setShareEnabled(hasArtifact);
    }
  }
  shareBtn.addEventListener('click', share);

  let elapsedTimer;
  let lastActivityLabel = '';

  function setTask(t) {
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

  function appendActivityRow(label, kind) {
    const row = document.createElement('div');
    row.className = 'activity-row';
    row.dataset.kind = kind;
    row.innerHTML = '<span class="activity-tick">›</span><span class="activity-text"></span>';
    row.querySelector('.activity-text').textContent = label;
    spinnerActivity.appendChild(row);
    // Keep last ~6 rows visible.
    while (spinnerActivity.children.length > 6) {
      spinnerActivity.firstElementChild?.remove();
    }
  }

  function logActivity(label) {
    // Skip duplicate consecutive labels.
    if (label === lastActivityLabel) return;
    lastActivityLabel = label;
    appendActivityRow(label, 'event');
  }

  function updateLastActivityLabel(label) {
    // Heartbeat ticks update the last row in place — but ONLY if that row is
    // itself a tick. A tick must never clobber a real event row (e.g. the
    // "agent picked up the instruction" line); in that case it appends.
    const last = spinnerActivity.lastElementChild;
    if (last && last.dataset.kind === 'tick') {
      const text = last.querySelector('.activity-text');
      if (text) text.textContent = label;
    } else {
      appendActivityRow(label, 'tick');
    }
  }

  function showError(msg) {
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

    const url = APP_BASE + 'api/run?sid=' + encodeURIComponent(sid) + '&msg=' + encodeURIComponent(msg);
    const es = new EventSource(url);

    es.addEventListener('progress', (e) => {
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

    es.addEventListener('done', (e) => {
      es.close();
      try {
        const { url: artifactUrl } = JSON.parse(e.data);
        // Artifact URL is a backend-relative path; resolve it against APP_BASE.
        const absolute = String(artifactUrl).startsWith('http')
          ? artifactUrl
          : APP_BASE + String(artifactUrl).replace(/^\//, '');
        iframe.src = `${absolute}?t=${Date.now()}`;
        iframe.hidden = false;
        hideSpinner();
        busy = false;
        setShareEnabled(true);
        return;
      } catch (err) {
        showError(err?.message || 'bad done payload');
      }
      busy = false;
      setShareEnabled(hasArtifact);
    });

    es.addEventListener('error', (e) => {
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
    const t = e.target;
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
      const probe = APP_BASE + 'artifacts/' + encodeURIComponent(sid) + '/latest.html';
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
