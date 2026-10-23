// Opt-in device acceptance, not a controller/transport implementation.
// Ordinary tests inject mocks. Importing this module never contacts Android.
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { Controller, loadProfile, validateProfile } from '../index.mjs';

class Refusal extends Error {
  constructor(code) { super(code); this.code = code; }
}

function requireCondition(condition, code) {
  if (!condition) throw new Refusal(code);
}

function safeId(value, minimum, maximum) {
  return typeof value === 'string' && value.length >= minimum && value.length <= maximum
    && /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(value);
}

function verifyObservation(snapshot, tab) {
  requireCondition(snapshot?.protocol === 3 && snapshot.status === 'ok'
    && snapshot.source === 'android-accessibility' && snapshot.backendCaptured === false, 'unexpected_evidence_source');
  requireCondition(snapshot.guardComplete === true && snapshot.truncated === false, 'incomplete_observation');
  requireCondition(['home', 'reports'].includes(snapshot.selectedTab), 'unexpected_start_tab');
  if (tab) requireCondition(snapshot.selectedTab === tab, 'unexpected_selected_tab');
}

/** Returns an allowlisted report. Never include an error message, tree, path or identity. */
export async function runTermuxSmoke(options, dependencies = {}) {
  const report = {
    schemaVersion: 1,
    status: 'blocked',
    phase: 'options',
    reason: null,
    backend: 'accessibility',
    transport: 'rish',
    mode: options?.navigate === true ? 'navigation' : 'observation',
    evidenceLevel: 'ui-only',
    claimState: 'not_acquired',
    navigationInvoked: false,
    backendCaptured: false,
    trackingWrites: false,
    screenshots: false,
    checks: {
      serviceConnected: false, capabilitiesVerified: false, vacantClaim: false,
      targetVerified: false, claimAcquired: false, baselineVerified: false,
      unusedActionIds: false, reportsVerified: false, homeVerified: false, released: false,
    },
  };
  let claimAttempted = false;
  let releaseAttempted = false;
  try {
    requireCondition(options && typeof options.profilePath === 'string' && options.profilePath.trim().length > 0, 'private_profile_required');
    requireCondition(safeId(options.session, 3, 80), 'invalid_session');
    requireCondition(options.exclusiveAccess === true, 'exclusive_access_ack_required');
    requireCondition(options.navigate === undefined || typeof options.navigate === 'boolean', 'invalid_navigation_option');
    if (options.navigate === true) requireCondition(safeId(options.actionPrefix, 8, 64), 'stable_action_prefix_required');
    else requireCondition(options.actionPrefix === undefined, 'action_prefix_requires_navigation');

    report.phase = 'profile';
    const profile = validateProfile(await (dependencies.loadProfile || loadProfile)(options.profilePath));
    requireCondition(profile.transport === 'rish', 'termux_transport_required');
    report.phase = 'connect';
    const controller = (dependencies.createController || (value => new Controller(value)))(profile);
    await controller.connect();
    report.phase = 'status';
    const status = await controller.status();
    requireCondition(status?.protocol === 3 && status.status === 'ok', 'unexpected_status_protocol');
    requireCondition(status.owner === null || safeId(status.owner, 3, 80), 'unexpected_status_protocol');
    requireCondition(status.serviceConnected === true, 'service_not_connected');
    report.checks.serviceConnected = true;
    const capabilities = status.capabilities;
    requireCondition(capabilities?.inspect === true && capabilities.semanticActions === true
      && Array.isArray(capabilities.actions) && capabilities.actions.includes('click')
      && capabilities.trackingWrites === false && capabilities.screenshots === false
      && capabilities.gestures === false, 'unexpected_capabilities');
    report.checks.capabilitiesVerified = true;
    // Even the same ID may belong to an interrupted/live task. Never resume it here.
    requireCondition(status.owner === null, 'existing_claim');
    report.checks.vacantClaim = true;
    report.phase = 'identity';
    await controller.identity(); // Common implementation verifies user/package/focus/task.
    report.checks.targetVerified = true;

    report.phase = 'claim';
    claimAttempted = true;
    report.claimState = 'unknown';
    const claim = await controller.claim(options.session);
    requireCondition(claim?.status === 'ok' && claim.owner === options.session, 'claim_not_confirmed');
    report.claimState = 'retained';
    report.checks.claimAcquired = true;
    report.phase = 'baseline';
    verifyObservation(await controller.inspect(options.session), options.navigate ? 'home' : undefined);
    report.checks.baselineVerified = true;

    if (options.navigate) {
      const reportsId = `${options.actionPrefix}-reports`;
      const homeId = `${options.actionPrefix}-home`;
      report.phase = 'action_preflight';
      for (const id of [reportsId, homeId]) {
        const action = await controller.actionStatus(options.session, id);
        requireCondition(action?.protocol === 3 && action.status === 'ok' && typeof action.consumed === 'boolean', 'unexpected_action_status');
        requireCondition(action.consumed === false, 'action_id_already_consumed');
      }
      report.checks.unusedActionIds = true;
      report.phase = 'reports_navigation';
      report.navigationInvoked = true;
      const reports = await controller.navigate(options.session, 'home', 'reports', reportsId);
      requireCondition(reports?.status === 'verified', 'navigation_not_verified');
      report.phase = 'reports_observation';
      verifyObservation(await controller.inspect(options.session), 'reports');
      report.checks.reportsVerified = true;
      report.phase = 'home_navigation';
      const home = await controller.navigate(options.session, 'reports', 'home', homeId);
      requireCondition(home?.status === 'verified', 'navigation_not_verified');
      report.phase = 'home_observation';
      verifyObservation(await controller.inspect(options.session), 'home');
      report.checks.homeVerified = true;
    }

    report.phase = 'release';
    releaseAttempted = true;
    const released = await controller.release(options.session);
    requireCondition(released?.status === 'ok' && released.released === options.session, 'release_not_confirmed');
    report.claimState = 'released';
    report.checks.released = true;
    report.status = 'passed';
    report.phase = 'complete';
  } catch (error) {
    // No finally/recovery input or release. Unknown ownership/action outcomes stay
    // with the operator for reconciliation through the original common CLI/IDs.
    report.status = report.navigationInvoked || (claimAttempted && !report.checks.claimAcquired) || releaseAttempted ? 'uncertain' : 'blocked';
    report.reason = error instanceof Refusal ? error.code : `${report.phase}_failed`;
    if (releaseAttempted || claimAttempted && !report.checks.claimAcquired) report.claimState = 'unknown';
  }
  return report;
}

