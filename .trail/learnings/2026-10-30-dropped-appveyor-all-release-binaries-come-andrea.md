---
title: Dropped AppVeyor; all release binaries come from GitHub Actions
author: Andrea-dintino
date: 2026-10-30
standing: true
id: l-2e1b1fbd
scope: repo
status: active
src: init-review
---

## Decided
- AppVeyor was about 2x slower or more, and maintaining two Windows CI flavors risked missing issues. Setting up vcpkg on GitHub Actions was enough work that a second CI system was not worth it.
- In the code: Do not re-add appveyor.yml. CI and release changes go in the GitHub Actions workflows only, and vcpkg installs deps including lua.

_From PRs #414, #406 here, Sep 20._
