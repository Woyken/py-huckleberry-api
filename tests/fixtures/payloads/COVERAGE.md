# Capture coverage

App 0.9.316 (93160006), Android work profile, synthetic test child.
Corpus: 77 fixtures in this directory (`manifest.json`).

| Tracker | Count | Branches |
| --- | --- | --- |
| sleep | 3 | timer started, paused, completed interval |
| feed | 27 | bottle: all 7 types (Breast Milk, Formula, Tube Feeding, Cow Milk, Goat Milk, Soy Milk, Other) at 100 mL, plus mL/oz/0/note; nursing timer L/R/pause/save; nursing manual symmetric/asymmetric/note; solids 4 reactions + note + 2 foods + custom food create/log |
| diaper | 15 | pee/poo/mixed/dry, size/consistency/rash/note, potty dry/success/accident/mixed, 5 of 7 consistency literals |
| pump | 7 | total mL/oz, left-right, default amount, timer start/complete, note |
| health | 15 | medicine mL/oz/drops/tsp, blank, note, type creation; growth kg/lbs.oz + cm/ft.in + head cm/in; temperature C/F/note |
| activity | 10 | Bath (plain, duration, timer), Tummy time, Story time, Screen time, Skin to skin, Brush teeth, Indoor play, Outdoor play |

The fixture test parses every created document and asserts that the model
declares every captured key (`tests/test_payload_fixtures.py`).

## Observed schema notes

- Bottle `bottleType` literals are stored title-cased: `Breast Milk`,
  `Formula`, `Tube Feeding`, `Cow Milk`, `Goat Milk`, `Soy Milk`, `Other`.
- Solids reactions: `LOVED`, `MEH`, `HATED`, `ALLERGIC`. Per-food entries carry
  `reaction` or `allergic: true`, plus `order` and an optional `notes`; the
  interval carries a `reactions` boolean map.
- Diaper `quantity`: Little = 0, Medium = 50, Big = 100 (pee and poo).
  Captured consistency literals: `solid`, `loose`, `runny`, `mucousy`, `hard`
  (lowercase). `pebbles` and `diarrhea` exist in the model but the 0.9.316 UI
  does not offer them.
- Potty `howItHappened`: `satButDry`, `wentPotty`, `accident`; Mixed -> `mode: both`.
- Activity kinds are the interval `mode`: `bath`, `tummyTime`, `storyTime`,
  `screenTime`, `skinToSkin`, `brushTeeth`, `indoorPlay`, `outdoorPlay`;
  manual duration and timer both add `duration` (seconds) + `end_offset`.
- Growth: `weightUnits` `kg` | `lbs.oz`, `heightUnits` `cm` | `ft.in` (the value
  is stored in feet), `headUnits` `hcm` | `hin`. The app converts the stored
  number when the unit is switched; fixtures replace the magnitudes with
  placeholders, so conversion cannot be derived from them.
- Temperature: `units` `C` | `F`, `amount`; the unit toggle needs a double tap
  through accessibility.
- Medicine: `health/<child>/data` with `medication_id`, `medication_name`,
  `units` (`ml`, `oz`, `drops`, `tsp`), `amount`; numpad precision follows the
  unit (mL 3 decimals, oz 2, tsp 1, drops integer). A blank amount is stored as
  `0.0` with the selected unit. `health/<child>/types` docs carry `name`,
  `mode`, `active`, and `lastTake` after first use.
- Every new interval carries `tz` (IANA zone). All interval models now accept it.
- `offset` / `end_offset` are minutes and still encode the capture zone
  (`-120`); they are schema fields, so they are kept.
- The activity timer patch for `bath` stores `startTime` slightly after
  `endTime`; that inversion is the app's own (the offset is applied uniformly),
  see `activity_bath_timer.json`.

## Not captured

- **Sleep manual entry** (start/end via wheel date-time pickers) and any
  interval with a populated `details` block (sleep conditions and locations).
  The timer flow produces the same interval document, which is covered.
- **Diaper `color`** (`PooColor`); the UI renders colour swatches without text
  or accessibility actions.
- **Bottle row with `amount` omitted**: the app retains the previous amount, so
  the form always writes one.
- **`foodNoteImage`**, **`multientry_key`**, and batched `multi` wrapper rows:
  not produced by this app version's UI.
- **Exact temperature value**: the value is a slider; the retained 37.0 °C is
  captured together with the °F conversion.
- **Exact activity duration**: the duration picker is a wheel; the picker's
  default value is committed and captured.
- **Medicine type selection**: rows in the "Medicine type" sheet have no
  accessibility actions; type creation is captured.
- **Activity notes**: the activity form has no note field in 0.9.316.

## Tooling notes

- Home tracker cards need touch swipes; the accessibility scroll action is a
  no-op on this WebView.
- Ionic segment buttons sometimes commit only on the second tap.
- Sheet content can duplicate page text; the harness picks the sheet copy by
  tree order.
- Wheel pickers (bottle type, sleep times) accept coordinate taps on rows.
