---
title: Switched JSON library to jsoncpp via vcpkg to cut build times
author: Andrea-dintino
date: 2026-10-30
standing: true
id: l-9b8f04b4
scope: repo
status: active
src: init-review
---

## Decided
- Header-only nlohmann json slowed compilation. jsoncpp is stalled upstream and a bit flaky, but it builds faster.
- In the code: Use jsoncpp (vcpkg), not nlohmann. On macOS, 64-bit sizes need an explicit cast, e.g. `static_cast<Json::UInt64>(...)`.

_From PR #414 here, Sep 20._
