"""Validate the captured payload fixtures against the strict schema models.

The fixtures under ``tests/fixtures/payloads`` are redacted Firestore payloads
captured from the Android app (one app action per fixture). Created documents
are parsed with the model for their collection, and every captured key must be
declared by that model so the corpus fails when the app writes a field the
library does not model. Nested containers that are not modeled 1:1 (solids
per-food entries) are checked explicitly.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

import pytest

from huckleberry_api.firebase_types import (
    FirebaseActivityIntervalData,
    FirebaseBottleFeedIntervalData,
    FirebaseBreastFeedIntervalData,
    FirebaseCustomFoodTypeDocument,
    FirebaseDiaperData,
    FirebaseGrowthData,
    FirebaseMedicationData,
    FirebaseMedicationTypeDocument,
    FirebasePumpIntervalData,
    FirebaseSleepIntervalData,
    FirebaseSolidsFeedIntervalData,
    FirebaseTemperatureData,
)

FIXTURES = Path(__file__).parent / "fixtures" / "payloads"

MODELS = {
    "sleep": FirebaseSleepIntervalData,
    "bottle": FirebaseBottleFeedIntervalData,
    "breast": FirebaseBreastFeedIntervalData,
    "solids": FirebaseSolidsFeedIntervalData,
    "diaper": FirebaseDiaperData,
    "pump": FirebasePumpIntervalData,
    "activity": FirebaseActivityIntervalData,
    "medication": FirebaseMedicationData,
    "medicine-type": FirebaseMedicationTypeDocument,
    "growth": FirebaseGrowthData,
    "temperature": FirebaseTemperatureData,
    "custom-food": FirebaseCustomFoodTypeDocument,
}

PLACEHOLDERS = {
    "<child>": "child",
    "<note>": "note",
    "<medicine>": "item",
    "<food>": "item",
    "<tz>": "Etc/UTC",
    "<datetime>": "2026-01-01T00:00:00Z",
}

LIVE_ID = re.compile(r"^\d{13,}-[0-9a-f]{10,}$")

# Captures are shifted onto 2026-01-01T00:00:00Z. A value past this ceiling
# means a raw epoch (seconds or milliseconds) leaked through unshifted.
SYNTHETIC_CEILING = 1_770_000_000
SYNTHETIC_CEILING_MS = SYNTHETIC_CEILING * 1000

# SHA-256 of each word in the account and child display names (lowercase).
# Kept as hashes so the guard can catch a leaked name without committing the
# names themselves. Regenerate with the guard's exact fold:
#   python3 -c 'import hashlib,sys,unicodedata as u;
#   w=u.normalize("NFKD",sys.argv[1]).encode("ascii","ignore").decode().lower();
#   print(hashlib.sha256(w.encode()).hexdigest())' "$word"
FORBIDDEN_NAME_HASHES = {
    "46248ac689828800502186d8753cc5717c5c2b47712e8158705a510dc892f00b",
    "fff7754faccec4a2211f1ea00c31b9bbbfa765c1bde093cc7c3fe8b4181d7ddd",
    "9ac0b243574aaffd88073e9be7236806ecd8d039ddf24e986801aa78246396c5",
}


def load_fixtures():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["fixtures"]:
        path = FIXTURES / entry["tracker"] / f"{entry['id']}.json"
        yield entry, json.loads(path.read_text(encoding="utf-8"))


def resolve(value):
    if isinstance(value, dict):
        return {key: resolve(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item) for item in value]
    if isinstance(value, str):
        if value.startswith("<id"):
            return "00000000-0000-4000-8000-000000000000"
        return PLACEHOLDERS.get(value, value)
    return value


def model_key(document):
    path = document["path"]
    payload = document["payload"]
    if "/custom/" in path:
        return "custom-food"
    if "/types/" in path:
        return "medicine-type"
    if path.startswith("sleep/"):
        return "sleep"
    if path.startswith("feed/"):
        return payload.get("mode")
    if path.startswith("diaper/"):
        return "diaper"
    if path.startswith("pump/"):
        return "pump"
    if path.startswith("activities/"):
        return "activity"
    if path.startswith("health/"):
        return payload.get("mode")
    return None


def created_documents():
    for entry, fixture in load_fixtures():
        for document in fixture["documents"]:
            if document["change"] == "created":
                yield entry["id"], document


def all_documents():
    for entry, fixture in load_fixtures():
        for document in fixture["documents"]:
            yield entry["id"], document


def model_fields(model) -> set[str]:
    names = set()
    for name, field in model.model_fields.items():
        names.add(name)
        if field.alias:
            names.add(field.alias)
    return names


def identifier_tokens(text: str) -> list[str]:
    """ASCII-fold the text before tokenizing so accented names cannot split."""
    folded = unicodedata.normalize("NFKD", text).replace("\u2019", "'").encode("ascii", "ignore").decode()
    return re.findall(r"[A-Za-z][A-Za-z'\u2019-]{2,}", folded)


def walk_strings(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from walk_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_strings(value)
    elif isinstance(node, str):
        yield node


def walk_numbers(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from walk_numbers(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_numbers(value)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield float(node)


def walk_notes(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key.rsplit(".", 1)[-1] == "notes" and isinstance(value, str) and value:
                yield value
            yield from walk_notes(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_notes(value)


def test_manifest_matches_files():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    files = {path.relative_to(FIXTURES).as_posix() for path in FIXTURES.glob("*/*.json")}
    expected = {f"{entry['tracker']}/{entry['id']}.json" for entry in manifest["fixtures"]}
    assert files == expected
    assert manifest["count"] == len(manifest["fixtures"])


def test_manifest_describes_fixture_contents():
    for entry, fixture in load_fixtures():
        assert entry["documents"] == [doc["path"] for doc in fixture["documents"]]
        assert entry["changes"] == [doc["change"] for doc in fixture["documents"]]
        assert entry["action"] == fixture["action"]


@pytest.mark.parametrize(
    ("fixture_id", "document"),
    list(created_documents()),
    ids=[f"{fixture_id}:{document['path']}" for fixture_id, document in created_documents()],
)
def test_created_payload_matches_model(fixture_id, document):
    key = model_key(document)
    assert key in MODELS, f"{fixture_id}: no model for {document['path']}"
    payload = resolve(document["payload"])
    MODELS[key].model_validate(payload)

    unknown = set(payload) - model_fields(MODELS[key])
    assert not unknown, f"{fixture_id}: model does not declare {sorted(unknown)}"


def test_solids_food_entries_are_modeled():
    """Per-food entries are validated through the parent, so check keys here."""
    from huckleberry_api.firebase_types import SolidsFoodEntry

    declared = model_fields(SolidsFoodEntry)
    for fixture_id, document in created_documents():
        if not document["path"].startswith("feed/"):
            continue
        foods = document["payload"].get("foods") or {}
        for food in foods.values():
            unknown = set(food) - declared
            assert not unknown, f"{fixture_id}: SolidsFoodEntry does not declare {sorted(unknown)}"


def test_identifier_tokens_fold_accents():
    # Synthetic accented word: NFKD folding must run before tokenizing.
    assert identifier_tokens("A\u017eb") == ["Azb"]


def test_fixtures_contain_no_identifiers():
    """Guard every file in the corpus, including the manifest and docs."""
    for path in FIXTURES.rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            # Also scan decoded strings so JSON \u escapes cannot hide a name.
            text += "\n" + "\n".join(walk_strings(json.loads(text)))
        assert "@" not in text, f"{path.name} contains an email-like value"
        assert "Europe/" not in text, f"{path.name} contains a timezone"
        assert "api-" not in text, f"{path.name} contains test-account names"
        for value in re.findall(r'"([^"]+)"', text):
            assert not LIVE_ID.match(value), f"{path.name} contains a live document id"
        # Word-level only: a name fused inside a longer token is not covered,
        # but free-text fields are pinned to <note> by test_notes_are_redacted.
        for token in identifier_tokens(text):
            # Split inflections ("Name's", "Name-Smith") before hashing.
            for part in {token, *re.split(r"['\u2019-]", token)}:
                if len(part) < 3:
                    continue
                digest = hashlib.sha256(part.lower().encode()).hexdigest()
                assert digest not in FORBIDDEN_NAME_HASHES, f"{path.name} contains a personal name"


def test_timestamps_are_shifted():
    for path in FIXTURES.rglob("*"):
        if not path.is_file() or path.suffix != ".json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for value in walk_numbers(data):
            assert value < SYNTHETIC_CEILING_MS, f"{path.name}: unshifted epoch {value}"
            assert not SYNTHETIC_CEILING <= value < 1_900_000_000, f"{path.name}: unshifted epoch seconds {value}"


def test_notes_are_redacted():
    for fixture_id, document in all_documents():
        for note in walk_notes(document["payload"]):
            assert note == "<note>", f"{fixture_id}: unredacted note {note!r}"
