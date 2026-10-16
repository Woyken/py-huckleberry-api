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
  action-status --session <id> --action-id <id>  Read consumed/uncertain dispatch state
  release --session <id>                  Release only this session; leave app intact
No forms, typing, saves, deletes, screenshots, coordinate input or backend writes.
Enable the helper service manually. No AVD boot, APK install or policy changes are automatic.`;

export async function main(args = process.argv.slice(2)) {
  const { positionals, values } = parseArgs({ args, allowPositionals: true, strict: true, options: {
    profile: { type: 'string' }, session: { type: 'string' }, from: { type: 'string' },
    'action-id': { type: 'string' }, out: { type: 'string' }, help: { type: 'boolean', short: 'h' },
  } });
  const [command = 'help', destination] = positionals;
  if (values.help || command === 'help') { console.log(HELP); return; }
  if (!['status', 'claim', 'launch', 'inspect', 'navigate', 'action-status', 'release'].includes(command)) throw new Error('Unknown command');
  if (positionals.length > (command === 'navigate' ? 2 : 1)) throw new Error('Unexpected positional arguments');
  if (command !== 'status') identifier(values.session);
  if (values.out && command !== 'inspect') throw new Error('--out is only valid for inspect');
  if (command === 'navigate') {
    identifier(values['action-id'], 'action-id', 8);
    if (!Object.hasOwn(TABS, destination) || !Object.hasOwn(TABS, values.from) || destination === values.from) throw new Error('Choose different Home/Reports tabs');
  } else if (command === 'action-status') {
    identifier(values['action-id'], 'action-id', 8);
    if (values.from) throw new Error('--from requires navigate');
  } else if (values.from || values['action-id']) throw new Error('Action flags require navigate or action-status');
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
  console.log(JSON.stringify(result, null, 2));
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    console.error(JSON.stringify({ error: error.message, guidance: 'No action is automatically retried. Preserve the session/action ID and inspect uncertain outcomes.' }));
    process.exitCode = 1;
  });
}
