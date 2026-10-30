---
title: "Texture info comparison must check every field, since it defines map-key equality"
author: Andrea-dintino
date: 2026-10-30
standing: true
id: l-3735dd18
scope: repo
status: active
src: init-review
touches:
  - include/qbsp/qbsp.hh
---

## Watch out
- A cleanup tried to compare fewer members when sorting texture info structs. Because the ordering decides equality, a lookup could return a wrong match, such as one with phong enabled when it was requested off.
- In the code: In include/qbsp/qbsp.hh, `mtexinfo_t::operator<` must compare all members. Use `std::tie` to build the comparison.

_From PR #316 here, Sep 13._
