---
title: "A tiny portal fold can join leaves that do not touch, and only the fills may refuse the piece"
author: Andrea-dintino
date: 2026-11-01
branch: main
id: l-816a7e3b
scope: repo
status: active
replaces: l-7bc5bccb
src: agent
touches:
  - qbsp/portals.cc
  - qbsp/outside.cc
  - tests/test_qbsp.cc
---

## Decided
`WindingIsTiny` counts edges, so a needle hundreds of units long is "tiny". `SplitNodePortals` folds such a piece into the other child uncut, the child's portal then reaches past its own volume, and a piece split off the far end joins leaves with solid between them. The fold stays, and the plane it crossed is recorded in `buildportal_t::folds`. A later piece wholly past that plane stays an ordinary portal, marked `past_fold`, and only `OutsideFill_Passable` and `DetailFill_Passable` refuse it. Faces, VIS and the portal file cannot move: both sculpt maps compile byte-identical to 1.4536. A clip-hull false leak is the symptom: hull 0 stays sealed, and an edit anywhere can expose it by changing the split tree.

## Tried and ruled out
Cutting the portal at the needle: it changes hull 0 faces and fails `sculptTinyNodePortalDrawn` on Linux (`three_point` 6 > 5). Keeping the portal whole but making the far piece draw-only: green on Linux, but it leaves `MakeNodePortal`'s clipping and fails the same audit on Windows and macOS arm64 by one sliver. A Linux gate cannot show that: the count is float arithmetic. Neither bound was raised. Authored bent corridors and wedges never reproduced the bug: it needs a real split tree, so use a copy of the real map.

## Watch out
Decide `FoldReachesPastSplit` BEFORE `keep_fill_only`: that lambda moves the winding out of the optional, and reading it afterwards silently finds nothing. A map reduced under `-forcegoodtree` is only valid under it: `q1_folded_needle_corridor.map` leaks in hull 0 under the default tree with or without the repair, and a test loop over both modes fails fast, which looks exactly like a red run. Check the red failure stops in the hull you expect.

In the code: `qbsp/portals.cc` `SplitNodePortals`, `FoldReachesPastSplit`, `PieceIsPastAFold`; `qbsp/outside.cc`; `tests/test_qbsp.cc` `testmapsQ1.foldedNeedleDoesNotJoinLeaves`.
