# Agent-local qualification and light CI

Operator decision: 2026-11-02. This fork-specific policy controls the placement of the
verification described by the shared `TEST-SKILL.md`; older wording requiring broad tests
to run again in CI is superseded. It does not change release/tag immutability.

## Before the agent wraps up

Use small red-capable reproducers while repairing a defect. Freeze a coherent candidate,
then run the required full native suite once locally, the affected geometry/feature
acceptance, and relevant sanitizer checks. Run full extracted-archive acceptance and
source-archive rebuild qualification locally when compiler/build/dependency changes
affect them. Documentation/workflow-only changes need script and workflow contract
checks; they do not require rebuilding the compiler or rerunning its geometry corpus.

Record the exact candidate/tree and changed paths, compiler and dependency identities,
commands, exit codes, executed/failed/skipped counts, durations and durable report paths
in the canonical handoff. Distinguish fresh runs from reused valid evidence. A changed
relevant input invalidates its dependent evidence; a prose-only commit does not by itself
invalidate an unchanged compiler binary. Do not rerun completed predecessors or all test
stages because one stage failed. Fix the cause and repeat only affected stages, retaining
the failed attempt. Missing required local evidence remains incomplete, even if CI passes.

`build.py` defaults to full qualification. With task-owned paths outside the checkout:

```sh
python3 ci/auto-pigeon/selftest.py
python3 ci/auto-pigeon/plan.py --sha HEAD --out ../plan.json
python3 ci/auto-pigeon/fetch_deps.py --platform linux-amd64 --dest ../deps
python3 ci/auto-pigeon/build.py --platform linux-amd64 --deps ../deps --build-dir ../build --plan ../plan.json --report ../build-report.json
python3 ci/auto-pigeon/build.py --platform linux-amd64 --asan --deps ../deps --build-dir ../build-asan --plan ../plan.json --report ../asan-report.json
```

The README documents packaging and full `accept.py` (without `--smoke`). For a source
archive, unpack it into a fresh directory, run its documented build recipe in full mode,
and use `source_archive.py --mark-rebuilt` with that report. Preserve stage outputs to
resume valid work rather than rebuilding everything.

Sanitizer evidence must state its allocator and configuration: the normal TBB winding
allocator can hide some lifetime errors from ASan. For an allocator-sensitive repair,
also reproduce the affected case with instrumented standard allocations using the
supported build configuration; do not claim the default ASan run covered that path.
Native coverage required by a platform-sensitive change runs on an available native
local/test host. A Linux run does not establish Windows or macOS full-suite coverage;
report unavailable required coverage honestly.

## What automatic CI does

- Lightweight Python policy/script checks in Plan.
- Native release builds on the existing declared targets and one named GoogleTest
  regression per build (`--test-mode smoke`, 60-second test deadline).
- Packaging, architecture, dependency closure, manifests, provenance and immutable
  release checks remain in place.
- Fresh-runner extracted-archive smoke acceptance (`accept.py --smoke`): integrity,
  relocation, all six help entry points, one tiny sealed room through QBSP/VIS/LIGHT
  with solid/air probes, and one deliberately leaking room. Each tool invocation has
  a 60-second deadline and the acceptance job has a 10-minute budget.
- The corresponding-source archive is produced with its pinned dependencies and
  hashes. CI does not rebuild it a second time or mark it rebuild-verified.

CI does not run the full GoogleTest suite, ASan, broad geometry sweeps, repeated full
archive regressions or full-map VIS/LIGHT workloads. Do not restore those on push,
pull request, tags or automatic retries. A heavier CI diagnostic requires an explicit
operator request; use local execution otherwise. Native build/packaging time remains
separate from test time, so this policy is not a promise that every workflow finishes
within a few minutes.

Reports and release notes label `smoke` and `full` separately. A clean smoke-qualified
archive is releasable under this policy, with the agent responsible for required local
qualification. Local handoff evidence is not uploaded or independently attested by the
release workflow. `--skip-full-suite` remains an unqualified local iteration mode and
cannot make a releasable archive. Never describe a CI smoke pass as a full-suite or
sanitizer pass. Retain existing baseline failure and known-limit reporting.
