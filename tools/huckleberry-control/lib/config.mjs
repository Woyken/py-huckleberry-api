import { readFile } from 'node:fs/promises';
import { integer } from './protocol.mjs';

export function validateProfile(value) {
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Profile must be an object');
  const keys = ['transport', 'serial', 'avd', 'androidUser', 'executable'];
  if (Object.keys(value).some(key => !keys.includes(key))) throw new Error('Unknown profile field');
  integer(value.androidUser, 'androidUser');
  if (value.executable !== undefined && (typeof value.executable !== 'string' || !value.executable.trim() || /[\x00-\x1f]/.test(value.executable))) {
    throw new Error('Invalid executable path');
  }
  if (value.transport === 'rish') {
    if (value.androidUser === 0) throw new Error('Termux mode targets an explicitly enrolled work profile, never personal user 0');
    if (value.serial !== undefined || value.avd !== undefined) throw new Error('ADB fields are not valid for rish');
  } else if (value.transport === 'adb') {
    const match = /^emulator-(\d+)$/.exec(value.serial || '');
    const port = Number(match?.[1]);
    if (!match || port < 5554 || port > 5682 || port % 2) throw new Error('ADB mode requires an explicit even-port emulator serial');
    if (typeof value.avd !== 'string' || !/^[A-Za-z0-9_.-]+$/.test(value.avd) || /YOUR_/.test(value.avd)) throw new Error('Configure an existing AVD name');
  } else throw new Error('Explicit transport must be adb or rish; no automatic fallback');
  return Object.freeze({ ...value });
}

export async function loadProfile(path) {
  if (!path) throw new Error('--profile <private-local-config.json> is required');
  return validateProfile(JSON.parse(await readFile(path, 'utf8')));
}
