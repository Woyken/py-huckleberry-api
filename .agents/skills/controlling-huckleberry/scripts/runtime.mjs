import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { existsSync, openSync, closeSync } from 'node:fs';
import { mkdir, readFile, writeFile, rename, rm, mkdtemp } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { adbCompatibility } from './adb-safety.mjs';

const exec = promisify(execFile);
export const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
export const PACKAGE = 'com.huckleberry_labs.app';
export const ACTIVITY = `${PACKAGE}/.MainActivity`;
export const STATE_DIR = join(process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'), 'huckleberry-agent');
export const RUNTIME_DIR = join(STATE_DIR, 'runtime');
const CLAIM = join(STATE_DIR, 'claim');
const STATE = join(CLAIM, 'state.json');
const BUSY = join(STATE_DIR, 'command.json'); // Diagnostic only; the OS mutex is the actual command lock.
// Cold boot on this existing software-rendered AVD can take minutes.
const BOOT_TIMEOUT_MS = 180000;
// Every individual ADB call is bounded, including offline/unresponsive devices.
const ADB_TIMEOUT_MS = 10000;
export const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function validateConfig(config) {
  if (!/^[A-Za-z0-9_.-]+$/.test(config.avd || '')) throw new Error('config.json: invalid AVD name.');
  const match = /^emulator-(\d+)$/.exec(config.serial || '');
  const port = Number(match?.[1]);
  if (!match || port < 5554 || port > 5682 || port % 2) {
    throw new Error('config.json: serial must be an explicit emulator with an even console port (5554–5682), never a physical phone.');
  }
  return { avd: config.avd, serial: config.serial, port };
}

export async function config() {
  return validateConfig(JSON.parse(await readFile(join(ROOT, 'config.json'), 'utf8')));
}

export function validateSession(session) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$/.test(session || '')) {
    throw new Error('Pass --session with a unique 3–80 character task ID (letters, digits, dot, underscore, hyphen). Reuse it for this task only.');
  }
  return session;
}

