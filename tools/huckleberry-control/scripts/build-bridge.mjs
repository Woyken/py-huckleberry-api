// Developer-only APK build. No app installation, settings edits or bundled key.
import { execFile } from 'node:child_process';
import { promisify, parseArgs } from 'node:util';
import { mkdir, readdir, stat, mkdtemp, writeFile } from 'node:fs/promises';
import { resolve, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';

const exec = promisify(execFile);
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const { values } = parseArgs({ options: {
  sdk: { type: 'string' }, 'build-tools': { type: 'string', default: '35.0.0' },
  platform: { type: 'string', default: 'android-35' }, aapt2: { type: 'string' }, zipalign: { type: 'string' },
  keystore: { type: 'string' }, 'password-file': { type: 'string' }, out: { type: 'string' }, help: { type: 'boolean' },
} });
if (values.help) {
  console.log('Build-only debug APK: --sdk <Android SDK> [--aapt2 <native-tool>] [--zipalign <native-tool>] [--keystore <private-key> --password-file <private-file>] [--out <new.apk>]. Never installs or enables the service.');
} else {
  const sdk = values.sdk || process.env.ANDROID_SDK_ROOT || process.env.ANDROID_HOME;
  if (!sdk) throw new Error('--sdk or ANDROID_SDK_ROOT is required');
  if (!!values.keystore !== !!values['password-file']) throw new Error('Supply both --keystore and --password-file, or neither for an unsigned build');
  if (!/^[A-Za-z0-9_.-]+$/.test(values['build-tools']) || !/^android-\d+$/.test(values.platform)) throw new Error('Invalid SDK version');
  const tools = join(resolve(sdk), 'build-tools', values['build-tools']);
  const androidJar = join(resolve(sdk), 'platforms', values.platform, 'android.jar');
  const suffix = process.platform === 'win32' ? '.exe' : '';
  const aapt2 = values.aapt2 || join(tools, `aapt2${suffix}`);
  const zipalign = values.zipalign || join(tools, `zipalign${suffix}`);
  const run = async (program, args) => {
    try { await exec(program, args, { timeout: 120000, maxBuffer: 2 * 1024 * 1024, windowsHide: true }); }
    catch (error) { throw new Error(`${program} failed: ${error.stderr || error.message}`); }
  };
  const buildRoot = join(root, 'android', 'build');
  await mkdir(buildRoot, { recursive: true });
  const work = await mkdtemp(join(buildRoot, 'compile-'));
  const classes = join(work, 'classes'), dex = join(work, 'dex'), generated = join(work, 'generated');
  for (const path of [classes, dex, generated]) await mkdir(path);
  const sourceDir = join(root, 'android', 'src', 'dev', 'local', 'huckleberry', 'a11yprobe');
  const sources = (await readdir(sourceDir)).filter(name => name.endsWith('.java')).map(name => join(sourceDir, name));
  const resources = join(work, 'resources.zip'), unsigned = join(work, 'unsigned.apk'), aligned = join(work, 'aligned.apk');
  await run(aapt2, ['compile', '--dir', join(root, 'android', 'res'), '-o', resources]);
  await run(aapt2, ['link', '-o', unsigned, '--manifest', join(root, 'android', 'AndroidManifest.xml'), '-I', androidJar, '--java', generated, resources]);
  await run(`javac${suffix}`, ['--release', '8', '-cp', androidJar, '-d', classes, ...sources]);
  const code = join(work, 'code.jar');
  await run(`jar${suffix}`, ['cf', code, '-C', classes, '.']);
  await run(`java${suffix}`, ['-cp', join(tools, 'lib', 'd8.jar'), 'com.android.tools.r8.D8', '--min-api', '28', '--lib', androidJar, '--output', dex, code]);
  await run(`jar${suffix}`, ['uf', unsigned, '-C', dex, 'classes.dex']);
  await run(zipalign, ['-f', '4', unsigned, aligned]);
  let artifact = aligned;
  if (values.keystore) {
    // Password is read by apksigner from the private file, never printed or stored here.
    artifact = join(work, 'signed.apk');
    const signer = join(tools, 'lib', 'apksigner.jar');
    await run(`java${suffix}`, ['-jar', signer, 'sign', '--ks', resolve(values.keystore), '--ks-pass', `file:${resolve(values['password-file'])}`, '--out', artifact, aligned]);
    await run(`java${suffix}`, ['-jar', signer, 'verify', artifact]);
  }
  const out = resolve(values.out || join(buildRoot, values.keystore ? 'bridge-debug.apk' : 'bridge-unsigned.apk'));
  if (await stat(out).then(() => true, error => { if (error.code === 'ENOENT') return false; throw error; })) throw new Error('Output exists; choose a new --out path');
  const bytes = await readFile(artifact);
  await writeFile(out, bytes, { flag: 'wx', mode: 0o600 });
  console.log(JSON.stringify({ artifact: out, signed: !!values.keystore, sha256: createHash('sha256').update(bytes).digest('hex'), installed: false }, null, 2));
}
