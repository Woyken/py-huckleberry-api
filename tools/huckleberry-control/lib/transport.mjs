import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdtemp, readFile, unlink, rmdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { quote } from './protocol.mjs';
import { validateAdbEnvironment, protocolNumber, readServerProtocol } from './adb-safety.mjs';
import { verifyWorkProfile } from './identity.mjs';

const LIMIT = 2 * 1024 * 1024;

export function runProcess(program, args, options) {
  return new Promise((resolve, reject) => {
    const process = spawn(program, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    const stdout = [], stderr = [], combined = [];
    let size = 0, failure;
    const stop = error => {
      failure = error;
      clearTimeout(timer);
      process.kill();
      process.stdout.destroy(); process.stderr.destroy(); process.unref();
      reject(error); // Do not wait forever for an inherited rish pipe to close.
    };
    const timer = setTimeout(() => stop(new Error('Command timed out; reconcile uncertain actions')), options.timeout);
    const collect = (destination, chunk) => {
      size += chunk.length;
      if (size > options.maxBuffer) { stop(new Error('Command output exceeded limit')); return; }
      destination.push(chunk); combined.push(chunk);
    };
    process.stdout.on('data', chunk => collect(stdout, chunk));
    process.stderr.on('data', chunk => collect(stderr, chunk));
    process.on('error', error => { clearTimeout(timer); reject(error); });
    process.on('close', (code, signal) => {
      clearTimeout(timer);
      if (failure || code !== 0) return reject(failure || new Error(`Command failed (${signal || code})`));
      resolve({ stdout: Buffer.concat(stdout).toString('utf8'), stderr: Buffer.concat(stderr).toString('utf8'),
        combined: Buffer.concat(combined).toString('utf8') });
    });
  });
}

export function verifyTransfer(bytes, expectedSize, expectedHash) {
  if (bytes.length !== expectedSize || createHash('sha256').update(bytes).digest('hex') !== expectedHash) {
    throw new Error('Android result transfer was incomplete or corrupt; never replay an uncertain action');
  }
  return bytes;
}

export class Transport {
  constructor(profile, run = runProcess) {
    this.profile = profile;
    this.run = run;
    this.executable = profile.executable || (profile.transport === 'rish' ? 'rish' : process.platform === 'win32' ? 'adb.exe' : 'adb');
    this.ready = false;
  }

  async command(args, timeout = 15000) {
    const result = await this.run(this.executable, args, { timeout, maxBuffer: LIMIT, windowsHide: true, encoding: 'utf8' });
    // Shizuku/rish can forward shell builtins and child output to different host
    // streams. Large payloads are transferred separately and checksum-verified.
    return this.profile.transport === 'rish' ? result.combined ?? result.stdout + (result.stderr || '') : result.stdout;
  }

  async shell(script, timeout = 15000) {
    // An explicit remote `exit` can race rish's forwarding threads. Send a
    // completion record and drain instead; missing records fail closed.
    const marker = `HB_EXIT_${randomUUID().replaceAll('-', '')}`;
    const wrapped = `/system/bin/sh -c ${quote(script)}; result=$?; printf '\\n${marker}=%s\\n' "$result"; sleep 1`;
    const output = await this.command(this.profile.transport === 'rish' ? ['-c', wrapped] : ['-s', this.profile.serial, 'shell', wrapped], timeout);
    const matches = [...output.matchAll(new RegExp(`(?:^|\\n)${marker}=(\\d+)\\r?\\n`, 'g'))];
    if (matches.length !== 1) throw new Error('Missing Android command completion; reconcile any uncertain action');
    const match = matches[0];
    if (Number(match[1]) !== 0) throw new Error(`Android command failed (${match[1]})`);
    return output.slice(0, match.index) + output.slice(match.index + match[0].length);
  }

  async verify() {
    if (this.profile.transport === 'adb') {
      validateAdbEnvironment();
      // Query an existing server without invoking a client that might restart it.
      const server = await readServerProtocol();
      const client = protocolNumber(await this.command(['version']));
      if (server !== null && server !== client) throw new Error('ADB wire protocols differ; refusing to restart the shared server');
      if ((await this.command(['-s', this.profile.serial, 'get-state'])).trim() !== 'device') throw new Error('Configured emulator is not online');
      const name = (await this.command(['-s', this.profile.serial, 'emu', 'avd', 'name'])).split(/\r?\n/)[0].trim();
      if (name !== this.profile.avd) throw new Error('Configured serial belongs to a different AVD');
      if ((await this.shell('/system/bin/getprop ro.kernel.qemu')).trim() !== '1') throw new Error('Target does not identify as an emulator');
    } else {
      const uid = (await this.shell('/system/bin/id -u')).trim();
      if (uid !== '2000') throw new Error(`rish must provide Android shell UID 2000 (received ${JSON.stringify(uid)})`);
      // Only retrieve the enrolled user's header/type, not the full user-policy dump.
      verifyWorkProfile(await this.capture(`/system/bin/dumpsys user | /system/bin/grep -A 8 ${quote(`UserInfo{${this.profile.androidUser}:`)}`), this.profile.androidUser);
    }
    this.ready = true;
  }

  async capture(script) {
    // Large streamed rish output has been unreliable. Keep it shell-private and
    // transfer bounded base64 chunks, then verify its complete length and hash.
    const remote = `/data/local/tmp/hb-debug-${randomUUID()}.txt`;
    const packed = `${remote}.gz`;
    const transferMarker = `HB_FILE_${randomUUID().replaceAll('-', '')}`;
    const frameMarker = `HB_FRAME_${randomUUID().replaceAll('-', '')}`;
    const chunkSize = 4096;
    try {
      if (this.profile.transport === 'rish') {
        // One shell session emits metadata and self-identifying frames. Shell
        // command substitution keeps external base64 output out of the
        // forwarding pipes; every complete frame is emitted by one printf.
        const output = await this.shell(`umask 077
trap '/system/bin/rm -f ${remote} ${packed}' EXIT
${script} > ${quote(remote)}
result=$?
if [ "$result" -ne 0 ]; then /system/bin/rm -f ${quote(remote)}; exit "$result"; fi
/system/bin/gzip -c ${quote(remote)} > ${quote(packed)} || exit 1
rawsize=$(wc -c < ${quote(remote)})
size=$(wc -c < ${quote(packed)})
hash=$(sha256sum ${quote(packed)})
printf '${transferMarker} %s %s %s\n' "$rawsize" "$size" "\${hash%% *}"
count=$(( (size + ${chunkSize} - 1) / ${chunkSize} ))
i=0
while [ "$i" -lt "$count" ]; do
part=$(/system/bin/dd if=${quote(packed)} bs=${chunkSize} skip="$i" count=1 2>/dev/null | /system/bin/base64 -w 0)
printf '${frameMarker} %s %s\n' "$i" "$part"
i=$((i+1))
done`, 60000);
        const match = new RegExp(`^${transferMarker}\\s+(\\d+)\\s+(\\d+)\\s+([0-9a-f]{64})\\s*$`, 'm').exec(output);
        if (!match || Number(match[1]) > LIMIT || Number(match[2]) > LIMIT) throw new Error('Invalid or oversized Android result');
        const size = Number(match[2]);
        const frames = [...output.matchAll(new RegExp(`^${frameMarker} (\\d+) ([A-Za-z0-9+/]*={0,2})\\r?$`, 'gm'))];
        const byIndex = new Map();
        for (const frame of frames) {
          const index = Number(frame[1]);
          if (index >= Math.ceil(size / chunkSize) || byIndex.has(index)) throw new Error('Duplicate or invalid Android transfer frame');
          byIndex.set(index, frame[2]);
        }
        if (byIndex.size !== Math.ceil(size / chunkSize)) throw new Error('Missing Android transfer frames');
        const parts = [];
        for (let index = 0; index < byIndex.size; index++) {
          const encoded = byIndex.get(index);
          const part = Buffer.from(encoded, 'base64');
          if (part.toString('base64') !== encoded || part.length !== Math.min(chunkSize, size - index * chunkSize)) throw new Error('Truncated Android transfer chunk');
          parts.push(part);
        }
        const bytes = Buffer.concat(parts);
        verifyTransfer(bytes, size, match[3]);
        const uncompressed = gunzipSync(bytes, { maxOutputLength: LIMIT });
        if (uncompressed.length !== Number(match[1])) throw new Error('Android result decompressed length mismatch');
        return uncompressed.toString('utf8');
      }
      const metadata = await this.shell(`umask 077
${script} > ${quote(remote)}
result=$?
if [ "$result" -ne 0 ]; then exit "$result"; fi
/system/bin/gzip -c ${quote(remote)} > ${quote(packed)} || exit 1
rawsize=$(wc -c < ${quote(remote)})
size=$(wc -c < ${quote(packed)})
hash=$(sha256sum ${quote(packed)})
printf '${transferMarker} %s %s %s\n' "$rawsize" "$size" "\${hash%% *}"`, 20000);
      const match = new RegExp(`^${transferMarker}\\s+(\\d+)\\s+(\\d+)\\s+([0-9a-f]{64})\\s*$`, 'm').exec(metadata);
      if (!match || Number(match[1]) > LIMIT || Number(match[2]) > LIMIT) throw new Error('Invalid or oversized Android result');
      const size = Number(match[2]);
      const directory = await mkdtemp(join(tmpdir(), 'hb-debug-'));
      const path = join(directory, 'reply.txt');
      let bytes;
      try {
        await this.command(['-s', this.profile.serial, 'pull', packed, path], 20000);
        bytes = await readFile(path);
      } finally {
        await unlink(path).catch(error => { if (error.code !== 'ENOENT') throw error; });
        await rmdir(directory);
      }
      verifyTransfer(bytes, size, match[3]);
      const uncompressed = gunzipSync(bytes, { maxOutputLength: LIMIT });
      if (uncompressed.length !== Number(match[1])) throw new Error('Android result decompressed length mismatch');
      return uncompressed.toString('utf8');
    } finally {
      // Only remove the exact per-request file this instance generated.
      if (this.profile.transport === 'adb') await this.shell(`/system/bin/rm -f ${quote(remote)} ${quote(packed)}`).catch(() => {});
    }
  }
}

// rish startup dominates short accessibility commands. Keep one interactive
// shell for the lifetime of this process so a multi-step flow pays the
// Shizuku/Java startup once instead of once per shell call.
export class PersistentTransport extends Transport {
  constructor(profile, spawnProcess = spawn) {
    super(profile);
    if (profile.transport !== 'rish') throw new Error('PersistentTransport requires the rish transport');
    this.spawnProcess = spawnProcess;
    this.child = null;
    this.pending = null;
    this.buffer = '';
    this.queue = Promise.resolve();
    this.closed = false;
  }

  async #start() {
    if (this.child) return;
    if (this.closed) throw new Error('rish session is closed; create a new transport');
    const child = this.spawnProcess(this.executable, [],
      { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    this.child = child;
    this.buffer = '';
    child.stdout.on('data', chunk => this.#onData(chunk));
    child.stderr.on('data', chunk => this.#onData(chunk));
    child.on('error', error => this.#fail(error));
    child.on('close', (code, signal) =>
      this.#fail(new Error(`rish session closed (${signal || code})`)));
    await new Promise((resolve, reject) => {
      child.once('spawn', resolve);
      child.once('error', reject);
    });
  }

  #onData(chunk) {
    // Data from a previous command is not part of the next reply. If no
    // command is pending, it is stray output and is deliberately discarded.
    if (!this.pending) return;
    this.buffer += chunk.toString('utf8');
    if (this.buffer.length > LIMIT) {
      this.#fail(new Error('rish output exceeded limit'));
      return;
    }
    this.#match();
  }

  #match() {
    const pending = this.pending;
    if (!pending) return;
    const endIndex = this.buffer.indexOf(`${pending.end}=`);
    if (endIndex < 0) return;
    const lineEnd = this.buffer.indexOf('\n', endIndex);
    if (lineEnd < 0) return;
    const beginIndex = this.buffer.indexOf(pending.begin);
    if (beginIndex < 0) {
      this.#fail(new Error('Missing rish command start marker; reconcile any uncertain action'));
      return;
    }
    const code = this.buffer.slice(endIndex + pending.end.length + 1, lineEnd).trim();
    const output = this.buffer.slice(beginIndex + pending.begin.length, endIndex)
      .replace(/^\r?\n/, '');
    this.buffer = this.buffer.slice(lineEnd + 1);
    this.pending = null;
    clearTimeout(pending.timer);
    if (Number(code) !== 0) pending.reject(new Error(`Android command failed (${code || 'unknown'})`));
    else pending.resolve(output);
  }

  #fail(error) {
    const pending = this.pending;
    this.pending = null;
    this.buffer = '';
    this.closed = true;
    if (pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    if (this.child) {
      try { this.child.kill(); } catch {}
      this.child = null;
    }
  }

  async #run(script, timeout) {
    if (this.closed) throw new Error('rish session is closed; create a new transport');
    await this.#start();
    const id = randomUUID().replaceAll('-', '');
    const begin = `HB_BEGIN_${id}`;
    const end = `HB_END_${id}`;
    // The inner shell isolates a command's exit/trap from the long-lived
    // session. The outer shell still writes the completion record.
    const wrapped = `printf '\\n${begin}\\n'; /system/bin/sh -c ${quote(script)}; `
      + `result=$?; printf '\\n${end}=%s\\n' "$result"`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#fail(new Error('Command timed out; reconcile uncertain actions'));
      }, timeout);
      this.pending = { begin, end, resolve, reject, timer };
      this.child.stdin.write(`${wrapped}\n`, error => {
        if (error && this.pending) this.#fail(error);
      });
    });
  }

  shell(script, timeout = 15000) {
    const run = () => this.#run(script, timeout);
    const result = this.queue.then(run, run);
    this.queue = result.catch(() => {});
    return result;
  }

  close() {
    this.closed = true;
    const pending = this.pending;
    this.pending = null;
    this.buffer = '';
    if (pending) {
      clearTimeout(pending.timer);
      pending.reject(new Error('rish session closed'));
    }
    const child = this.child;
    this.child = null;
    if (!child) return;
    try { child.stdin.end(); } catch {}
    const timer = setTimeout(() => { try { child.kill(); } catch {} }, 100);
    timer.unref?.();
  }
}
