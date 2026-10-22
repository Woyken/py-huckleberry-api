import { randomUUID } from 'node:crypto';
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { loadProfile } from './lib/config.mjs';
import { Controller, summary, saveEvidence } from './lib/client.mjs';
import { identifier, TABS } from './lib/protocol.mjs';

const HELP = `Huckleberry accessibility debugging tool (not part of the API product)
  node tools/huckleberry-control/cli.mjs <command> --profile <local.json>
  status                                  Verify target and bridge connection
  claim --session <id>                    Claim target with configured user/child
  launch --session <id>                   Open enrolled Huckleberry via launcher API
  inspect --session <id> [--out <new.json>] Read accessibility nodes; no screenshots
  navigate <home|reports> --from <tab> --session <id> --action-id <unique-id>
  act --action <action> --session <id> [--id <view-id> | --text <exact-text> | --path <node-path>]
      [--value <text>] [--expect-id <id>] [--expect-text <text>] [--action-id <unique-id>]
  click --session <id> (--id <view-id> | --text <exact-text> | --path <node-path>)
  set-text --session <id> (--id <view-id> | --text <exact-text> | --path <node-path>) --value <text>
  scroll --session <id> (--id <view-id> | --text <exact-text> | --path <node-path>) --direction <forward|backward>
  back --session <id>                     Android global BACK (journaled)
  action-status --session <id> --action-id <id>  Read consumed/uncertain dispatch state
  release --session <id>                  Release only this session; leave app intact

Actions are semantic accessibility node actions, not coordinates or screenshots.
They can change app/backend state. Every action requires a unique action id and is
never retried automatically. Verify the intended child/profile before acting.
Enable the helper service manually. No AVD boot, APK install or policy changes are automatic.`;

function actionId(value) {
  return identifier(value, 'action-id', 8);
}

function freshActionId() {
  return actionId(`ui-${Date.now().toString(36)}-${randomUUID().replaceAll('-', '').slice(0, 12)}`);
}

function targetArgs(values) {
  const selectors = [values.id, values.text, values.path].filter(Boolean).length;
  if (selectors > 1) throw new Error('Choose exactly one of --id, --text or --path');
  return { id: values.id || '', text: values.text || '', path: values.path || '',
    expectId: values['expect-id'] || '', expectText: values['expect-text'] || '' };
}

export async function main(args = process.argv.slice(2)) {
  const { positionals, values } = parseArgs({ args, allowPositionals: true, strict: true, options: {
    profile: { type: 'string' }, session: { type: 'string' }, from: { type: 'string' },
    'action-id': { type: 'string' }, out: { type: 'string' }, help: { type: 'boolean', short: 'h' },
    id: { type: 'string' }, text: { type: 'string' }, path: { type: 'string' }, action: { type: 'string' },
    value: { type: 'string' }, 'expect-id': { type: 'string' }, 'expect-text': { type: 'string' },
    direction: { type: 'string' },
  } });
  const [command = 'help', destination] = positionals;
  if (values.help || command === 'help') { console.log(HELP); return; }
  const commands = ['status', 'claim', 'launch', 'inspect', 'navigate', 'action-status', 'act', 'click', 'set-text', 'scroll', 'back', 'release'];
  if (!commands.includes(command)) throw new Error('Unknown command');
  if (positionals.length > (command === 'navigate' ? 2 : 1)) throw new Error('Unexpected positional arguments');
  if (command !== 'status' || values.session) identifier(values.session);
  if (values.out && command !== 'inspect') throw new Error('--out is only valid for inspect');
  if (command === 'navigate') {
    actionId(values['action-id']);
    if (!Object.hasOwn(TABS, destination) || !Object.hasOwn(TABS, values.from) || destination === values.from) throw new Error('Choose different Home/Reports tabs');
    if (values.id || values.text || values.path || values.action || values.value || values.direction || values['expect-id'] || values['expect-text']) throw new Error('Node/action flags require act/click/set-text/scroll/back');
  } else if (command === 'action-status') {
    actionId(values['action-id']);
    if (values.from || values.id || values.text || values.path || values.action || values.value || values.direction || values['expect-id'] || values['expect-text']) throw new Error('action-status takes only --session and --action-id');
  } else if (['act', 'click', 'set-text', 'scroll', 'back'].includes(command)) {
    const supplied = values.id || values.text || values.path || values.action || values.value || values.direction || values['expect-id'] || values['expect-text'];
    if (command === 'back' && supplied) throw new Error('back takes no target or action flags');
    if (values.from) throw new Error('--from requires navigate');
  } else if (values.from || values['action-id'] || values.id || values.text || values.path || values.action || values.value || values.direction) {
    throw new Error('Action flags require act/click/set-text/scroll/back');
  }

  const controller = await new Controller(await loadProfile(values.profile)).connect();
  let result;
  if (command === 'status') result = await controller.status();
  if (command === 'claim') result = await controller.claim(values.session);
  if (command === 'release') result = await controller.release(values.session);
  if (command === 'action-status') result = await controller.actionStatus(values.session, values['action-id']);
  if (command === 'launch' || command === 'inspect') {
    const snapshot = command === 'launch' ? await controller.launch(values.session) : await controller.inspect(values.session);
    if (values.out) await saveEvidence(values.out, snapshot);
    result = { ...summary(snapshot), ...(values.out ? { saved: values.out } : {}) };
  }
  if (command === 'navigate') result = await controller.navigate(values.session, values.from, destination, values['action-id']);
  if (['act', 'click', 'set-text', 'scroll', 'back'].includes(command)) {
    const id = values['action-id'] ? actionId(values['action-id']) : freshActionId();
    const target = command === 'back' ? { id: '', text: '', path: '', expectId: '', expectText: '' } : targetArgs(values);
    let action = values.action;
    if (command === 'click') action = 'click';
    if (command === 'set-text') action = 'set-text';
    if (command === 'scroll') {
      if (!['forward', 'backward'].includes(values.direction)) throw new Error('--direction must be forward or backward');
      action = `scroll-${values.direction}`;
    }
    if (command === 'back') action = 'back';
    if (!action) throw new Error('--action is required for act');
    if (command === 'set-text' && values.value === undefined) throw new Error('set-text requires --value (empty string is allowed)');
    result = await controller.act(values.session, { ...target, action, value: values.value ?? '', actionId: id });
  }
  console.log(JSON.stringify(result, null, 2));
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    console.error(JSON.stringify({ error: error.message, guidance: 'No action is automatically retried. Preserve the session/action ID and inspect uncertain outcomes.' }));
    process.exitCode = 1;
  });
}
