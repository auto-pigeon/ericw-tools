---
title: A leak at exactly the split tolerance came from the box side test rounding differently from SplitBrush
author: Andrea-dintino
date: 2026-11-06
branch: main
cost: { minutes: 29, tokens: 239877 }
id: l-623a2b6a
scope: repo
status: active
src: agent
touches:
  - AGENTS.md
  - README.md
  - qbsp/brushbsp.cc
---

## Decided
Two slabs exactly 0.001 (PLANESIDE_EPSILON) apart in a sealed room leaked through the room's wall in both tree modes (NEW_396); 0.0009 and 0.00101 sealed. The cause is two predicates disagreeing at the last bit, not a threshold: BoxOnPlaneSide's axial test compared a coordinate with `dist - PLANESIDE_EPSILON` (that sum rounds: 16.001 - 0.001 == 16.0, so a wall ending at z=16 was "front only" and skipped SplitBrush), while SplitBrush measured `16 - 16.001 = -0.0010000000000012` and cut the node VOLUME. The 0.001 child got the volume and none of the walls crossing it: an EMPTY film through solid. The repair makes the axial box test use the same `distance_to` arithmetic and the same `>=` boundary as SplitBrush. Any brush SplitBrush would split must be PSIDE_BOTH.

## Tried and ruled out
Lowering or widening PLANESIDE_EPSILON, or another SplitLeavesBrushFilm-style refusal: the planes here are exactly parallel and the film is a consistency error, not a near-coplanar split. The non-axial branch of BoxOnPlaneSide already agrees (front `>= eps` on the same dot product, back test is loose), and a 30-degree tilted slit never leaked.

## Watch out
A test generator that writes slabs by swapping two axes (normal along x or y) is a reflection: reverse the face points, or the brushes are inside out and the case silently "passes". Identity sweep over 189 committed Q1/Q2 maps: only the new witness changed; 5 maps (detail fences, tjunc matrix, hull1 content types) differ between two runs of the SAME binary, so don't read those as regressions.

In the code: qbsp/brushbsp.cc BoxOnPlaneSide (axial branch) vs SplitBrush/CheckSplitBrush; tests testmapsQ1.exactEpsilonSlit*; testmaps/q1_exact_epsilon_slit.map; evidence $MAPPER_ROOT/LLM/runs/auto-pigeon/NEW_396/.
