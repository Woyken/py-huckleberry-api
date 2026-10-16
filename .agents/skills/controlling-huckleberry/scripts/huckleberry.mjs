import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { withSession, start, status, release, setupRuntime, delay } from './runtime.mjs';

const HELP = `Huckleberry (native Windows; only the configured emulator)
  setup                                    Install pinned dependency in LOCALAPPDATA, not skills
  start --session <unique-task-id>           Boot existing AVD/open app/connect; no Home reset
  status                                   Read-only device/claim status; does not boot
  snapshot --session <id>                   Accessibility snapshot, with visible-DOM fallback
  inspect [CSS|qa:name] --session <id>       Current visible controls (top overlay when present)
  click <CSS|qa:name> --session <id>         One UI click; never automatically retry a write
  fill <CSS|qa:name> <text> --session <id>    Fill a native input/textarea (supports Unicode)
  read <CSS|qa:name> --session <id>          Read visible text; not credential/input-value extraction
  wait <CSS|qa:name> [--text <literal> [--exact]|--hidden] --session <id>
  keypad <integer-ml> --session <id>         Set open bottle mL keypad; does NOT save record
  screenshot --out <new-path.png> --session <id>
  checkpoint --out <new-path.json> --session <id>  UI/time evidence, NOT a Firebase snapshot
  release --session <id>                    Remove owned forward/claim; leave emulator/app/data
  help
Never control another device, steal a claim, patch the APK, or infer API parity from UI alone.`;

export async function main(args = process.argv.slice(2)) {
  const { positionals, values } = parseArgs({ args, allowPositionals: true, strict: true, options: {
    session: { type: 'string' }, out: { type: 'string' }, text: { type: 'string' }, hidden: { type: 'boolean' },
    help: { type: 'boolean', short: 'h' }, exact: { type: 'boolean' },
  } });
  const [command = 'help', selector, value] = positionals;
  if (values.help || command === 'help') { console.log(HELP); return; }
  const commands = ['setup', 'start', 'status', 'release', 'snapshot', 'inspect', 'click', 'fill', 'read', 'wait', 'keypad', 'screenshot', 'checkpoint'];
  if (!commands.includes(command)) throw new Error(`Unknown command: ${command}. Run help.`);
  const needsSelector = ['click', 'fill', 'read', 'wait', 'keypad'].includes(command);
  if (needsSelector && !selector) throw new Error(`${command} needs an explicit selector/value. Run help.`);
  if (command === 'fill' && value === undefined) throw new Error('fill needs text; use an empty quoted string to clear.');
  if (['screenshot', 'checkpoint'].includes(command) && !values.out) throw new Error(`${command} needs --out <new-file>.`);
  if (values.hidden && values.text !== undefined) throw new Error('Use --hidden or --text, not both.');
  if (values.exact && values.text === undefined) throw new Error('--exact requires --text.');
  let result;
  if (command === 'setup') result = await setupRuntime();
  else if (command === 'status') result = await status();
  else if (command === 'start') result = await withSession(values.session, start, { create: true });
  else if (command === 'release') {
    result = await withSession(values.session, release);
  } else {
    const { withHuckleberry, locate, snapshot, inspect, keypad, checkpoint } = await import('./ui.mjs');
    result = await withHuckleberry(values.session, async (page, state) => {
      if (command === 'inspect') return inspect(page, selector);
      if (command === 'read') {
        const node = locate(page, selector);
        if (await node.evaluate((element) => element.matches('input, textarea') || element.getAttribute('type') === 'password')) {
          throw new Error('read returns visible element text, not input/password values. Use form/UI evidence, not credential inspection.');
        }
        return { text: await node.innerText() };
      }
      if (command === 'wait') {
        const node = values.hidden ? (selector.startsWith('qa:') ? page.getByTestId(selector.slice(3)) : page.locator(selector)) : locate(page, selector);
        if (values.hidden) {
          // Cached hidden pages may duplicate QA attributes. Require ALL matches
          // hidden, rather than selecting an arbitrary instance or failing strictness.
          const deadline = Date.now() + 10000;
          while (!(await node.evaluateAll((nodes) => nodes.every((element) => !element.checkVisibility())))) {
            if (Date.now() >= deadline) throw new Error(`Timed out waiting for all matches of ${selector} to be hidden.`);
            await delay(150);
          }
        } else if (values.text !== undefined) {
          const escaped = values.text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          await node.filter({ hasText: values.exact ? new RegExp(`^\\s*${escaped}\\s*$`) : values.text }).waitFor({ state: 'visible' });
        } else await node.waitFor({ state: 'visible' });
        return { matched: selector, state: values.hidden ? 'hidden' : 'visible', expectedText: values.text,
          ...(values.text !== undefined ? { textMatch: values.exact ? 'exact' : 'substring' } : {}) };
      }
      if (command === 'click') await locate(page, selector).click();
      if (command === 'fill') await locate(page, selector).fill(value);
      if (command === 'keypad') return keypad(page, selector);
      if (command === 'checkpoint') return checkpoint(page, state, values.out);
      if (command === 'screenshot') {
        const { writeFile } = await import('node:fs/promises');
        const image = await page.screenshot({ type: 'png' });
        await writeFile(values.out, image, { flag: 'wx', mode: 0o600 });
        return { saved: values.out };
      }
      return snapshot(page);
    });
  }
  console.log(JSON.stringify(result, null, 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(JSON.stringify({ error: error.message, guidance: 'Inspect status. Retry observations freely, but reconcile an uncertain write before clicking Save again. Claims are retained on failure; release your session when done.' }));
    process.exitCode = 1;
  });
}
