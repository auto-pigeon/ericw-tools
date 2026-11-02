---
title: A crash in the winding allocator can be a use-after-free the sanitizer cannot see
author: Andrea-dintino
date: 2026-11-02
branch: main
id: l-dfe32c21
scope: repo
status: active
src: agent
touches:
  - qbsp/brush.cc
  - include/common/polylib.hh
  - testmaps/q1_brush_past_world_extent.map
  - tests/test_qbsp.cc
---

## Decided
`CreateBrushWindings` validates a winding first and resets it afterwards. Resetting the optional inside the range-for freed the points being walked, and the code then moved from the empty optional, so the side kept freed points. It came from upstream.

## Tried and ruled out
AddressSanitizer on the stock build: it reports a SEGV inside `scalable_malloc`, far from the cause, because windings use TBB's allocator. A larger stack changed nothing. The size gdb printed in the allocator frames was misleading; the winding in the frame above was fine.

## Watch out
To let ASan see windings, build privately with `std::allocator` in place of `tbb::scalable_allocator` in `polylib.hh`: it then names the free at once. Exit 0 proves little on a hand-made map: check the BSP has clipnodes and the player's point is open, or a near-empty result passes and the test faults reading a hull that is not there.

In the code: `qbsp/brush.cc` `CreateBrushWindings`, `include/common/polylib.hh`, `testmaps/q1_brush_past_world_extent.map`, `tests/test_qbsp.cc` `testmapsQ1.brushPastWorldExtent`.
