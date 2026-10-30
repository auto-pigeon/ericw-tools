---
title: Global stat counters in multithreaded vis caused a big slowdown
author: Andrea-dintino
date: 2026-10-30
standing: true
id: l-96869083
scope: repo
status: active
src: init-review
---

## Watch out
- After the C++ conversion, vis went from about 120s to 158s on a benchmark map. The largest single recovery came from moving global counters to per-thread, and making the per-stack data cheap to allocate again.
- In the code: In vis/, keep counters like c_chains per-thread and keep pstack_t cheap to stack-allocate (`qvec3d points[]`). Benchmark with a release build on gmsp3v2.bsp, since debug builds mislead.

_From PR #370 here, Nov 12._
