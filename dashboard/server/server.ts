// Mindframe dashboard backend — taskpilot agent mode.
//
// The dashboard is driven by ONE persistent taskpilot task (the "Mindframe
// agent"). Each user instruction is delivered to that agent as a mesh message.
// The agent reads the customer vault, composes a complete HTML document, and
// writes it to artifacts/<sid>/latest.html. The server watches that file and
// notifies the browser over SSE when it lands.
//
//   Browser --SSE /api/run--> this server
//                                  |  POST :8912/tasks/<agent>/message
//                                  v
//                          taskpilot daemon --> session-bridge --> agent (tmux)
//                                                                     | writes
//                                                                     v
//                                              artifacts/<sid>/latest.html
//
// No `claude --print`. No Anthropic API key. The agent is a full Claude Code
// session supervised by taskpilot; it authenticates with the user's
// subscription exactly as any taskpilot task does.

import express from 'express';
import { execFile } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import {
  readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync,
  statSync, rmSync,
} from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { homedir } from 'node:os';
import type { Response } from 'express';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const ARTIFACTS_ROOT = resolve(ROOT, 'artifacts');
const SHARES_ROOT = resolve(ROOT, 'shares');
const AGENT_CWD = resolve(ROOT, 'agent');
const AGENT_BRIEF = resolve(AGENT_CWD, 'brief.json');
const AGENT_ID_FILE = resolve(ROOT, '.agent-id');
// The customer vault lives beside the dashboard, under the mindframe launch dir.
const VAULT_ROOT = resolve(ROOT, '../launch/stage/vault');

const PORT = Number(process.env.PORT || 5174);
const MODEL = process.env.MINDFRAME_MODEL || 'sonnet';
const TASKPILOT_DAEMON = process.env.MINDFRAME_TASKPILOT_DAEMON || 'http://127.0.0.1:8912';
const SESSION_BRIDGE = process.env.MINDFRAME_SESSION_BRIDGE || 'http://127.0.0.1:8910';
const TASKPILOT_DIR = process.env.MINDFRAME_TASKPILOT_DIR
  || resolve(ROOT, '../../../providers/taskpilot');

const SHARE_RETENTION_DAYS = Number(process.env.MINDFRAME_SHARE_RETENTION_DAYS || 60);
const SHARE_RETENTION_MS = SHARE_RETENTION_DAYS * 24 * 60 * 60 * 1000;
const SHARE_ID_LEN = 10;
const SHARE_ID_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';

// How long to wait for the agent to produce an artifact before giving up.
const RUN_TIMEOUT_MS = 6 * 60 * 1000;
// After the artifact file stops changing for this long, consider it final.
const ARTIFACT_STABLE_MS = 3000;
// Grace after the agent ends a turn with no file before declaring failure.
const NO_WRITE_GRACE_MS = 4000;

mkdirSync(ARTIFACTS_ROOT, { recursive: true });
mkdirSync(SHARES_ROOT, { recursive: true });

// --------------------------- shares ---------------------------

function generateShareId(): string {
  for (let attempt = 0; attempt < 8; attempt++) {
    const bytes = randomBytes(SHARE_ID_LEN);
    let out = '';
    for (let i = 0; i < SHARE_ID_LEN; i++) {
      out += SHARE_ID_ALPHABET[bytes[i] % SHARE_ID_ALPHABET.length];
    }
    if (!existsSync(join(SHARES_ROOT, out))) return out;
  }
  return `${Date.now().toString(36)}${randomBytes(3).toString('hex')}`;
}

