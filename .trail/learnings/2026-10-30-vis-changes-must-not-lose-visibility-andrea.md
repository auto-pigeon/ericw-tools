---
title: "Vis changes must not lose visibility; false positives are acceptable, false negatives are not"
author: Andrea-dintino
date: 2026-10-30
standing: true
id: l-0afc2a08
scope: repo
status: active
src: init-review
---

## Watch out
- Reviewers of the faster vis algorithm said the visible-leaf data must match known-good maps. Missing visible areas show up as visible holes in game, while extra visibility only costs performance.
- In the code: When changing vis/, compare vis data on known-good Q1 and Q2 maps. Keep new work behind `-targetchecks` (vis_options.targetratio > 0). Avoid `std::popcount` on the default path, since it caused a 20% slowdown.

_From PR #361 here, Dec 2._
