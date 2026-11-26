# Huckleberry payload capture corpus

Redacted Firestore payload fixtures captured by driving the Huckleberry Android
app (0.9.316) and diffing the account documents before and after each single UI
action. Raw snapshots never leave the capture machine.

- Manifest: `manifest.json` (77 fixtures)
- Schema test: `tests/test_payload_fixtures.py`
- Coverage and schema notes: `COVERAGE.md`

## Guarantees

Every fixture is generated from one app action:

1. snapshot the account documents (7 roots, 7 subcollections),
2. perform exactly one form flow in the app,
3. wait for the write, park the app in the background,
4. snapshot again and keep only leaf-level changes,
5. delete created documents and restore changed ones from the snapshot,
6. verify the account matches the baseline before the next capture.

## Redaction

- child id and document ids -> `<child>`, `<doc>`, `<id>` (disambiguated as `<id:1>`, `<id:2>`, ... inside maps)
- child name, medicine names, custom food names -> placeholders
- free-text notes -> `<note>`
- timezone -> `<tz>` (`Etc/UTC` when a test needs a real IANA name)
- ISO timestamps -> `<datetime>`
- epoch fields are shifted by a per-capture offset so the created interval
  starts at `1767225600` (2026-01-01T00:00:00Z); relative offsets inside a
  capture are preserved.
- growth and temperature magnitudes are replaced with placeholder values
  (`weight` 10, `height` 70, `head` 45, `amount` 37); the field names, units and
  value types are unchanged.
- `analytics.*.count` values are pinned to `1.0`; the counters describe the
  account's real usage history and are not part of the write payloads.

Only created/updated payloads are stored (plus the minimal changed-field patch
for root documents). Deleted documents are described by the action text.

## Fixture shape

```json
{
  "id": "feed_bottle_ml_100",
  "tracker": "feed",
  "action": "Bottle, mL, 100 ml",
  "documents": [
    {
      "path": "feed/<child>/intervals/<doc>",
      "change": "created",
      "payload": {"mode": "bottle", "units": "ml", "amount": 100.0}
    },
    {
      "path": "feed/<child>",
      "change": "updated",
      "payload": {"added": {}, "changed": {"prefs.bottleAmount": 100.0}, "removed": []}
    }
  ]
}
```
