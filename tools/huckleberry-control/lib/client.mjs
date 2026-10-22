import { writeFile } from 'node:fs/promises';
import { Transport } from './transport.mjs';
import { validateProfile } from './config.mjs';
import { parseIdentity } from './identity.mjs';
import { BRIDGE, PACKAGE, TABS, contentArguments, identifier, parseReply, quote, requireOk, validateSnapshot } from './protocol.mjs';

// Android's accessibility window cache can briefly lag the newly resumed task.
// Bound fresh reads, never launcher/input dispatches, and preserve other refusals.
const LAUNCH_READBACK_ATTEMPTS = 3;
const LAUNCH_READBACK_DELAY_MS = 200;

export class Controller {
  constructor(profile, transport) {
    this.profile = validateProfile(profile);
    this.transport = transport || new Transport(this.profile);
  }

  async connect() { await this.transport.verify(); return this; }

  async #exchange(request) {
    if (!this.transport.ready) throw new Error('Verify the enrolled target before bridge access');
    return parseReply(await this.transport.capture(contentArguments(request).map(quote).join(' ')));
  }

  async request(request) {
    contentArguments(request); // Validate before any device access.
    if (!this.transport.ready) throw new Error('Verify the enrolled target before bridge access');
    if (Object.hasOwn(request, 'user') && (request.user !== this.profile.androidUser || request.child !== this.profile.expectedChild)) {
      throw new Error('Request does not match the enrolled profile');
    }
    if (request.method === 'tree' || request.method === 'navigate') {
      const current = await this.identity();
      if (current.task !== request.task) throw new Error('Request does not match the verified current task');
    }
    return this.#exchange(request);
  }

  async identity() {
    if (!this.transport.ready) throw new Error('Verify the enrolled target before identity access');
    const activity = await this.transport.shell("/system/bin/dumpsys activity activities | /system/bin/grep -E 'topResumedActivity=|mResumedActivity'");
    const window = await this.transport.shell('/system/bin/dumpsys window | /system/bin/grep mCurrentFocus');
    return parseIdentity(activity, window, this.profile);
  }

  async claim(session) {
    return requireOk(await this.request({ method: 'claim', session, user: this.profile.androidUser, child: this.profile.expectedChild }));
  }

  async status() { return requireOk(await this.request({ method: 'status' })); }
  async release(session) { return requireOk(await this.request({ method: 'release', session })); }
  async actionStatus(session, actionId) {
    return requireOk(await this.request({ method: 'action-status', session, action_id: actionId }));
  }

  async launch(session) {
    identifier(session);
    const status = await this.status();
    if (status.owner !== session) throw new Error('Claim the bridge with this session before launching');
    await this.claim(session); // Refuse a changed enrollment for the existing claim.
    const path = (await this.transport.shell(`/system/bin/pm path ${quote(BRIDGE)}`)).trim();
    if (!/^package:\/data\/app\/[^\r\n]+\/base\.apk$/.test(path)) throw new Error('Unable to identify the installed bridge APK');
    await this.transport.shell(`CLASSPATH=${quote(path.slice(8))} /system/bin/app_process /system/bin ${BRIDGE}.ProfileLauncher ${this.profile.androidUser}`);
    for (let attempt = 1; ; attempt++) {
      try { return await this.inspect(session); }
      catch (error) {
        if (!(error instanceof Error) || error.message !== 'Bridge refused request: focused_task_not_found'
            || attempt >= LAUNCH_READBACK_ATTEMPTS) throw error;
        await new Promise(resolve => setTimeout(resolve, LAUNCH_READBACK_DELAY_MS));
      }
    }
  }

  async inspect(session) {
    const before = await this.identity();
    const snapshot = validateSnapshot(await this.#exchange({ method: 'tree', session,
      user: this.profile.androidUser, child: this.profile.expectedChild, task: before.task }), before.task);
    const after = await this.identity();
    if (before.task !== after.task) throw new Error('Task changed during observation');
    return { ...snapshot, identity: after, source: 'android-accessibility', backendCaptured: false };
  }

  async act(session, request) {
    const { actionId, action, id = '', text = '', path = '', expectId = '', expectText = '', value = '' } = request;
    // Validate every argument, including empties, before any device access.
    contentArguments({ method: 'action', session, user: this.profile.androidUser,
      child: this.profile.expectedChild, task: 1, action_id: actionId, path, id, text, action,
      expect_id: expectId, expect_text: expectText, value });
    const before = await this.identity();
    return this.request({ method: 'action', session, user: this.profile.androidUser,
      child: this.profile.expectedChild, task: before.task, action_id: actionId, path, id, text, action,
      expect_id: expectId, expect_text: expectText, value });
  }

  async navigate(session, from, to, actionId) {
    // Validate all arguments before any command. Never generate a fresh ID on retry.
    contentArguments({ method: 'navigate', session, user: this.profile.androidUser,
      child: this.profile.expectedChild, task: 1, from, to, action_id: actionId });
    const before = await this.inspect(session);
    if (before.selectedTab !== from || !before.childVerified) throw new Error('Navigation preconditions failed');
    const result = await this.request({ method: 'navigate', session, user: this.profile.androidUser,
      child: this.profile.expectedChild, task: before.task, from, to, action_id: actionId });
    if (result.status !== 'dispatched') {
      throw new Error(`Action not repeated: ${result.status}. Inspect and reconcile before further actions`);
    }
    // Poll observations only. Dispatch is never retried, even on timeout/error.
    const deadline = Date.now() + 15000;
    do {
      const after = await this.inspect(session);
      if (after.selectedTab === to && after.childVerified) return { status: 'verified', actionId, from, to, identity: after.identity, trackingWrites: false };
      await new Promise(resolve => setTimeout(resolve, 200));
    } while (Date.now() < deadline);
    throw new Error('Navigation outcome uncertain; inspect the UI tree, never replay the action');
  }
}

export function summary(snapshot) {
  return {
    status: snapshot.status, source: snapshot.source, identity: snapshot.identity,
    childVerified: snapshot.childVerified, selectedTab: snapshot.selectedTab,
    nodeCount: snapshot.nodes.length, truncated: snapshot.truncated,
    controls: snapshot.nodes.filter(node => node.visible && node.id && node.id !== 'undefined')
      .map(node => ({ id: node.id, path: node.path, text: node.text, className: node.className,
        clickable: node.clickable, editable: node.editable, actions: node.actions })),
    navigationTabs: Object.keys(TABS), package: PACKAGE, backendCaptured: false,
  };
}

export async function saveEvidence(path, snapshot) {
  await writeFile(path, JSON.stringify(snapshot, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
}
