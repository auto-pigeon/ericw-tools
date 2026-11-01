---
title: "A tiny portal fold can join leaves that do not touch, and the repair must leave drawn faces alone"
author: Andrea-dintino
date: 2026-11-01
branch: main
id: l-7bc5bccb
scope: repo
status: active
src: agent
touches:
  - qbsp/portals.cc
  - tests/test_qbsp.cc
---

## Decided
`WindingIsTiny` counts edges, so a needle hundreds of units long is "tiny". `SplitNodePortals` folds such a piece into the other child uncut, the child's portal then reaches past its own volume, and a piece split off the far end joins leaves with solid between them. The fold stays, for the faces. The plane it crossed is recorded in `buildportal_t::folds`, and a later piece wholly past that plane becomes draw-only, so no flood crosses it. A clip-hull false leak is the symptom: hull 0 stays sealed, and an edit anywhere can expose it by changing the split tree.

## Tried and ruled out
Cutting the portal at the needle and keeping the needle draw-only, or dropping it. It seals, but it changes hull 0 faces on tessellated patches and fails `sculptTinyNodePortalDrawn` (`three_point` 6 over a bound of 5). The bound was not raised. Authored bent corridors and wedges (about 9,000 maps) never reproduced it: it needs a real split tree, so reduce a real map instead of searching for a synthetic one.

## Watch out
Decide `FoldReachesPastSplit` BEFORE `keep_fill_only`: that lambda moves the winding out of the optional, and reading it afterwards silently finds nothing. A map reduced under `-forcegoodtree` is only valid under it: `q1_folded_needle_corridor.map` leaks in hull 0 under the default tree with or without the repair, and a test loop over both tree modes fails fast for that reason, which looks exactly like a red run. Check the red failure stops in the hull you expect. Reduction runs: `Processing hull 2` in a compile that has not leaked means sealed, so stop there.

In the code: `qbsp/portals.cc` `SplitNodePortals`, `FoldReachesPastSplit`, `PieceIsPastAFold`; `tests/test_qbsp.cc` `testmapsQ1.foldedNeedleDoesNotJoinLeaves`.
