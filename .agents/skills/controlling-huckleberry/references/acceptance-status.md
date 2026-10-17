# Acceptance evidence snapshot — 2026-10-17

This records tested/reported outcomes, not guarantees for another app build, host, profile or later investigation. Public records omit enrollment, original session/action IDs, account/device identifiers, paths and raw trees.

## Sources

- Shared baseline: `bd376c3`; Windows skill routing: `6837e23`.
- Termux runner source: `29314f326048728d2f439ab97d943c92308bea16`; integrated unchanged with provenance as `be877cd`.
- Integrated head checked by CI: `61a9a5b`.
- Fresh device outcomes are **reported by the Termux agent**, not independently exercised from Windows: [Termux acceptance and reconciliation](https://github.com/Woyken/py-huckleberry-api/pull/68#issuecomment-6234016918).
- [Windows source review](https://github.com/Woyken/py-huckleberry-api/pull/68#issuecomment-6233923190) and [integrated CI results](https://github.com/Woyken/py-huckleberry-api/pull/68#issuecomment-6233966744).

## Result matrix

| Scope | Result | Boundary |
| --- | --- | --- |
| Shared controller + runner offline tests | 44 passed locally on each reported host; integrated CI passed on Windows/Linux | Synthetic/mocked guards, uncertainty handling and sanitization; not live navigation. |
| Existing Windows CDP skill offline tests | 10 passed locally on Windows | CDP regression checks; not Termux or accessibility-service acceptance. |
| Unsigned Android helper compile | Integrated CI passed | Build compatibility, not install, consent or device behavior. |
| Windows CDP app operations | Historical local UI acceptance | Startup, keypad/Unicode and marked record readback/cleanup on the tested emulator/build; not bridge forms or Firebase parity. |
| Termux common-controller navigation | Historical reported acceptance | Earlier Home/Reports/Home checks; not a fresh runner pass. |
| New Termux runner, observation-only | Fresh **passed**, reported by Termux | Target/service/capability/child/complete-baseline checks and verified release; no navigation/launch/input. |
| New Termux runner, full navigation | Fresh **uncertain / not passed** | Controller verified the first transition, but the runner's independent Reports readback failed. Home was not attempted; no automatic retry/release. |
| Windows live accessibility bridge | Pending | No consented emulator live acceptance in this collaboration; local default SDK build prerequisites unavailable. |
| Forms, typing/keypad, Save/delete, timers or screenshots via accessibility | Unsupported / not established | Navigation code/tests do not confer these capabilities. |
| Firebase persistence / app-vs-API parity in acceptance | Not tested | No backend observer/capture; UI results have `backendCaptured: false`. |

## Fresh navigation uncertainty and reconciliation

The larger-budget fresh runner ended with exit code 1, `phase: reports_observation`, `reason: reports_observation_failed`, and a retained claim. Its allowlisted report did not retain the raw exception, so the exact cause is **unknown**. A later inspection was refused by the identity/focus guard; that does not prove the earlier failure's cause or establish a transport defect.

The Termux operator reconciled the terminal run's original metadata: Reports consumed, Home unconsumed. Under the same matching claim, one scoped launcher request brought the enrolled work app forward **for readback only**. Inspection verified the expected child, Reports selection and complete visible guard evidence. No Reports replay or Home navigation occurred. The operator explicitly released the claim and left the app on Reports.

At that handoff there was **no in-flight runner or retained claim from these attempts**, according to Termux. Windows did not operate the phone or perform recovery. This historical record is not proof that the device is currently free.

An earlier 300-second external timeout and separately reported Home recovery are also **not** a passed fresh round-trip. Do not combine historical navigation, interrupted-run recovery and observation success into a fictitious complete pass.

## Follow-up boundary

- Keep full-navigation acceptance qualified; do not call both hosts fully live-accepted.
- No further run, install, policy/service change or historical fixture cleanup is requested here. Do not weaken guards to turn uncertainty green.
- A later foreground-stable acceptance is a new explicitly planned/authorized investigation after reconciliation. New case IDs must not be replacement IDs chosen to replay an uncertain action.
- Re-establish capability, consent, target identity, quiet conditions and current ownership for that investigation.
- Passing CI or observation does not validate form entry, exactly-once input, unified cross-driver locking or Firebase parity.