export async function readState() {
  try { return JSON.parse(await readFile(STATE, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

async function saveState(state) {
  const temporary = `${STATE}.${process.pid}.tmp`;
  await writeFile(temporary, JSON.stringify(state, null, 2) + '\n', { mode: 0o600 });
  await rename(temporary, STATE);
}

export function sdkRoot() {
  const candidates = [process.env.ANDROID_HOME, process.env.ANDROID_SDK_ROOT,
    join(process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'), 'Android', 'Sdk')];
  return candidates.find((path) => path && existsSync(join(path, 'emulator', 'emulator.exe')));
}

export function adbExecutable() {
  const sdk = sdkRoot();
  const bundled = sdk && join(sdk, 'platform-tools', 'adb.exe');
  return bundled && existsSync(bundled) ? bundled : 'adb.exe';
}

let compatibility;
export async function adb(args, { allowFailure = false, timeout = ADB_TIMEOUT_MS } = {}) {
  const executable = adbExecutable();
  // Intentionally outside allowFailure: compatibility errors must never be
  // mistaken for an absent emulator, which could trigger a duplicate startup.
  compatibility ??= adbCompatibility(executable);
  await compatibility;
  try {
    const result = await exec(executable, args, { timeout, windowsHide: true, maxBuffer: 2 * 1024 * 1024 });
    return result.stdout.trim();
  } catch (error) {
    if (allowFailure) return '';
    const detail = String(error.stderr || error.message).trim();
    throw new Error(`ADB failed (${args.slice(0, 4).join(' ')}): ${detail}`);
  }
}

export async function setupRuntime() {
  if (await readState()) throw new Error('Release the active skill session before changing runtime dependencies.');
  const result = await exec('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', join(ROOT, 'scripts', 'setup.ps1')],
    { timeout: 180000, windowsHide: true, maxBuffer: 1024 * 1024 });
  return { runtime: RUNTIME_DIR, dependency: 'playwright-core@1.64.1', output: result.stdout.trim() };
}

export function parseForwards(text) {
  return text.split(/\r?\n/).map((line) => line.trim().split(/\s+/))
    .filter((parts) => parts.length === 3)
    .map(([serial, local, remote]) => ({ serial, local, remote }));
}

export function sameForward(rule, state) {
  return !!rule && !!state.forward && rule.serial === state.serial &&
    rule.local === `tcp:${state.forward.port}` && rule.remote === `localabstract:${state.forward.socket}`;
}

async function selectedDevice(expected, { allowAbsent = false } = {}) {
  const state = await adb(['-s', expected.serial, 'get-state'], { allowFailure: true });
  if (state !== 'device') {
    if (allowAbsent) return false;
    throw new Error(`${expected.serial} is not ready. Run start with the same --session; do not select another device.`);
  }
  const name = (await adb(['-s', expected.serial, 'emu', 'avd', 'name'])).split(/\r?\n/)[0].trim();
  if (name !== expected.avd) throw new Error(`Refusing ${expected.serial}: AVD is ${name}, expected ${expected.avd}. No app command sent.`);
  if ((await adb(['-s', expected.serial, 'shell', 'getprop', 'ro.kernel.qemu'])) !== '1') {
    throw new Error('Selected device does not identify as an emulator. Refusing app control.');
  }
  return true;
}

function pidAlive(pid) {
  if (!Number.isInteger(pid) || pid < 1) return true;
  try { process.kill(pid, 0); return true; }
  catch (error) { return error.code !== 'ESRCH'; }
}

async function commandLock(action) {
  if (process.platform !== 'win32') throw new Error('Use native Windows Node for this profile skill, not WSL.');
  await mkdir(STATE_DIR, { recursive: true });
  const name = 'Local\\HuckleberryAgent-' + createHash('sha256').update(STATE_DIR.toLowerCase()).digest('hex').slice(0, 24);
  // Windows releases an abandoned named mutex. The keeper exits on stdin EOF,
  // including if Node is killed; no stale-lock TTL, incomplete owner file, or PID
  // reuse can grant ownership. The JSON file is only status information.
  const keeper = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
    join(ROOT, 'scripts', 'command-lock.ps1'), '-Name', name], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  let acquired = false;
  let closing = false;
  let stderr = '';
  keeper.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
  keeper.stdin.on('error', () => {}); // EPIPE is normal when a non-owner gets BUSY.
  const lines = createInterface({ input: keeper.stdout });
  const ended = new Promise((resolve) => keeper.once('close', resolve));
  keeper.once('exit', () => {
    if (acquired && !closing) {
      console.error('Command mutex keeper was lost. Stopping this process; reconcile any in-flight write before retrying.');
      process.exit(1);
    }
  });
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Windows command mutex did not become ready in 10s.')), 10000);
      const finish = (error) => { clearTimeout(timer); error ? reject(error) : resolve(); };
      keeper.once('error', finish);
      keeper.once('exit', (code) => { if (!acquired) finish(new Error(code === 3
        ? 'Another command is using this session. Run controls sequentially, never concurrently.'
        : stderr.trim() || `Command lock keeper exited (${code}).`)); });
      lines.on('line', (line) => {
        if (line.trim() === 'LOCKED') { acquired = true; finish(); }
        else if (line.trim() === 'BUSY') finish(new Error('Another command is using this session. Run controls sequentially, never concurrently.'));
      });
    });
    const temporary = `${BUSY}.${process.pid}.tmp`;
    await writeFile(temporary, JSON.stringify({ pid: process.pid, keeperPid: keeper.pid, at: new Date().toISOString() }));
    await rename(temporary, BUSY);
    return await action();
  } finally {
    closing = true;
    if (acquired) await rm(BUSY, { force: true });
    keeper.stdin.end();
    // EOF normally releases immediately. Bound cleanup if PowerShell itself hangs.
    const timer = setTimeout(() => keeper.kill(), 3000);
    timer.unref();
    await ended;
    clearTimeout(timer);
    lines.close();
  }
}

export async function withSession(session, action, { create = false } = {}) {
  validateSession(session);
  return commandLock(async () => {
    if (create && !existsSync(CLAIM)) {
      const expected = await config();
      const staged = await mkdtemp(join(STATE_DIR, '.claim-'));
      try {
        await writeFile(join(staged, 'state.json'), JSON.stringify({ session, createdAt: new Date().toISOString(), ...expected, forward: null }));
        await rename(staged, CLAIM); // Publish a complete claim, never a half-written owner.
      } finally { await rm(staged, { recursive: true, force: true }); }
    }
    const state = await readState();
    if (!state) throw new Error(`No readable claim. Run start. If ${CLAIM} exists but is incomplete, inspect that exact directory and request user-approved recovery.`);
    if (state.session !== session) throw new Error(`Device claimed by session ${state.session}. Wait for it to release. If its task ended, obtain user confirmation before resuming/releasing that owner ID.`);
    return action(state);
  });
}

async function forwardRules() { return parseForwards(await adb(['forward', '--list'])); }

function forwardKey(rule) {
  return `${rule.serial}\n${rule.local}\n${rule.remote}`;
}

export function pendingForwardRemovals(rules, state) {
  const pending = state.pendingForward;
  if (!pending) return [];
  const baseline = new Set(pending.before.map(forwardKey));
  return rules.filter((rule) => rule.serial === state.serial &&
    rule.remote === `localabstract:${pending.socket}` &&
    !baseline.has(forwardKey(rule)));
}

async function reconcilePendingForward(state) {
  if (!state.pendingForward) return;
  const rules = await forwardRules();
  if (!rules.some((rule) => sameForward(rule, state))) {
    for (const rule of pendingForwardRemovals(rules, state)) {
      await adb(['-s', state.serial, 'forward', '--remove', rule.local]);
    }
  }
  state.pendingForward = null;
  await saveState(state);
}

export async function start(state) {
  if (process.platform !== 'win32') throw new Error('This profile skill requires native Windows Node/ADB, not WSL.');
  const expected = await config();
  if (state.serial !== expected.serial || state.avd !== expected.avd) throw new Error('Config changed during an active session. Release it before changing config.');
  if (!(await selectedDevice(expected, { allowAbsent: true }))) {
    const sdk = sdkRoot();
    if (!sdk) throw new Error('Android SDK emulator not found. Set ANDROID_HOME to the existing SDK; this skill does not install one.');
    const avdHome = join(homedir(), '.android', 'avd');
    if (!existsSync(join(avdHome, `${expected.avd}.ini`))) throw new Error(`Existing AVD ${expected.avd} not found under ${avdHome}. Do not create or wipe an AVD automatically.`);
    // Wait for an existing offline emulator instead of launching a duplicate.
    const devices = await adb(['devices']);
    if (!devices.split(/\r?\n/).some((line) => line.startsWith(`${expected.serial}\t`))) {
    const output = openSync(join(STATE_DIR, 'emulator.stdout.log'), 'a');
    const errors = openSync(join(STATE_DIR, 'emulator.stderr.log'), 'a');
    try {
      const child = spawn(join(sdk, 'emulator', 'emulator.exe'), [
        '-avd', expected.avd, '-port', String(expected.port), '-no-snapshot-save', '-no-audio', '-gpu', 'auto',
      ], { detached: true, stdio: ['ignore', output, errors], env: { ...process.env, ANDROID_AVD_HOME: avdHome } });
      await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
      child.unref();
    } finally { closeSync(output); closeSync(errors); }
    }
  }
  const deadline = Date.now() + BOOT_TIMEOUT_MS;
  let booted = false;
  while (Date.now() < deadline) {
    if (await selectedDevice(expected, { allowAbsent: true })) {
      booted = (await adb(['-s', state.serial, 'shell', 'getprop', 'sys.boot_completed'], { allowFailure: true })) === '1';
      if (booted) break;
    }
    await delay(1500);
  }
  if (!booted) throw new Error(`Android did not boot in ${BOOT_TIMEOUT_MS / 1000}s. Inspect ${STATE_DIR}/emulator.*.log. Claim retained for retry/release.`);
  if (!(await adb(['-s', state.serial, 'shell', 'pm', 'path', PACKAGE])).startsWith('package:')) {
    throw new Error('Huckleberry is not installed in this AVD. Install from a trusted source manually; no APK is bundled.');
  }
  await adb(['-s', state.serial, 'shell', 'am', 'start', '-n', ACTIVITY]);
  let socket;
  // The WebView can appear after Android reports the app process as running.
  const appDeadline = Date.now() + 45000;
  while (Date.now() < appDeadline) {
    const pid = (await adb(['-s', state.serial, 'shell', 'pidof', PACKAGE], { allowFailure: true })).split(/\s+/)[0];
    const sockets = await adb(['-s', state.serial, 'shell', 'cat', '/proc/net/unix']);
    if (/^\d+$/.test(pid) && sockets.includes(`@webview_devtools_remote_${pid}`)) {
      socket = `webview_devtools_remote_${pid}`;
      break;
    }
    await delay(750);
  }
  if (!socket) throw new Error('No Huckleberry WebView CDP socket after 45s. This app build may disable debugging. Stop and report this limitation; never patch the APK or silently substitute backend writes.');
  await reconcilePendingForward(state);
  if (state.forward) {
    const rule = (await forwardRules()).find((entry) => entry.local === `tcp:${state.forward.port}`);
    if (rule && !sameForward(rule, state)) throw new Error('Saved forwarding port is now owned by a different rule. Refusing to replace/remove it.');
    if (!rule || state.forward.socket !== socket) {
      if (rule) await adb(['-s', state.serial, 'forward', '--remove', rule.local]);
      state.forward = null;
      await saveState(state);
    }
  }
  if (!state.forward) {
    const before = (await forwardRules()).filter((rule) => rule.serial === state.serial);
    state.pendingForward = { socket, before };
    await saveState(state);
    const port = Number(await adb(['-s', state.serial, 'forward', 'tcp:0', `localabstract:${socket}`]));
    if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('ADB did not return a valid local debugging port.');
    state.forward = { port, socket };
    state.pendingForward = null;
    try { await saveState(state); }
    catch (error) { await adb(['-s', state.serial, 'forward', '--remove', `tcp:${port}`], { allowFailure: true }); throw error; }
  }
  let lastError;
  const targetDeadline = Date.now() + 30000;
  while (Date.now() < targetDeadline) {
    try { await targets(state); lastError = null; break; }
    catch (error) { lastError = error; await delay(500); }
  }
  if (lastError) throw lastError;
  return { session: state.session, serial: state.serial, avd: state.avd, endpoint: endpoint(state), stateDirectory: STATE_DIR,
    next: 'snapshot with the same --session. Confirm the intended child/profile before writes. Start does not navigate Home or dismiss modals.' };
}

export function endpoint(state) {
  if (!state.forward) throw new Error('Session has no CDP connection. Run start with the same --session.');
  return `http://127.0.0.1:${state.forward.port}`;
}

export async function targets(state) {
  const response = await fetch(`${endpoint(state)}/json/list`, { signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(`WebView discovery failed: HTTP ${response.status}. Retry start with the same --session.`);
  const list = await response.json();
  if (!Array.isArray(list) || !list.some((target) => target.type === 'page' && /^Huckleberry\b/.test(target.title))) {
    throw new Error('CDP endpoint does not contain a Huckleberry page. Stop; do not control another target.');
  }
  return list;
}

export async function validateAttachment(state) {
  await selectedDevice(state);
  const pid = (await adb(['-s', state.serial, 'shell', 'pidof', PACKAGE], { allowFailure: true })).split(/\s+/)[0];
  if (state.forward?.socket !== `webview_devtools_remote_${pid}`) throw new Error('App process changed. Run start with the same --session to reconnect safely.');
  if (!(await forwardRules()).some((rule) => sameForward(rule, state))) throw new Error('Session forwarding rule missing/changed. Run start with the same --session; do not replace other rules.');
  await targets(state);
  const window = await adb(['-s', state.serial, 'shell', 'dumpsys', 'window']);
  const focus = window.split(/\r?\n/).find((line) => /mCurrentFocus=/.test(line));
  if (!focus || !focus.includes(PACKAGE)) {
    throw new Error(`Huckleberry is not the focused Android window (${focus?.trim() || 'unknown focus'}). A native dialog may be blocking. Inspect an ADB screenshot; do not click the background WebView.`);
  }
}

export async function status() {
  const expected = await config();
  const online = await selectedDevice(expected, { allowAbsent: true });
  const state = await readState();
  let busy = null;
  try { busy = JSON.parse(await readFile(BUSY, 'utf8')); busy.processAlive = pidAlive(busy.pid); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  return { ...expected, online, adb: adbExecutable(), adbCompatibility: await compatibility, owner: state?.session ?? null, forward: state?.forward ?? null, busy, stateDirectory: STATE_DIR };
}

export async function release(state) {
  await reconcilePendingForward(state);
  if (state.forward) {
    const rule = (await forwardRules()).find((entry) => entry.local === `tcp:${state.forward.port}`);
    if (rule && !sameForward(rule, state)) throw new Error('Forward changed ownership. Refusing to remove it or silently release the claim.');
    if (rule) await adb(['-s', state.serial, 'forward', '--remove', rule.local]);
  }
  const tombstone = join(STATE_DIR, `.released-${randomUUID()}`);
  await rename(CLAIM, tombstone); // Atomically release; a crash cannot leave an empty active claim.
  await rm(tombstone, { recursive: true });
  return { released: state.session, emulatorLeftRunning: true, recordsChanged: false };
}
