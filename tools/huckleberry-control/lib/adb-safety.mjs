import { createConnection } from 'node:net';

export function validateAdbEnvironment(env = process.env) {
  for (const key of ['ADB_SERVER_SOCKET', 'ANDROID_ADB_SERVER_ADDRESS', 'ANDROID_ADB_SERVER_PORT']) {
    if (env[key]) throw new Error(`Refusing non-default ADB server (${key})`);
  }
}

export function protocolNumber(text) {
  const match = /Android Debug Bridge version 1\.0\.(\d+)/.exec(text);
  if (!match) throw new Error('Unrecognized ADB client protocol');
  return Number(match[1]);
}

export function readServerProtocol(port = 5037) {
  return new Promise((resolve, reject) => {
    const socket = createConnection({ host: '127.0.0.1', port });
    let data = Buffer.alloc(0), settled = false;
    const finish = (error, result) => {
      if (settled) return;
      settled = true; socket.destroy();
      error ? reject(error) : resolve(result);
    };
    socket.setTimeout(2000, () => finish(new Error('ADB server version query timed out')));
    socket.on('connect', () => socket.write('000chost:version'));
    socket.on('error', error => error.code === 'ECONNREFUSED' ? finish(null, null) : finish(error));
    socket.on('data', chunk => {
      data = Buffer.concat([data, chunk]);
      if (data.length > 128) return finish(new Error('Invalid ADB server response'));
      if (data.length < 8) return;
      const header = data.subarray(0, 8).toString();
      if (!/^OKAY[0-9a-fA-F]{4}$/.test(header)) return finish(new Error('ADB server rejected version query'));
      const size = parseInt(header.slice(4), 16);
      if (size !== 4) return finish(new Error('Invalid ADB server version size'));
      if (data.length >= 12) {
        const version = data.subarray(8, 12).toString();
        if (!/^[0-9a-fA-F]{4}$/.test(version)) return finish(new Error('Invalid ADB server version'));
        finish(null, parseInt(version, 16));
      }
    });
    socket.on('end', () => { if (!settled) finish(new Error('Incomplete ADB server response')); });
  });
}