function sweepExpiredShares(): { kept: number; pruned: number } {
  let kept = 0;
  let pruned = 0;
  const now = Date.now();
  let entries: string[];
  try { entries = readdirSync(SHARES_ROOT); } catch { return { kept, pruned }; }
  for (const id of entries) {
    const dir = join(SHARES_ROOT, id);
    let st;
    try { st = statSync(dir); } catch { continue; }
    if (!st.isDirectory()) continue;
    const metaPath = join(dir, 'meta.json');
    let createdAt = st.mtimeMs;
    if (existsSync(metaPath)) {
      try {
        const meta = JSON.parse(readFileSync(metaPath, 'utf8'));
        if (typeof meta.createdAt === 'number') createdAt = meta.createdAt;
      } catch {}
    }
    if (now - createdAt > SHARE_RETENTION_MS) {
      try { rmSync(dir, { recursive: true, force: true }); pruned++; } catch {}
    } else {
      kept++;
    }
  }
  return { kept, pruned };
}

// --------------------------- artifacts ---------------------------

function sidDir(sid: string): string {
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(sid)) sid = randomUUID();
  const dir = join(ARTIFACTS_ROOT, sid);
  mkdirSync(dir, { recursive: true });
  return dir;
}

function artifactPath(sid: string): string {
  return join(sidDir(sid), 'latest.html');
}

function artifactMtime(path: string): number {
  try { return statSync(path).mtimeMs; } catch { return 0; }
}

// --------------------------- taskpilot integration ---------------------------

interface DaemonStatus { taskpilot: boolean; sessionBridge: boolean; }

async function checkDaemons(): Promise<DaemonStatus> {
  const probe = async (url: string) => {
    try {
      const r = await fetch(`${url}/health`, { signal: AbortSignal.timeout(3000) });
      return r.ok;
    } catch { return false; }
  };
  const [taskpilot, sessionBridge] = await Promise.all([
    probe(TASKPILOT_DAEMON),
    probe(SESSION_BRIDGE),
  ]);
  return { taskpilot, sessionBridge };
}

function readAgentId(): string | null {
  try {
    const id = readFileSync(AGENT_ID_FILE, 'utf8').trim();
    return id || null;
  } catch { return null; }
}

function writeAgentId(id: string): void {
  writeFileSync(AGENT_ID_FILE, id + '\n', 'utf8');
}

