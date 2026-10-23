# Windows accessibility live evidence — 2026-10-17

This is actual emulator/device evidence, separate from mocked Windows CI, historical CDP tests, and the Termux runner. It is limited to the tested setup and supported navigation/inspection capabilities. Enrollment, signing material, original IDs and raw UI trees remain private.

## Tested setup

- Native Windows, existing Android 14/API-34 emulator, Huckleberry `0.9.305`.
- Bridge manifest version code 12 / protocol 1; unchanged Android Java/XML from the reviewed source.
- Explicit local build: Android SDK **platform 34**, **build-tools 34.0.0**, **JDK 21**. The APK compiled, was locally signed and verified, then installed only on the verified emulator.
- This validates that explicit API-34 build on Android 14. It does **not** claim the default API-35 build was performed locally or silently change project defaults; API-35 CI compilation is separate evidence.
- The user manually enabled the separate accessibility service. No secure-settings enablement, policy change, Huckleberry APK modification or physical phone operation occurred.
- Shared accessibility was the only app-control backend used. No CDP session, coordinate input or screenshot fallback was used for acceptance. The bridge's broad service permission is consented debugging access, not a sandbox guarantee.

## First case: navigation/guards passed, launch readback qualified

Against `e72c708`, the single launcher request opened the app, but its immediate guarded readback refused `focused_task_not_found`. A later **read-only** inspection verified the intended task/child and complete Home tree without relaunching. That initial launch command was not called a clean pass.

With ownership explicitly retained and reconciled, the previously unattempted navigation actions then passed Home → Reports → Home, including controller verification and independent complete/untruncated postcondition reads. The navigation phase including its verification/journal reads took **81.1 seconds**, not single-click latency.

The same live case verified:

- Competing claim and release refused; original owner unchanged.
- Changed enrollment under the same owner refused.
- Intentional replay of an already-verified consumed action ID suppressed; Home stayed unchanged. This was a planned negative test, not a replay of uncertain input.
- Consumed IDs survived release/reclaim; final owner was vacant.

## Narrow host fix and fresh clean case

Source **`9ef9ebda53dd91d6a6c4d7c89ba0ae28136e4b4f`** changes only launch readback behavior: after its **one** launcher request, repeat at most three fresh inspections only for the exact transient bridge refusal `focused_task_not_found`. Each read retains host/device identity and ownership checks. No launcher/navigation input retry, permission override, new driver fallback or protocol change is introduced. Other refusals still stop immediately.

Five added mocked cases cover the successful path, recovered task-cache observation, retry bound, immediate non-transient refusal, and uncertain launcher dispatch without replay/cleanup. Shared-tool offline total: **49 passed**; separate CDP skill offline total: **10 passed**.

After the first case was fully released, a separate explicitly planned fresh case used new retained IDs. Operator preparation confirmed complete Home/no open form, stopped **only the enrolled app process**, and verified it absent; that preparation is not a new controller command. The installed helper, its consent and replay journal were not reset.

The complete fresh live case passed:

- **One launcher request**, **two readback attempts**: first transient refusal, then a complete correctly enrolled Home observation.
- Home → Reports → Home, each dispatched once through the common controller and independently read back with the then-required child proof, complete guard scan and no truncation.
- Verified-action replay suppressed and Home unchanged.
- Explicit final release and server-reported vacant bridge owner.

The timed phase after transport connection, including preparation, metadata checks, launch, navigation/readbacks, replay probe and release, took **129.8 seconds**. It is not a latency guarantee. Non-invasive counting wrappers delegated to the real inspection/transport methods unchanged: no synthetic responses, extra launcher calls or guard bypass were used.

## Boundaries and cleanup

- No tracking-record write, form entry, timer, screenshot/gesture capability or backend observer was invoked. `backendCaptured: false`; this does not prove no unrelated app/CI backend activity occurred.
- Windows live acceptance now covers this setup's supported common accessibility workflow. It does not establish broader bridge capabilities, every host/build/profile, exactly-once work or unified CDP/bridge locking.
- The separate Termux full-runner uncertainty is unchanged. This startup fix is not a diagnosis or validation of its later Reports observation failure.
- Raw evidence, private enrollment, local APK/key/password and retained original IDs are not committed or uploaded. Retain the signing key privately for any explicitly authorized helper update; never uninstall/reset just to clear a journal.
- No test claim remained at the completed handoff; the app was on Home at final verification. Check current ownership afresh before another task.
- Disable the accessibility service manually when not continuing authorized debugging. No automatic service/permission change is part of controller release.
- Accessibility settings were opened afterward only for user-controlled service disablement, separate from the recorded app-control acceptance.
