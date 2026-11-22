# Contributing

Thanks for your interest in contributing to `huckleberry-api`. This guide describes the workflow used for external contributions. For coding rules specific to this codebase (schema validation, strict typing, reverse-engineering discipline), see [`AGENTS.md`](./AGENTS.md) — it is the source of truth and is not duplicated here.

## Getting set up

1. [Fork the repo](https://github.com/Woyken/py-huckleberry-api/fork) on GitHub.
2. Clone your fork and add `upstream`:

   ```bash
   git clone git@github.com:<you>/py-huckleberry-api.git
   cd py-huckleberry-api
   git remote add upstream https://github.com/Woyken/py-huckleberry-api.git
   ```

3. Install dependencies with [`uv`](https://docs.astral.sh/uv/):

   ```bash
   uv sync --dev
   ```

Python 3.14+ is required (see `pyproject.toml`).

## Proposing changes

- **Small fixes** (typos, docs, obvious bugs): go straight to a PR.
- **Anything larger** (new features, behavior changes, refactors): open an issue first to check scope and approach before investing time.

## Making a change

1. Sync your fork's `main` with upstream before branching:

   ```bash
   git fetch upstream
   git checkout main
   git merge --ff-only upstream/main
   git push origin main
   ```

2. Branch from `main`. Naming is informal — recent examples: `feat/activity-support`, `fix/<short-description>`, `docs/<short-description>`.

3. Make your change. Follow the rules in [`AGENTS.md`](./AGENTS.md) — in particular:
   - Use `uv` for every Python command (`uv run pytest ...`, `uv run ruff ...`).
   - Validate Firebase schema values against evidence (APK decompilation in `jadx output latest/` or live data) before adding or changing them.
   - Prefer strict types; avoid broad `Any`/open dicts.

4. **Add a towncrier fragment for user-visible changes.** CI rejects PRs without one (see `.github/workflows/pr-validation.yml`). Create it either via towncrier or manually:

   ```bash
   uv run towncrier create <id-or-slug>.<type>.md
   ```

   Fragments live in `newsfragments/`. Filename format is `<id-or-slug>.<type>` where `<id>` is the associated issue number (preferred) or a short kebab-case slug, and `<type>` is one of: `feature`, `bugfix`, `doc`, `removal`, `misc`. The file content is a one-or-two-sentence changelog blurb.

## Local checks

Run these before pushing — they are the exact checks PR Validation runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check --python-version 3.14 --ignore unknown-argument
```

If `ruff format --check` fails, run `uv run ruff format .` to fix.

## Tests

Unit and integration tests live in `tests/` — see [`tests/README.md`](./tests/README.md) for details. Integration tests hit real Firebase and need Huckleberry account credentials:

```bash
export HUCKLEBERRY_EMAIL="you@example.com"
export HUCKLEBERRY_PASSWORD="..."
uv run pytest tests/ -v
```

If you don't have a Huckleberry account, you can still submit a PR. The maintainer will run integration tests before merging — **on fork PRs, the Integration Tests workflow cannot access repo secrets and its run on your PR will be incomplete.** PR Validation (lint/format/type-check) runs normally on fork PRs and must be green.

## Opening the PR

- Base: `Woyken/py-huckleberry-api:main`. Head: `<your-fork>:<your-branch>`.
- Reference the issue if one exists (`Closes #NN`).
- Keep the diff focused. If you discover unrelated work along the way, open a separate PR for it.
- Push additional commits to the same branch in response to review; don't force-push unless asked.

## Release process (maintainer-only, FYI)

Releases are cut by running `uv run towncrier build --yes --version <version>`, which consumes the fragments from `newsfragments/` into `CHANGELOG.md`. Contributors only add fragments; they never run `towncrier build`.

## License

By contributing, you agree that your contributions are licensed under the MIT License that covers this project.
