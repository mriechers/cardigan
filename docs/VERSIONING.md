# Versioning

Cardigan uses **git tags** as the single source of truth for the application
version. The Python package, the FastAPI app, the Docker images, and the cost
data epoch all read from the same place.

## How it works

```
git tag (vX.Y.Z)
    │
    ▼
setuptools_scm  ─►  api/_version.py  ─►  api/__init__.py  ─►  __version__
                          │                                       │
                          │                                       ▼
                          │                              FastAPI(version=…)
                          │                              /api/system/health
                          │                              CARDIGAN_VERSION default
                          ▼
                   Docker build-arg
                   (CI computes via `python -m setuptools_scm`)
```

- `pyproject.toml` declares `version` as `dynamic` and points
  `[tool.setuptools_scm]` at `api/_version.py`.
- `api/_version.py` is **gitignored** — it is regenerated on every build.
- In CI, `python -m setuptools_scm` resolves the version from the latest
  `vX.Y.Z` tag plus distance/sha and passes it to Docker via
  `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_EDITORIAL_ASSISTANT`.
- At runtime, `api/services/database.py` derives the cost-data
  `app_version` (e.g. `v4.1`) from `__version__` unless `CARDIGAN_VERSION`
  is set explicitly.
- `web/package.json` carries a static version string that npm requires.
  CI's `version-consistency` job (see `scripts/check_versions.py`) enforces
  that its MAJOR.MINOR matches the latest git tag.

## When to bump — threshold table

The team has been undisciplined about version bumps in the past. These rules
make it explicit. Bump when **any** row applies; pick the highest precedence.

| Bump   | When                                                         | Examples                                                                 |
| ------ | ------------------------------------------------------------ | ------------------------------------------------------------------------ |
| MAJOR  | Breaking API change, schema migration users must coordinate  | `v4 → v5`: ingest API rewrite, change to job state machine              |
| MINOR  | Sprint completion with user-visible behavior change          | `v4.0 → v4.1`: cost data preservation sprint, multi-arch images         |
| PATCH  | Bug fix or operator-visible change to a shipping behavior    | `v4.1.0 → v4.1.1`: ingest scanner crash fix, prompt routing fix         |
| _none_ | Pure refactor, internal docs, dev-tooling, CI changes        | Workflow YAML edits, test-only changes, README updates                   |

A few sharper edges worth calling out:

- **No-op refactors don't bump.** If behavior is identical, do not tag. The
  next real change picks up the bump. Avoid version-noise commits.
- **Cost epoch changes are MINOR.** Anything that materially shifts model
  selection, retry strategy, or pricing assumptions is a new pricing era.
  Bump MINOR so the `app_version` tag in `jobs` / `chat_sessions` separates
  cleanly. See `docs/COST_DATA_VERSIONING.md`.
- **DB schema changes don't automatically bump.** If users won't notice (a
  new index, a nullable column with a backfill), no bump. If users _will_
  notice (a re-keyed table, a re-run requirement), MINOR or MAJOR.
- **Frontend-only releases bump too.** If `web/` ships a meaningful change
  to the dashboard, bump PATCH and the consistency check will keep
  `web/package.json` aligned.

## How to release

1. Confirm `main` is green, the work you want to ship is merged, and the
   release notes are ready.
2. Tag locally with an annotated tag:
   ```bash
   git tag -a v4.1.1 -m "v4.1.1: <one-line summary>"
   git push origin v4.1.1
   ```
3. The `Deploy` workflow runs on push to `main`, so tagging itself does
   not trigger a build. To redeploy with the new version baked in, push
   any commit to `main` (or merge a release-notes PR that touches at
   least one file).
4. Verify the deployed version after Watchtower picks up the new image:
   ```bash
   curl -s http://<host>/api/system/health | jq '.version // .status'
   curl -s http://<host>/ | jq '.version'
   ```
5. If you bumped MAJOR or MINOR and a cost epoch change is part of it,
   follow `docs/COST_DATA_VERSIONING.md` for the snapshot/backfill flow.

## How to bump web/package.json

When you tag a new MAJOR.MINOR (e.g. `v4.2.0`), update
`web/package.json` and `web/package-lock.json` to match in the same PR.
PATCH-level drift is tolerated — the CI consistency check only enforces
MAJOR.MINOR.

```bash
cd web
npm version --no-git-tag-version 4.2.0
```

## Local development

If you check out the repo without running a build, `api/_version.py`
won't exist. `api/__init__.py` falls back to `"0.0.0+unknown"` so imports
never fail. To populate the file:

```bash
pip install -e .  # writes api/_version.py via setuptools_scm
```

`./init.sh` does this for you on every dev session.

## Docker builds

Local builds work three ways:

1. **With `.git` available in the build context** — setuptools_scm reads
   the tag directly. (Default is to exclude `.git` via `.dockerignore`,
   so this requires removing the entry temporarily.)
2. **With a build-arg** — pass the version explicitly:
   ```bash
   docker build \
     -f Dockerfile.api \
     --build-arg SETUPTOOLS_SCM_PRETEND_VERSION_FOR_EDITORIAL_ASSISTANT=$(python -m setuptools_scm) \
     -t cardigan-api .
   ```
3. **Without either** — falls back to `0.0.0+unknown`. Fine for one-off
   smoke tests; never ship this to production.

CI always uses option (2).

## Why setuptools_scm and not a hand-edited version string

We tried hand-edited version strings. Four files drifted (api/main.py,
api/__init__.py, web/package.json, FastAPI title). Bumping consistently
turned into a checklist nobody followed. setuptools_scm collapses the
checklist to one action — `git tag` — and the rest follows.