async function getTask(id: string): Promise<any | null> {
  try {
    const r = await fetch(`${TASKPILOT_DAEMON}/tasks/${encodeURIComponent(id)}`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

async function spawnTask(id: string): Promise<void> {
  const r = await fetch(`${TASKPILOT_DAEMON}/tasks/${encodeURIComponent(id)}/spawn`, {
    method: 'POST',
    signal: AbortSignal.timeout(60000),
  });
  if (!r.ok && r.status !== 409) {
    const body = await r.text().catch(() => '');
    throw new Error(`taskpilot spawn failed (${r.status}): ${body}`);
  }
}

// Create the Mindframe agent task config via taskpilot's spawner CLI
// (--dry-run registers the task in the daemon's store without launching it).
function createAgentTask(): Promise<string> {
  return new Promise((resolvePromise, reject) => {
    const cli = join(TASKPILOT_DIR, 'spawner_cli.py');
    if (!existsSync(cli)) {
      reject(new Error(`taskpilot spawner_cli not found at ${cli} - set MINDFRAME_TASKPILOT_DIR`));
      return;
    }
    // Unique name per creation so a stale killed task in taskpilot's store
    // never blocks a fresh create. The id is persisted to .agent-id and
    // reused across restarts, so this only mints a new task when there is
    // genuinely no agent to reuse.
    const name = `mindframe-dashboard-agent-${Date.now().toString(36)}`;
    execFile('python3', [
      cli,
      'Mindframe dashboard agent',
      '--name', name,
      '--brief', AGENT_BRIEF,
      '--cwd', AGENT_CWD,
      '--model', MODEL,
      '--dry-run',
    ], { cwd: TASKPILOT_DIR, timeout: 60000 }, (err, stdout, stderr) => {
      if (err) {
        reject(new Error(`spawner_cli failed: ${stderr || err.message}`));
        return;
      }
      try {
        const lastLine = stdout.trim().split('\n').filter(Boolean).pop() || '{}';
        const out = JSON.parse(lastLine);
        const id = out.task_id || out.id;
        if (!id) {
          reject(new Error(`spawner_cli returned no task_id: ${stdout}`));
          return;
        }
        resolvePromise(String(id));
      } catch (e: any) {
        reject(new Error(`spawner_cli output unparseable: ${stdout} - ${e.message}`));
      }
    });
  });
}

async function isChannelHealthy(id: string): Promise<boolean> {
  try {
    const r = await fetch(`${SESSION_BRIDGE}/sessions`, { signal: AbortSignal.timeout(3000) });
    if (!r.ok) return false;
    const data = await r.json();
    const list = Array.isArray(data) ? data : (data.sessions || []);
    return list.some((s: any) => (s.id || s.session_id || s.name) === id);
  } catch { return false; }
}

// The agent's Stop hook records the end of every turn to its state file.
// This is the reliable "agent finished a turn" signal. Returns epoch-ms of the
// most recent turn end, or 0 if unknown.
function agentLastStopMs(agentId: string): number {
  try {
    const p = join(homedir(), '.taskpilot', agentId, 'state', 'agent.json');
    const j = JSON.parse(readFileSync(p, 'utf8'));
    const iso = j?.last_stop?.received_at;
    if (typeof iso === 'string') {
      const t = Date.parse(iso);
      if (isFinite(t)) return t;
    }
  } catch {}
  return 0;
}

// The agent's UserPromptSubmit hook records the last prompt it received.
// We use this to confirm the agent has actually PICKED UP our instruction
// (vs. still chewing through an earlier queued message). Returns the prompt
// text and when it arrived, or empty/0 if unknown.
function agentLastPrompt(agentId: string): { receivedAt: number; prompt: string } {
  try {
    const p = join(homedir(), '.taskpilot', agentId, 'state', 'agent.json');
    const j = JSON.parse(readFileSync(p, 'utf8'));
    const lp = j?.last_prompt;
    if (lp && typeof lp.prompt === 'string') {
      const t = Date.parse(lp.received_at);
      return { receivedAt: isFinite(t) ? t : 0, prompt: lp.prompt };
    }
  } catch {}
  return { receivedAt: 0, prompt: '' };
}

// Agent lifecycle - exactly one persistent agent. ensureAgent() is idempotent
// and de-duplicated: concurrent callers await the same in-flight promise.
let agentReadyPromise: Promise<string> | null = null;

async function ensureAgentInner(): Promise<string> {
  let id = readAgentId();

  if (id) {
    const task = await getTask(id);
    if (task && task.status === 'running') {
      if (await isChannelHealthy(id)) return id;
      for (let i = 0; i < 20; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        if (await isChannelHealthy(id)) return id;
      }
    }
    if (task && task.status !== 'running') {
      await spawnTask(id);
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        if (await isChannelHealthy(id)) return id;
      }
      return id;
    }
    // id on file but daemon doesn't know it - recreate.
    id = null;
  }

  const newId = await createAgentTask();
  writeAgentId(newId);
  await spawnTask(newId);
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    if (await isChannelHealthy(newId)) return newId;
  }
  return newId;
}

function ensureAgent(forceRecreate = false): Promise<string> {
  if (forceRecreate) agentReadyPromise = null;
  if (!agentReadyPromise) {
    agentReadyPromise = ensureAgentInner().catch((e) => {
      agentReadyPromise = null;
      throw e;
    });
  }
  return agentReadyPromise;
}

async function sendMessage(agentId: string, text: string): Promise<void> {
  const r = await fetch(`${TASKPILOT_DAEMON}/tasks/${encodeURIComponent(agentId)}/message`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text, from_session: 'mindframe-dashboard' }),
    signal: AbortSignal.timeout(15000),
  });
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`message delivery failed (${r.status}): ${body}`);
  }
}

// --------------------------- express app ---------------------------

const app = express();
app.use(express.json({ limit: '1mb' }));

