import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { createServer } from 'node:net';
import { EventEmitter, once } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import { PersistentTransport, Transport, verifyTransfer, runProcess } from '../lib/transport.mjs';
import { protocolNumber, readServerProtocol, validateAdbEnvironment } from '../lib/adb-safety.mjs';

test('both adapters execute the same Android script without a host shell', async () => {
  const calls = [];
  const run = async (file, args, options) => {
    calls.push({ file, args, options });
    const marker = /HB_EXIT_[a-f0-9]+/.exec(args.at(-1))[0];
    return { stdout: `ok\n${marker}=0\n` };
  };
  const windows = new Transport({ transport: 'adb', serial: 'emulator-5580', executable: 'C:\\SDK Space\\adb.exe' }, run);
  const termux = new Transport({ transport: 'rish', executable: '/native/rish' }, run);
  await windows.shell('content call --method status');
  await termux.shell('content call --method status');
  assert.equal(calls[0].file, 'C:\\SDK Space\\adb.exe');
  assert.deepEqual(calls[0].args.slice(0, 3), ['-s', 'emulator-5580', 'shell']);
  assert.equal(calls[0].args[3].replace(/HB_EXIT_[a-f0-9]+/g, 'MARKER'), calls[1].args[1].replace(/HB_EXIT_[a-f0-9]+/g, 'MARKER'));
  assert.equal(calls[0].options.shell, undefined);
  assert.equal(calls[1].args[0], '-c');
});

test('bounded rish transfers verify length, SHA-256 and canonical base64', async () => {
  const original = randomBytes(10000).toString('hex');
  const bytes = gzipSync(original);
  const hash = createHash('sha256').update(bytes).digest('hex');
  const chunk = 4096;
  const transport = new Transport({ transport: 'rish' });
  const commands = [];
  transport.shell = async script => {
    commands.push(script);
    if (!script.includes('wc -c') || !script.includes('/system/bin/dd')) return '';
    const marker = /HB_FRAME_[a-f0-9]+/.exec(script)[0];
    const metadata = `${/HB_FILE_[a-f0-9]+/.exec(script)[0]} ${Buffer.byteLength(original)} ${bytes.length} ${hash}\n`;
    return metadata + Array.from({ length: Math.ceil(bytes.length / chunk) }, (_, index) =>
      `${marker} ${index} ${bytes.subarray(index * chunk, (index + 1) * chunk).toString('base64')}\n`).reverse().join('');
  };
  assert.equal(await transport.capture('content call'), original);
  assert.equal(commands.length, 1);
  assert.equal(commands.filter(command => command.includes('/system/bin/dd')).length, 1);
  assert.match(commands[0], /\/system\/bin\/base64 -w 0/);
  assert.match(commands[0], /\/system\/bin\/rm -f .*hb-debug-[a-f0-9-]+\.txt.*hb-debug-[a-f0-9-]+\.txt\.gz/);
  assert.throws(() => verifyTransfer(bytes.subarray(1), bytes.length, hash));
  assert.throws(() => verifyTransfer(bytes, bytes.length, '0'.repeat(64)));
});

test('corrupt/truncated transfer still removes only its generated file', async () => {
  const transport = new Transport({ transport: 'rish' });
  const calls = [];
  transport.shell = async script => {
    calls.push(script);
    return script.includes('wc -c') ? `${/HB_FILE_[a-f0-9]+/.exec(script)[0]} 10 10 ${'a'.repeat(64)}\n` : 'bad chunk!';
  };
  await assert.rejects(transport.capture('command'), /frames/);
  assert.match(calls.at(-1), /rm -f/);
});

test('rish completion records work even when forwarded through stderr', async () => {
  for (const reversed of [false, true]) {
    const transport = new Transport({ transport: 'rish' }, async (file, args) => {
      const marker = /HB_EXIT_[a-f0-9]+/.exec(args[1])[0];
      return reversed ? { stdout: `\n${marker}=0\n`, stderr: '2000\n' }
        : { stdout: '2000\n', stderr: `\n${marker}=0\n` };
    });
    assert.equal((await transport.shell('id -u')).trim(), '2000');
  }
});

