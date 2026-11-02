---
title: continue-on-error does not forgive a cancelled job
author: Andrea-dintino
date: 2026-11-02
branch: main
cost: { minutes: 45 }
id: l-ed58cb55
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/auto-pigeon-release.yml"
  - ci/auto-pigeon/test_ci_policy.py
---

## Decided
Required and optional targets are two job chains, `build` -> `accept` and `build-candidate` -> `accept-candidate`, sharing their steps through YAML anchors, and `publish` joins them with `!cancelled()` plus `needs.accept.result == 'success'` and `needs.source.result == 'success'`. `continue-on-error` turns a failed job into a success in the matrix aggregate (run 38218905516 published without macOS Intel), but a job GitHub never gave a runner concludes `cancelled` with no steps, and that aggregate is not success, so in one shared matrix it skipped acceptance and publication for every target (run 41418779022).

## Tried and ruled out
A job-level `always()`: it also runs after the operator cancels the workflow. One `if: !cancelled()` on a shared `accept` matrix: the required acceptance would then depend on what `cancelled()` returns for an individually cancelled ancestor, and the aggregate could no longer gate publication, because one cancelled candidate acceptance makes it non-success. A selector job with a dynamic matrix: it saves a queued macOS acceptance for a target with no archive, at the cost of a new job, an `actions: read` token and an API dependency.

## Watch out
GitHub documents `cancelled()` only as the workflow being cancelled. What a job-level `!cancelled()` returns when one ancestor was cancelled on its own was neither documented nor found in public run history, so read a workflow's file at the run's own `head_sha` before citing a run as evidence: a playwright run looked like proof until its file at that commit turned out to have no such condition. The first run with a cancelled candidate settles it; if `publish` is skipped there, it failed closed and required acceptance still ran.