app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', req.headers.origin || '*');
  res.setHeader('Access-Control-Allow-Credentials', 'true');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Accept');
  if (req.method === 'OPTIONS') { res.status(204).end(); return; }
  next();
});

app.use('/artifacts', express.static(ARTIFACTS_ROOT, {
  setHeaders: (res) => res.setHeader('Cache-Control', 'no-store, must-revalidate'),
}));

app.get('/s/:id', (req, res) => {
  const id = req.params.id;
  if (!/^[A-Za-z0-9]{1,32}$/.test(id)) { res.status(400).send('invalid share id'); return; }
  const dir = join(SHARES_ROOT, id);
  const file = join(dir, 'index.html');
  if (!existsSync(file)) { res.status(404).send('share not found'); return; }
  const metaPath = join(dir, 'meta.json');
  if (existsSync(metaPath)) {
    try {
      const meta = JSON.parse(readFileSync(metaPath, 'utf8'));
      if (typeof meta.createdAt === 'number' && Date.now() - meta.createdAt > SHARE_RETENTION_MS) {
        res.status(410).send('this share has expired');
        return;
      }
    } catch {}
  }
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.setHeader('Cache-Control', 'public, max-age=300');
  res.sendFile(file);
});

app.get('/api/share/:id', (req, res) => {
  const id = req.params.id;
  if (!/^[A-Za-z0-9]{1,32}$/.test(id)) { res.status(400).json({ error: 'invalid share id' }); return; }
  const metaPath = join(SHARES_ROOT, id, 'meta.json');
  if (!existsSync(metaPath)) { res.status(404).json({ error: 'share not found' }); return; }
  try {
    res.json(JSON.parse(readFileSync(metaPath, 'utf8')));
  } catch {
    res.status(500).json({ error: 'meta unreadable' });
  }
});

function sse(res: Response, event: string, data: unknown) {
  res.write(`event: ${event}\n`);
  const payload = typeof data === 'string' ? data : JSON.stringify(data);
  for (const line of payload.split('\n')) res.write(`data: ${line}\n`);
  res.write('\n');
}

// One instruction at a time - there is a single shared agent.
let runInFlight = false;

