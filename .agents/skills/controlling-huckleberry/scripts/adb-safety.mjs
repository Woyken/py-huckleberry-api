import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createConnection } from 'node:net';
const exec = promisify(execFile);

export function validateAdbEnvironment(env = process.env) {
  const overrides = ['ADB_SERVER_SOCKET', 'ANDROID_ADB_SERVER_ADDRESS', 'ANDROID_ADB_SERVER_PORT'].filter((key) => env[key]);
  if (overrides.length) throw new Error(`This skill requires the default local ADB server. Clear ${overrides.join(', ')} for this process only before using it; no remote/custom server is controlled.`);
}

export function protocolNumber(version) {
  const match = /Android Debug Bridge version \d+\.\d+\.(\d+)/.exec(version);
  if (!match) throw new Error('Could not determine the ADB wire protocol from adb version.');
  return Number(match[1]);
}

// Query the shared server without invoking a client that might restart it.
export function readServerProtocol(port = 5037) {
  return new Promise((resolve, reject) => {
    const socket = createConnection({ host: '127.0.0.1', port });
    let bytes = Buffer.alloc(0);
    const finish = (error, value) => { socket.destroy(); error ? reject(error) : resolve(value); };
    socket.setTimeout(2000, () => finish(new Error('Shared ADB server did not answer its read-only protocol query in 2s.')));
    socket.once('connect', () => socket.write('000chost:version'));
    socket.on('error', (error) => error.code === 'ECONNREFUSED' ? finish(null, null) : finish(error));
    socket.on('data', (chunk) => {
      bytes = Buffer.concat([bytes, chunk]);
      if (bytes.length < 8) return;
      const length = Number.parseInt(bytes.toString('ascii', 4, 8), 16);
      if (bytes.toString('ascii', 0, 4) !== 'OKAY' || !Number.isInteger(length) || length > 64) {
        finish(new Error('Unexpected shared ADB server protocol response; refusing to risk a restart.'));
      } else if (bytes.length >= 8 + length) {
        const protocol = Number.parseInt(bytes.toString('ascii', 8, 8 + length), 16);
        Number.isInteger(protocol) ? finish(null, protocol) : finish(new Error('Invalid ADB server version.'));
      }
    });
    socket.once('end', () => { if (!socket.destroyed) finish(new Error('ADB server closed its protocol query early.')); });
  });
}

export function verifyProtocols(selected, path, server) {
  if (path !== null && selected !== path) throw new Error('SDK/PATH ADB wire protocols differ. Resolve the toolchain mismatch manually; this skill will not restart the shared server.');
  if (server !== null && selected !== server) throw new Error('Selected ADB client/server wire protocols differ. Refusing device commands that could restart the shared server.');
}

export async function adbCompatibility(selected) {
  validateAdbEnvironment();
  const version = async (file) => (await exec(file, ['version'], { timeout: 5000, windowsHide: true })).stdout.trim();
  const selectedVersion = await version(selected);
  let pathAdb = null;
  try { pathAdb = (await exec('where.exe', ['adb.exe'], { timeout: 5000, windowsHide: true })).stdout.split(/\r?\n/).find(Boolean) ?? null; }
  catch (error) { if (error.code !== 1) throw error; }
  const pathVersion = pathAdb ? await version(pathAdb) : null;
  const selectedProtocol = protocolNumber(selectedVersion);
  const pathProtocol = pathVersion ? protocolNumber(pathVersion) : null;
  const serverProtocol = await readServerProtocol();
  verifyProtocols(selectedProtocol, pathProtocol, serverProtocol);
  return { selected, selectedVersion, pathAdb, pathVersion, selectedProtocol, pathProtocol, serverProtocol,
    note: pathVersion && pathVersion !== selectedVersion ? 'Client builds differ but their wire protocols match; no server restart is requested.' : null };
}
