---
title: "A leak through solid on a re-bent corridor can be a sliver between two nearly coincident split planes, or a brush face written several times; the bogus-brush w…"
author: Andrea-dintino
date: 2026-11-03
branch: main
id: l-08b1dc77
scope: repo
status: active
src: agent
touches:
  - qbsp/brushbsp.cc
  - qbsp/brush.cc
  - tests/test_qbsp.cc
  - testmaps/q1_near_coplanar_film.map
---

## Decided
Two separate compiler defects, two narrow repairs (NEW_390). (1) A node split by plane A, then by a plane B a few ten-thousandths away and a hundred-thousandth of a radian off: every brush A cut is kept whole on the far side of B (it reaches past B by less than PLANESIDE_EPSILON), so the wedge between them is one EMPTY leaf through solid that cannot be carved again, and the flood leaks through it. Such a split plane is refused in that node (SplitLeavesBrushFilm). (2) Faces of one brush within -epsilon of one another (distinct planes to the loader, normals more than 1e-6 apart) all lost their windings in CreateBrushWindings; the brush had no side there, caps became world-sized and halves bogus, brushes were removed from a split. Nearly coincident same-facing planes are clipped last and the earlier side keeps the face.

## Tried and ruled out
Taking "huge winding / 3 bogus brushes" in seed 1017's log as the cause: those brushes were elsewhere and dense sampling found nothing lost there; 1017's leak is (1). Random sampling of material and air: it never lands in a 0.0005-thick film; only sampling between pairs of nearly coincident source planes is red. Auditing a -nofill compile: the defect is in the second, PRECISE tree built after the first fill marks void sides, which -nofill never builds. Delta-debugging the corridor: every chunk removal opens the closed tube. Synthetic duplicate faces closer than 5e-5: the loader merges them (DIST_EPSILON), so nothing is lost.

## Watch out
A seeded search over generated scenes found the reproducers (80 of 400 rotated thin-floor rooms leak with (1) off; a pillar whose top is written three times vanishes with (2) off). An open-sided brush in a turned map collapses hull 0 to two leaves with exit 0, on 1.4549 too: not fixed. Each repair alone: (1) seals seeds 1017, 1025, 1143; (2) seals 1184; 1044 is a real opening.

In the code: qbsp/brushbsp.cc SplitLeavesBrushFilm, SelectSplitPlane, ChooseMidPlaneFromList; qbsp/brush.cc CreateBrushWindings; tests/test_qbsp.cc testmapsQ1.nearCoplanarSplit*, coincidentFacesKeepTheirBrush; testmaps/q1_near_coplanar_film.map.
