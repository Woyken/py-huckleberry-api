export const PROTOCOL = 2;
export const PACKAGE = 'com.huckleberry_labs.app';
export const BRIDGE = 'dev.local.huckleberry.a11yprobe';
export const URI = `content://${BRIDGE}`;
// Selector implementation stays in the Android bridge, shared by both hosts.
export const TABS = Object.freeze({ home: true, reports: true });
export const ACTIONS = Object.freeze({
  click: true, 'long-click': true, 'set-text': true,
  'scroll-forward': true, 'scroll-backward': true,
  focus: true, select: true, back: true,
});

export function integer(value, name, minimum = 0) {
  if (!Number.isSafeInteger(value) || value < minimum || value > 2147483647) throw new Error(`Invalid ${name}`);
  return value;
}

export function identifier(value, name = 'session', minimum = 3) {
  if (typeof value !== 'string' || value.length < minimum || value.length > 80 || !/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(value)) {
    throw new Error(`${name} must contain ${minimum}–80 letters, digits, underscores or hyphens`);
  }
  return value;
}

export function childName(value) {
  if (typeof value !== 'string' || !value.trim() || value !== value.trim() || value.length > 128 || /[\x00-\x1f\x7f]/.test(value)) {
    throw new Error('An explicit expectedChild is required');
  }
  if (/YOUR_|SET_ME/.test(value)) throw new Error('Replace the example profile placeholders first');
  return value;
}

export function quote(value) {
  if (typeof value !== 'string' || value.includes('\0')) throw new Error('Invalid shell argument');
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export function contentArguments(request) {
  const allowed = {
    status: [], claim: ['session', 'user', 'child'], release: ['session'], 'action-status': ['session', 'action_id'],
    tree: ['session', 'user', 'child', 'task'],
    action: ['session', 'user', 'child', 'task', 'action_id', 'path', 'id', 'text', 'action', 'expect_id', 'expect_text', 'value'],
    navigate: ['session', 'user', 'child', 'task', 'action_id', 'from', 'to'],
  };
  if (!Object.hasOwn(allowed, request.method)) throw new Error('Unsupported bridge method');
  for (const key of Object.keys(request)) {
    if (key !== 'method' && !allowed[request.method].includes(key)) throw new Error(`Unexpected request field: ${key}`);
  }
  const args = ['content', 'call', '--user', '0', '--uri', URI, '--method', request.method];
  for (const key of allowed[request.method]) {
    let value = request[key];
    if (['id', 'text', 'path', 'expect_id', 'expect_text', 'value'].includes(key)) value = String(value ?? '');
    if (key === 'session' || key === 'action_id') value = identifier(value, key, key === 'action_id' ? 8 : 3);
    if (key === 'user' || key === 'task') value = integer(value, key, key === 'task' ? 1 : 0);
    if (key === 'child') value = childName(value);
    if ((key === 'from' || key === 'to') && !Object.hasOwn(TABS, value)) throw new Error('Only Home/Reports navigation is supported');
    if (key === 'action' && !Object.hasOwn(ACTIONS, value)) throw new Error('Unsupported accessibility action');
    if (key === 'path' && value !== '' && !/^root(?:\/\d{1,4}){0,60}$/.test(value)) throw new Error('Invalid accessibility path');
    if (typeof value === 'string' && value.length > 4096) throw new Error(`Value too long: ${key}`);
    args.push('--extra', `${key}:${typeof value === 'number' ? 'i' : 's'}:${value}`);
  }
  if (request.method === 'navigate' && request.from === request.to) throw new Error('Source and destination must differ');
  if (request.method === 'action') {
    const selectors = [request.id, request.text, request.path].filter(Boolean).length;
    if (!selectors && request.action !== 'back') throw new Error('An action needs a node id, text or path');
    if (selectors > 1) throw new Error('Choose exactly one node id, text or path');
    if (request.action === 'back' && selectors) throw new Error('back takes no target');
  }
  return args;
}

export function parseReply(text) {
  const match = /^Result: Bundle\[\{result=([\s\S]*)\}\]\s*$/.exec(text.trim());
  if (!match) throw new Error('Incomplete or unexpected bridge output; do not retry an uncertain action');
  const reply = JSON.parse(match[1]);
  if (!reply || Array.isArray(reply) || reply.protocol !== PROTOCOL || typeof reply.status !== 'string') {
    throw new Error('Bridge protocol mismatch; build/install the matching repository helper');
  }
  return reply;
}

export function requireOk(reply) {
  if (reply.status !== 'ok') throw new Error(`Bridge refused request: ${reply.status}`);
  return reply;
}

export function validateSnapshot(reply, expectedTask) {
  requireOk(reply);
  if (reply.task !== expectedTask || !Number.isInteger(reply.window) || !Array.isArray(reply.nodes)
      || typeof reply.truncated !== 'boolean' || typeof reply.childVerified !== 'boolean'
      || typeof reply.guardComplete !== 'boolean' || !['home', 'reports', 'child', null].includes(reply.selectedTab)) {
    throw new Error('Malformed accessibility snapshot');
  }
  for (const node of reply.nodes) {
    if (!node || typeof node.path !== 'string' || typeof node.className !== 'string'
        || !['id', 'text', 'description', 'hint', 'bounds'].every(key => node[key] === null || typeof node[key] === 'string')
        || !['visible', 'enabled', 'clickable', 'longClickable', 'scrollable', 'checkable', 'checked',
          'selected', 'editable', 'focusable', 'focused', 'password'].every(key => typeof node[key] === 'boolean')
        || !Number.isInteger(node.depth) || !Array.isArray(node.actions) || !node.actions.every(Number.isInteger)) {
      throw new Error('Malformed accessibility node');
    }
  }
  return reply;
}
