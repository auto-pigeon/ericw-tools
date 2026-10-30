---
title: "Lightmap offsets stay in the BSP's native format, even when writing extra HDR or lit lumps"
author: Andrea-dintino
date: 2026-10-30
standing: true
id: l-77a174ff
scope: repo
status: active
src: init-review
touches:
  - light/write.cc
---

## Watch out
- A review caught an HDR change that altered the lightmap offset to match the extra output. The offset must follow the BSP's own lightmap format: 1 byte per sample for Q1, 3 bytes for Q2 and Half-Life.
- In the code: In light/write.cc keep `lightofs = lit - lit_filebase.data()` for has_rgb_lightmap games. `bsp->dlightdata.size()` is bytes, so it is samples*3 when `has_rgb_lightmap` is true. Size .lit and BSPX lumps accordingly.

_From PRs #458, #421 here, Jun 30._