test('read-only ADB server protocol query never invokes an ADB client', async () => {
  let request;
  const server = createServer(socket => socket.once('data', bytes => {
    request = bytes.toString(); socket.end('OKAY00040029');
  }));
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  try {
    assert.equal(await readServerProtocol(server.address().port), 41);
    assert.equal(request, '000chost:version');
  } finally { server.close(); await once(server, 'close'); }
  assert.equal(protocolNumber('Android Debug Bridge version 1.0.41\nVersion 34.0.4'), 41);
  assert.throws(() => protocolNumber('unexpected'));
  assert.throws(() => validateAdbEnvironment({ ADB_SERVER_SOCKET: 'remote' }));
});

test('process timeouts reject without waiting for inherited output pipes', async () => {
  const start = Date.now();
  await assert.rejects(runProcess(process.execPath, ['-e', 'setTimeout(()=>{},15000)'],
    { timeout: 100, maxBuffer: 1024 }), /timed out/);
  assert.ok(Date.now() - start < 5000);
});

test('missing/nonzero command completion records are errors, not empty successes', async () => {
  const missing = new Transport({ transport: 'rish' }, async () => ({ stdout: '', stderr: '' }));
  await assert.rejects(missing.shell('id -u'), /completion/);
  const failed = new Transport({ transport: 'rish' }, async (file, args) => ({
    stdout: `\n${/HB_EXIT_[a-f0-9]+/.exec(args[1])[0]}=1\n`, stderr: '',
  }));
  await assert.rejects(failed.shell('false'), /failed/);
});

function fakeRish({ response = '2000', code = 0, delay = 0 } = {}) {
  let spawns = 0;
  const writes = [];
  const spawnProcess = () => {
    spawns += 1;
    const child = new EventEmitter();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.stdin = new Writable({
      write(chunk, encoding, callback) {
        const text = chunk.toString();
        writes.push(text);
        const begin = /HB_BEGIN_[a-f0-9]+/.exec(text);
        const end = /HB_END_[a-f0-9]+/.exec(text);
        if (begin && end) {
          const timer = setTimeout(() => {
            child.stdout.write(`\n${begin[0]}\n${response}\n${end[0]}=${code}\n`);
          }, delay);
          timer.unref?.();
        }
        callback();
      },
    });
    child.kill = () => { child.emit('close', 0, 'SIGTERM'); };
    queueMicrotask(() => child.emit('spawn'));
    return child;
  };
  return { spawnProcess, writes, spawns: () => spawns };
}

test('persistent rish transport reuses one shell for sequential commands', async () => {
  const fake = fakeRish();
  const transport = new PersistentTransport({ transport: 'rish', executable: 'rish' }, fake.spawnProcess);
  assert.equal((await transport.shell('id -u')).trim(), '2000');
  assert.equal((await transport.shell('/system/bin/dumpsys window')).trim(), '2000');
  assert.equal(fake.spawns(), 1);
  assert.equal(fake.writes.length, 2);
  assert.match(fake.writes[0], /HB_BEGIN_[a-f0-9]+/);
  assert.match(fake.writes[0], /\/system\/bin\/sh -c/);
  transport.close();
});

test('persistent rish transport keeps a failed command separate from the next', async () => {
  const fake = fakeRish({ code: 1 });
  const transport = new PersistentTransport({ transport: 'rish', executable: 'rish' }, fake.spawnProcess);
  await assert.rejects(transport.shell('false'), /failed/);
  assert.equal(transport.closed, false);
  transport.close();
});

test('persistent rish transport fails closed on timeout', async () => {
  const fake = fakeRish({ delay: 1000 });
  const transport = new PersistentTransport({ transport: 'rish', executable: 'rish' }, fake.spawnProcess);
  await assert.rejects(transport.shell('slow', 5), /timed out/);
  assert.equal(transport.closed, true);
});