app.get('/api/run', async (req, res) => {
  const msg = String(req.query.msg ?? '').trim();
  const sid = String(req.query.sid ?? '') || randomUUID();
  if (!msg) { res.status(400).json({ error: 'msg required' }); return; }

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders?.();

  const finish = () => { try { res.end(); } catch {} };
  const fail = (message: string) => { sse(res, 'error', message); finish(); };

  if (runInFlight) {
    fail('the agent is already working on an instruction - wait for it to finish');
    return;
  }

  const daemons = await checkDaemons();
  if (!daemons.taskpilot || !daemons.sessionBridge) {
    const down = [
      !daemons.taskpilot ? 'taskpilot daemon (:8912)' : null,
      !daemons.sessionBridge ? 'session-bridge daemon (:8910)' : null,
    ].filter(Boolean).join(' and ');
    fail(`${down} unreachable. Start the taskpilot daemon (python3 daemon.py --install), then retry.`);
    return;
  }

  runInFlight = true;
  const keepalive = setInterval(() => { try { res.write(': keepalive\n\n'); } catch {} }, 15000);
  let settled = false;
  const cleanup = () => {
    if (settled) return;
    settled = true;
    clearInterval(keepalive);
    runInFlight = false;
  };

  try {
    sse(res, 'progress', { stage: 'agent', label: 'connecting to the Mindframe agent' });

    let agentId: string;
    try {
      agentId = await ensureAgent();
    } catch (e: any) {
      cleanup();
      fail(`could not reach the Mindframe agent: ${e?.message || e}`);
      return;
    }

    const artifact = artifactPath(sid);
    const baselineMtime = artifactMtime(artifact);

    // Unique per-instruction nonce. The agent is a shared, persistent session
    // with a message queue — a Stop event after we send is NOT necessarily the
    // response to OUR instruction. We embed this run id in the message and
    // confirm pickup by finding it in the agent's last_prompt before arming
    // any fail-fast logic.
    const runId = randomUUID();
    const message = [
      `INSTRUCTION: ${msg}`,
      '',
      `VAULT: ${VAULT_ROOT}`,
      `ARTIFACT: ${artifact}`,
      `RUN-ID: ${runId}`,
      '',
      'Write the complete HTML document to the ARTIFACT path. Read the VAULT for'
        + ' ground truth. The RUN-ID line is correlation metadata — ignore it.'
        + ' Output nothing else.',
    ].join('\n');

    try {
      await sendMessage(agentId, message);
    } catch {
      try {
        agentId = await ensureAgent(true);
        await sendMessage(agentId, message);
      } catch (e2: any) {
        cleanup();
        fail(`could not deliver the instruction to the agent: ${e2?.message || e2}`);
        return;
      }
    }
    sse(res, 'progress', { stage: 'running', label: 'instruction delivered - agent is working' });

    // Watch the artifact file until it lands and stabilizes.
    //
    // Fail-fast is keyed off PICKUP, not send time. The agent has a message
    // queue: it may be busy on an earlier message when we send. We only arm
    // the "finished without writing" check once the agent's last_prompt
    // carries our runId — i.e. it is genuinely working on THIS instruction.
    // Until then we just wait (heartbeats), bounded by RUN_TIMEOUT_MS.
    const started = Date.now();
    let firstWriteAt = 0;
    let lastMtime = baselineMtime;
    let lastHeartbeatAt = 0;
    let pickedUpAt = 0;

    await new Promise<void>((resolveRun) => {
      const tick = setInterval(() => {
        if (Date.now() - started > RUN_TIMEOUT_MS) {
          clearInterval(tick);
          cleanup();
          fail('timed out waiting for the agent to produce the page (6 min)');
          resolveRun();
          return;
        }

        const mtime = artifactMtime(artifact);
        if (mtime > baselineMtime) {
          if (!firstWriteAt) {
            firstWriteAt = Date.now();
            sse(res, 'progress', { stage: 'running', label: 'artifact written - finalizing' });
          }
          if (mtime !== lastMtime) {
            lastMtime = mtime;          // still changing; reset stability window
            firstWriteAt = Date.now();
          } else if (Date.now() - firstWriteAt >= ARTIFACT_STABLE_MS) {
            clearInterval(tick);
            const url = `/artifacts/${encodeURIComponent(sid)}/latest.html`;
            let bytes = 0;
            try { bytes = statSync(artifact).size; } catch {}
            sse(res, 'done', { url, sid, bytes });
            cleanup();
            finish();
            resolveRun();
            return;
          }
          return;
        }

        // No artifact yet. Confirm the agent has picked up THIS instruction
        // before trusting any Stop event as "finished our turn".
        if (!pickedUpAt) {
          const lp = agentLastPrompt(agentId);
          if (lp.prompt.includes(runId)) {
            pickedUpAt = lp.receivedAt || Date.now();
            sse(res, 'progress', { stage: 'running', label: 'agent picked up the instruction' });
          }
        }

        // Fail-fast only once picked up: if the agent ended a turn AFTER it
        // received our instruction and still wrote nothing, it's a real miss.
        if (pickedUpAt && !firstWriteAt) {
          const stop = agentLastStopMs(agentId);
          if (stop > pickedUpAt && Date.now() - stop >= NO_WRITE_GRACE_MS) {
            clearInterval(tick);
            cleanup();
            fail('the agent finished without writing a page - try rephrasing the instruction');
            resolveRun();
            return;
          }
        }

        if (Date.now() - lastHeartbeatAt >= 12000) {
          lastHeartbeatAt = Date.now();
          const secs = Math.round((Date.now() - started) / 1000);
          const label = pickedUpAt
            ? `agent is working - ${secs}s`
            : `agent is busy - your instruction is queued (${secs}s)`;
          sse(res, 'progress', { stage: 'running', label, kind: 'tick' });
        }
      }, 1000);

      req.on('close', () => {
        clearInterval(tick);
        cleanup();
        resolveRun();
      });
    });
  } catch (e: any) {
    cleanup();
    fail(`run failed: ${e?.message || e}`);
  }
});