const HELP = `Termux-only accessibility acceptance (UI-only, no Firebase parity)
  --profile <private.json> --session <retained-id> --exclusive-access
  [--navigate --action-prefix <retained-unique-prefix>]
Default: observe the already-foreground enrolled Home/Reports UI; no launch or input.
Navigation opt-in requires Home and unused IDs; it verifies Home -> Reports -> Home.
No install, service enablement, policy changes, screenshot, form write or fixture cleanup.
Exclusive access is your operational acknowledgment, not a cross-backend lock.
Errors retain claims/IDs for manual reconciliation. Output omits enrollment/identity data.`;

export async function main(args = process.argv.slice(2)) {
  try {
    const { values } = parseArgs({ args, strict: true, options: {
      profile: { type: 'string' }, session: { type: 'string' }, navigate: { type: 'boolean' },
      'action-prefix': { type: 'string' }, 'exclusive-access': { type: 'boolean' }, help: { type: 'boolean', short: 'h' },
    } });
    if (values.help) { console.log(HELP); return null; }
    const report = await runTermuxSmoke({ profilePath: values.profile, session: values.session,
      navigate: values.navigate, actionPrefix: values['action-prefix'], exclusiveAccess: values['exclusive-access'] });
    console.log(JSON.stringify(report, null, 2));
    return report;
  } catch {
    const report = { schemaVersion: 1, status: 'blocked', phase: 'options', reason: 'invalid_arguments', backendCaptured: false };
    console.log(JSON.stringify(report));
    return report;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const report = await main();
  if (report && report.status !== 'passed') process.exitCode = 1;
}
