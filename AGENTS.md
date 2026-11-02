# AGENTS.md — the Auto-Pigeon fork of ericw-tools

This checkout is `ericw-tools`, alias **ERICW**: the Auto-Pigeon fork of
[ericwa/ericw-tools](https://github.com/ericwa/ericw-tools). The pinned compiler releases
(`auto-pigeon-ericw-v1.<N>`) that AUT's compiler oracle and the Companion's fork profiles name are
built from `main` here. Everything outside `ci/auto-pigeon/`, `.github/workflows/auto-pigeon-release.yml`,
this file, `TEST-SKILL.md` and the Auto-Pigeon sections at the top of `README.md` is upstream's tree.

The workspace root (`../AGENTS.md`) and `$MAPPER_ROOT/LLM/WORKFLOW.md` bind here as in every sibling.
This repository has no prompt queue of its own in practice: a prompt filed in another queue names it
with `execution_repository: ericw-tools`, and its handoff is written with
`python3 ../auto-pigeon-tools/scripts/agent_task.py checkpoint --repo-root "$PWD" --queue <queue> --prompt <prompt>.md`.

## Local qualification and light CI (operator decision, 2026-11-02)

Read `ci/auto-pigeon/TESTING.md` with `TEST-SKILL.md`. For this fork it specifies where
qualification runs: broad suites, sanitizers, geometry sweeps and source-archive rebuilds are
agent-local wrap-up work; automatic CI performs native builds and bounded smoke checks.
This explicit operator decision supersedes older requirements to repeat broad tests in CI.
Keep results honest: a CI smoke pass does not establish full-suite or sanitizer coverage.

## Rules that bind every task here

- **Read `TEST-SKILL.md` before selecting, resuming or qualifying tests** — the shared Auto-Pigeon
  testing skill (canonical: AUT `skills/auto-pigeon-testing/SKILL.md`). A documentation-only change
  here does not build the compilers; a compiler change keeps its native tests and release gates.
- **A published release is immutable.** Never replace a release's assets, move its tag or rewrite
  history. A correction is a new commit and the release the workflow computes from it; the version is
  `1.<commits in main>` and is read off `ci/auto-pigeon/plan.py`, never guessed.
- **Every push to `main` that passes its gates publishes.** So do not push an experimental or failing
  candidate, and do not commit or push unless the active prompt asks for it.
- **Do not touch upstream.** `upstream` is ericwa's repository; no task here updates its pull request
  or its branches unless the prompt names that.
- **A geometry test asserts what the source map describes**, not what a previous compiler emitted, and
  asks both questions: does source air stay air, and does source material stay present. No new skip or
  disabled test for geometry; a known limit is written in `README.md` under *Known limits*.
- **Compiler changes are kept narrow and named.** Each one has a `testmapsQ1.*`/`testmapsQ2.*` test in
  `tests/test_qbsp.cc`, a committed map under `testmaps/`, and a line in the README's fork section.
- **Build and run output stays out of the checkout** — a task-owned directory under
  `$MAPPER_ROOT/LLM/runs/`, or a scratch build directory outside the tree. `qbsp` has no `-threads`
  argument; keep concurrent compiles few.

## Where things are

| What | Where |
| --- | --- |
| the brush BSP split, predictor and leaf contents | `qbsp/brushbsp.cc` |
| native tests, and the maps they compile | `tests/test_qbsp.cc`, `testmaps/` |
| build + test one target, with the report CI reads | `ci/auto-pigeon/build.py` |
| extracted-archive acceptance, per published target | `ci/auto-pigeon/accept.py` |
| release plan, packaging, publication policy | `ci/auto-pigeon/plan.py`, `package.py`, `publish.py` |
| the CI scripts' own self-test | `ci/auto-pigeon/selftest.py` |

<!-- trail:start -->
## Trail — repo context graph

This repo is indexed in `.trail/cache/`: small linked markdown nodes that explain each
system and carry exact file:line spans, kept in sync with the code through git.

For ANY task here — understanding how something works, finding where code lives,
or scoping a change — get context from the graph before grepping or opening
source files. Re-ask freely (it's cheap) and reuse literal identifiers you
already have (symbol, error string, file name) as the query. New to this repo?
Run `trail map` first — a token-budgeted orientation (dir clusters, hubs,
hotspots), no LLM, no key.

- Run `trail ask "<your question>" --source` → ranked nodes with the relevant
  code spans inlined (each hit's ≤8-line crux by default; `--full` for whole
  definitions when the crux isn't enough). Match the tool to the task shape:
  for understanding or editing, the top node IS the answer — cite its
  `covers:` file:line spans and edit straight from `--source`. For
  exhaustive tasks ("every occurrence / every caller of this pattern"), ranked
  results are top-N, not complete — run `trail grep "<literal>"` instead
  (exhaustive over indexed files, grouped by enclosing symbol), falling back
  to raw `grep -rn` only for unindexed files.
- `trail skeleton <file>` → every definition's signature + span, ~10× cheaper
  than reading the file; use it to skim an API surface.
- `trail callers <symbol>` gives precomputed, exact edges — who calls this.
  Add `--direction out` for what it calls, or `--depth N` to walk
  transitively for the full blast radius. For structural questions, skip
  ranking and use this directly.
- Or browse: `.trail/cache/INDEX.md` lists every node; follow the links.
- Monorepos and folders of multiple repos rank fairly across sub-projects —
  hits carry `[scope/]` labels naming which one they're from. Narrow with
  `trail ask "<task>" --in <scope>/` once you know where you're working.

If a returned span is truncated ("+N more lines"), open the file at that exact
range before finalizing. Only open source files when a node genuinely lacks a
needed detail, and then at the exact file:line the node points to — never
re-read whole files.

After big code changes, refresh the graph with `trail build` (deterministic,
no API key, $0).
<!-- trail:end -->

