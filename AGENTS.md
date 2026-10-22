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
