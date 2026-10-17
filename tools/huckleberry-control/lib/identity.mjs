import { PACKAGE } from './protocol.mjs';

export function parseIdentity(activity, window, profile) {
  const entries = [...activity.matchAll(/(?:topResumedActivity|mResumedActivity)[^\n]*?\bu(\d+)\s+(\S+)\s+t(\d+)\b/g)]
    .map(match => ({ user: Number(match[1]), component: match[2], task: Number(match[3]) }));
  const unique = new Map(entries.map(entry => [JSON.stringify(entry), entry]));
  if (unique.size !== 1) throw new Error('Missing or ambiguous resumed activity');
  const active = [...unique.values()][0];
  const components = [`${PACKAGE}/.MainActivity`, `${PACKAGE}/${PACKAGE}.MainActivity`];
  if (active.user !== profile.androidUser || !components.includes(active.component) || active.task <= 0) {
    throw new Error('Refusing wrong Android user, app, or task');
  }
  const focus = /^\s*mCurrentFocus=Window\{[^\n]*?\bu(\d+)\s+(\S+)\}/m.exec(window);
  if (!focus || Number(focus[1]) !== active.user || !components.includes(focus[2])) throw new Error('Huckleberry is not the focused window in the enrolled user');
  return active;
}

export function verifyWorkProfile(users, androidUser) {
  const blocks = users.split(/(?=^\s*UserInfo\{\d+:)/m);
  const block = blocks.find(text => new RegExp(`^\\s*UserInfo\\{${androidUser}:`, 'm').test(text));
  if (!block || !/Type:\s*android\.os\.usertype\.profile\.MANAGED\b/.test(block)) {
    throw new Error('Enrolled rish target is not a verified managed work profile');
  }
}
