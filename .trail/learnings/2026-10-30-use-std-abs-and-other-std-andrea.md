---
title: "Use std::abs and other std-prefixed math, not the C versions"
author: Andrea-dintino
date: 2026-10-30
standing: true
id: l-fc72bb83
scope: repo
status: active
src: init-review
---

## Watch out
- Unqualified abs() can resolve to the integer C version on some platforms, such as arm64 Debian. That breaks the build or gives wrong results.
- In the code: Use `std::abs` etc. Add such functions to the `using std::max, std::min, std::clamp;` line in mathlib.hh.

_From PR #369 here, Aug 6._