// POST /api/save - snapshot the current artifact to a sharable URL.
app.post('/api/save', (req, res) => {
  const sid = String(req.body?.sid ?? '').trim();
  const label = typeof req.body?.label === 'string' ? req.body.label.slice(0, 200) : '';
  if (!sid) { res.status(400).json({ error: 'sid required' }); return; }
  const src = artifactPath(sid);
  if (!existsSync(src)) {
    res.status(404).json({ error: 'no artifact to save - run an instruction first' });
    return;
  }
  const html = readFileSync(src, 'utf8');
  const id = generateShareId();
  const dir = join(SHARES_ROOT, id);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'index.html'), html, 'utf8');
  const createdAt = Date.now();
  const expiresAt = createdAt + SHARE_RETENTION_MS;
  const meta = {
    id, sid, label, createdAt, expiresAt,
    retentionDays: SHARE_RETENTION_DAYS, bytes: html.length,
  };
  writeFileSync(join(dir, 'meta.json'), JSON.stringify(meta, null, 2), 'utf8');
  res.json({ id, url: `/s/${id}`, ...meta });
});

app.get('/api/health', async (_req, res) => {
  const daemons = await checkDaemons();
  res.json({ ok: true, port: PORT, agentId: readAgentId(), daemons });
});

// Serve the built SPA. Behind nginx, /demo/ is stripped, so the backend
// sees /, /assets/*, etc. express.static serves index.html at /; the
// fallback handles SPA client-side routes.
const DIST_ROOT = join(__dirname, '..', 'dist');
app.use(express.static(DIST_ROOT));
app.use((req, res) => {
  if (req.method === 'GET' && !req.path.startsWith('/api/')) {
    res.sendFile(join(DIST_ROOT, 'index.html'));
  } else {
    res.status(404).json({ error: 'not found' });
  }
});

app.listen(PORT, '127.0.0.1', async () => {
  // eslint-disable-next-line no-console
  console.log(`[mindframe-dashboard] server on http://127.0.0.1:${PORT}`);
  console.log(`[mindframe-dashboard] model: ${MODEL}`);
  console.log(`[mindframe-dashboard] artifacts: ${ARTIFACTS_ROOT}`);
  console.log(`[mindframe-dashboard] shares: ${SHARES_ROOT} (${SHARE_RETENTION_DAYS}-day retention)`);
  console.log(`[mindframe-dashboard] vault: ${VAULT_ROOT}`);
  const { kept, pruned } = sweepExpiredShares();
  console.log(`[mindframe-dashboard] startup share sweep: kept ${kept} pruned ${pruned}`);

  const daemons = await checkDaemons();
  if (!daemons.taskpilot || !daemons.sessionBridge) {
    console.warn('[mindframe-dashboard] WARNING: taskpilot/session-bridge daemon down - '
      + 'instructions will hard-fail until it is up.');
    return;
  }
  console.log('[mindframe-dashboard] warming the Mindframe agent...');
  ensureAgent()
    .then((id) => console.log(`[mindframe-dashboard] agent ready: ${id}`))
    .catch((e) => console.warn(`[mindframe-dashboard] agent warm-up failed: ${e?.message || e}`));
});

setInterval(() => {
  const { pruned } = sweepExpiredShares();
  if (pruned > 0) {
    // eslint-disable-next-line no-console
    console.log(`[mindframe-dashboard] hourly sweep pruned ${pruned} expired share(s)`);
  }
}, 60 * 60 * 1000).unref();
