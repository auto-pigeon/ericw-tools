## Auto-Pigeon fork of ericw-tools

This repository is the **Auto-Pigeon fork** of [ericwa/ericw-tools](https://github.com/ericwa/ericw-tools).
It follows upstream `main` and adds these compiler changes, which upstream has not reviewed or accepted:

- `qbsp`'s `SplitBrush` splits thin brush fragments with `PLANESIDE_EPSILON` instead of `0.1`, closing a
  clip-hull leak through valid thin geometry (`testmapsQ1.cliphull2ThinFragmentSplit`). The branch
  `fix/thin-brush-splits` holds exactly that change on top of upstream, for an upstream pull request.
- `AddBrushBevels` recognises a bevel that is one of the brush's own faces (`testmapsQ1.cliphullBevelDuplicatesFace`).
- `CheckSplitBrush` tests the node volume with `SplitBrush`'s tolerances. The first change alone (releases 1.4502 and
  1.4505) left a sliver's whole leaf SOLID: solid in the map's air beside non-planar geometry and the faces behind it not
  drawn, with no leak and exit 0 (`testmapsQ1.sliverFragmentPhantomSolid`).
- `LeafNode` gives a leaf a brush's contents only when the middle of the leaf is inside that brush, so a fragment that
  cannot be carved any further no longer makes a sheet of solid in open air (`testmapsQ1.fragmentLeafOutsideBrush`).
  Upstream 2.0.0-alpha11 has milder forms of the same defect, which this removes on the maps measured.
  `testmapsQ1.leafGuardKeepsMaterial` is the other side of that rule: thin material the map has must stay, and the room
  it seals must stay sealed.

- `SplitBrush` and `CheckSplitBrush` ask one shared question of the cap a plane cuts out of a brush, `SplitCapIsDegenerate`:
  does it have an interior (three points or more, finite, wider than `PLANESIDE_EPSILON`)? Both used to call
  `WindingIsTiny(w, 0.02)`, which wants three EDGES longer than 0.02 and so refused a strip 0.012 wide and as long as the
  room: the cap by which a side plane of a brush 0.012 thick cuts the slab that brush lies in. The slab stayed one leaf
  from wall to wall, and `LeafNode` then gave all of it the brush's contents or none of it. Releases up to 1.4513: a free
  fin 0.012 thick made half of its room solid with no leak and exit 0, and a plate 0.012 thick that alone closed a window
  was reported as a leak (`testmapsQ1.sub002FreeFin`, `sub002PlateSealsWindow`, and `sub002ThicknessLadder`,
  `sub002SplitTolerance`, `sub002MovedFin`, `sub002ObliqueSkin`, `sub002LiquidFilm`, `sub002CrossingFins`,
  `sub002OpenWindowStillLeaks`). `LeafNode`'s rule is unchanged. The same change draws the one face that
  was still partly missing on a 712-brush hollow pipe without `-forcegoodtree` (1,247 of 1,248 in release 1.4513, now
  1,248 in both tree modes, which write the same BSP for it); that map's clip nodes grew from 67,778 to 83,972, while
  three smaller hollow pipes got fewer.

- Every key/value line of an entity reaches the BSP entity lump where the `.map` wrote it, repeated keys included
  (`entdict_t` keeps each line; a lookup answers with a key's LAST occurrence, which is what a game ends up with). Games
  apply an entity's lines in order and `angle`/`angles` set one field, so `"angle" "0"`, `"angles" "0 90 0"`,
  `"angle" "195"` must face 195; releases up to 1.4519 kept a repeated key at its FIRST position with its last value
  (`"angle" "195"`, `"angles" "0 90 0"`, which Yamagi Quake II 8.70 spawns facing 90) and dropped the earlier
  occurrences, so a `team` or `targetname` spelled twice lost one. LIGHT and `-onlyents` rewrite the lump the same way
  (`testmapsQ2.orderedRepeatedKeys`, `testmapsQ1.orderedRepeatedKeys`, `testmapsQ1.orderedRepeatedKeysAliasDef`,
  `entdict.*`).

- `light -lmextents float32` sizes every face's lightmap with float arithmetic in the engine's order (each product and
  sum rounded to float, no fused multiply-add), which is how Yamagi Quake II 8.70's soft, GL1 and GL3 renderers size it
  on x86-64. A Quake II BSP stores no lightmap size, so the engine recomputes it; at a vertex whose texture coordinate
  lies on a 16-unit texel boundary the default (`extended`: an extended-precision sum rounded once, as upstream and as
  x87 engines) can give one column or row more or fewer than the engine, which then reads the face's samples with the
  wrong row length and, when it reads more, into the next face's samples. Measured on a BEND corridor compiled by
  release 1.4518 (8 of 172 faces) and on `testmaps/q2_lightmap_texel_boundary.map` (`ltfaceQ2.lightmapTexelBoundary`).
  The default is unchanged: choose the arithmetic of the engine that will load the map.

- A portal split no longer moves a whole strip of a wall to the wrong side of a nearly coplanar plane. `SplitNodePortals`
  and `BaseWindingForNode` clip with a 0.001 "on" epsilon; where a neighbouring cell's wall plane meets a portal at a
  small dihedral angle theta, everything within 0.001 / theta of the line they share (a strip about 1 unit wide at
  0.001 rad) was put on the side the rest of the winding lies on, so that strip's portal bounded a solid leaf instead of
  the room's air and its face was never drawn (collision and leaf contents were right). `ClipPortalWinding` re-clips at
  `NORMAL_EPSILON` only when the coarse clip put the whole winding on one side, and takes that verdict only for pieces
  that are not tiny and at least `EDGE_LENGTH_EPSILON` wide, so it makes no sliver the callers would drop. Measured on a
  Quake II AXIAL DEFORM corridor (an 0.8-unit undrawn strip on 4 cells, every release and upstream 2.0.0-alpha7/11) and
  on a synthetic bent wall (`testmapsQ2.bentWallPortalSplit`: 54 points undrawn before, 0 after). The tiny-portal rule
  itself is unchanged (see the crossing-fins limit below).
  The same epsilon also moved a split LINE: when it split a portal but one vertex was "on" only by the epsilon while lying
  well away from the planes' intersection (`EpsilonMovesSplitLine`), the split ran through that vertex and the triangle
  between it and the true intersection (74 units long, 0.37 wide on the Quake II AXIAL DEFORM 'organic' shell) went to
  the air in front of the next wall and was never drawn; there too the finer clip decides when both of its pieces are
  substantial (`testmapsQ2.curvedWallPortalSplitLine`).

- A portal the tiny-portal rule drops still draws its face. In this compiler a drawn face is a portal's winding
  (`FaceFromPortal`), and `MakeNodePortal` dropped a node portal with fewer than three edges longer than 0.2
  (`WindingIsTiny`), so the strip of a solid's face under such a portal was never drawn: on a SCULPT patch at
  tessellation 16 turned 45 degrees, two cap strips about 7 units long and 0.135 wide (2 of AUT's 7,366 sample points,
  every release up to 1.4524, Quake 1 and `-q2bsp` alike; collision and leaf contents were right). Such a portal, and
  the pieces `SplitNodePortals` lost when both were tiny, are now kept as **draw-only** portals
  (`KeepTinyPortalForFaces`): pushed down to the leaves, never linked into a node's portal list, read only by
  `MarkVisibleSides` and `MakeFaces`. The outside and area floods, VIS, the portal file and leaf bounds see the portals
  they saw before, so a hairline gap seals and fills exactly as it did (measured on the sculpt map: the same `.prt`
  bytes, nodes, leafs, brush sides, Quake II brushes and Quake 1 clip hulls; only faces differ). A tiny portal is kept
  only when the output can carry its face: three or more vertices after the 0.05 weld, some area, and wider than the
  T-junction pass's on-edge distance (`DEFAULT_ON_EPSILON`, 0.1) -- narrower, that pass threads one long edge's
  vertices into the other or into the next face and twists it (the 0.06 sides of a thin skin and of a water film:
  `testmapsQ1.leafGuardKeepsMaterial`, `sub002ObliqueSkin`). `testmapsQ2.sculptTinyNodePortalDrawn`,
  `testmapsQ1.sculptTinyNodePortalDrawn`; `testmapsQ1.hairlineCrackPocketUnchanged` holds a 0.15 crack into a closed
  pocket to what it was.

- A collision hull no longer splits a node that lies wholly inside one brush. In hulls 1 and 2 every brush is plain
  SOLID, so such a node can only ever hold SOLID leaves, and `PruneNodes` folded them back into one; but `BuildTree_r`
  went on splitting it on every plane of every other brush it overlapped. A hollow pipe's thin wall pieces, expanded by
  16 or 32 units, overlap one another by tens of units: on an 806-brush Auto-Pigeon PIPES map hull 1 grew 4,115,875
  leaves before pruning (14,309 after), and `qbsp -forcegoodtree` ran past 1,800 s and 11.3 GB where EricW 0.18.1 takes
  8 s. `NodeInsideOneSolidBrush` (`tree_t::clip_hull`, set only for hulls 1 and up) makes such a node a leaf: that map in
  99 s and 0.54 GB, a 248-brush one 70 s to 6.4 s. The exported clipnodes differ in layout (the flood that marks
  void-touching sides sees larger leaves), not in what they hold: hulls 0, 1 and 2 read the same contents as before at
  66 million jittered points over three such maps. `testmapsQ1.clipHullInsideOneBrushIsOneLeaf`
  (`testmaps/q1_clip_hull_hollow_tube.map`: past 300 s before, 12 s after; asserted from `-loghulls` statistics).

#### Known limits

- **A whole brush thinner than 0.001 units (`PLANESIDE_EPSILON`) is dropped.** It does not reach past its own far plane,
  so it is never cut out of its node, and `LeafNode`'s leaf-in-brush rule leaves it out: no material, and no solid in the
  map's air either. A room that only such a brush seals is reported as the leak it then is (`-leaktest` exit 1, no portal
  file). Measured: 0.00101 and up is compiled exactly, ordinary tree and `-forcegoodtree`; 0.00099 and 0.0005 are
  dropped (`testmapsQ1.sub002SplitTolerance`). The header comments of `testmaps/q1_brushbsp_sub002_free_fin.map` and
  `q1_brushbsp_sub002_plate_seals_window.map` describe what release 1.4513 did with them; the files are kept byte for
  byte so the same input can be put to an old archive and a new one.
- **Where two slabs thinner than 0.2 units cross, the column they share is filled.** Two fins 0.012 thick crossing at
  right angles are both right, and so is the room; the 0.012 x 0.012 column above and below the crossing is air in the
  map and SOLID in hull 0. BrushBSP carves it correctly (`-nofill` reads it EMPTY): every portal of a leaf that thin in
  two directions is "tiny" to the portal code (`WindingIsTiny`, 0.2 units) and is dropped, so the leaf cannot be reached
  and the outside filling takes it. Nothing is drawn there. `testmapsQ1.sub002CrossingFins` asserts everything around it
  and records the column's reading without asserting it. Changing that portal rule is what makes a map with hairline
  gaps between brushes leak, and it has not been done.
- `-lmextents float32` follows the engine's float arithmetic, not its range limits, which no lightmap layout can satisfy:
  Yamagi Quake II 8.70's GL renderers start a face's texture-coordinate bounds at 999999 / -99999 (soft: INT_MAX /
  INT_MIN), so a face whose texture coordinates all lie past them is sized differently by its own renderers, and every
  renderer keeps the lightmap origin (`texturemins`) in a 16-bit short, which wraps once a texture coordinate passes
  +/-32767 (measured: an offset of 65536 gives origin 0). Keep texture offsets small; AUT's Quake II oracle reports such
  faces by name.
- A brush whose two opposite faces coincide (zero thickness) makes `qbsp` crash while it builds the clip hulls (exit by
  signal, no BSP), exactly as upstream 2.0.0-alpha11 does. The editor does not write such a brush.
- With the default T-junction mode a drawn face can get a 0.03-unit spike (a vertex visited twice); upstream writes
  the same. On a finely tessellated patch it writes such slivers with a reversed or flat winding: 44 on
  `testmaps/q2_sculpt_tiny_node_portal.map` with release 1.4524, 45 with draw-only portals (one kept triangle 0.115 wide
  at its widest is narrower than 0.1 near its apex), and `CheckBsp` fails that map with `-tjunc none` too (5 windings
  whose first three points are collinear to its epsilon). The sculpt tests count both readings and bound them instead
  of asserting `CheckBsp`.
- **A face strip narrower than 0.1 units under a tiny portal is not drawn.** `KeepTinyPortalForFaces` keeps such a
  portal for its face only when the face is wider than the T-junction pass's on-edge distance; a 0.03 or 0.07 chamfer
  and the 0.06 sides of a thin skin stay undrawn, as in every earlier release.

Everything below the Auto-Pigeon sections is upstream's own README.

### Downloads

Builds are on the [Releases page](https://github.com/auto-pigeon/ericw-tools/releases). Every push to `main`
that passes its gates publishes an **unsigned development prerelease** named `auto-pigeon-ericw-v1.<N>`,
where `<N>` is the number of commits in `main`.

| Target | Asset | Status |
| --- | --- | --- |
| Linux x86-64 | `auto-pigeon-ericw-tools-1.<N>-linux-amd64.zip` | required: no release without it |
| Windows x86-64 | `auto-pigeon-ericw-tools-1.<N>-windows-amd64.zip` | required: no release without it |
| macOS Intel | `auto-pigeon-ericw-tools-1.<N>-macos-amd64.zip` | published only when it passed on a native Intel runner |
| macOS Apple silicon | `auto-pigeon-ericw-tools-1.<N>-macos-arm64.zip` | published only when it passed on a native arm64 runner (since 1.4505) |
| Source | `auto-pigeon-ericw-tools-1.<N>-source.tar.gz` | the commit with all submodules and the pinned GoogleTest |

There is no Windows or Linux arm64 build. A target missing from a release was not built or did not pass;
the release notes and `release-manifest.json` say which and why. Each release also carries `SHA256SUMS`.

Each ZIP unpacks to one folder:

```text
auto-pigeon-ericw-tools-1.<N>-<target>/
  bin/            qbsp vis light bspinfo bsputil maputil, with the Embree, oneTBB and (Windows) Visual C++
                  runtime libraries they load
  licenses/       the GPL texts and every shipped library's notices
  README.md       requirements and usage for that target
  build-info.json commit, upstream base, compiler, options, dependency versions and digests
  MANIFEST.json   every file with its size, mode and SHA-256
```

Requirements, measured per build and written into that archive's `README.md` and `build-info.json`:
Linux needs the glibc and libstdc++ of Ubuntu 22.04 or newer; Windows needs 64-bit Windows 10 or newer and
nothing installed; macOS states its minimum version from the shipped files. Nothing is installed system-wide
and nothing downloads or updates itself.

### Verify, unpack, run

Linux and macOS (`shasum -a 256 -c` on macOS):

```sh
cd "$HOME/Downloads"
sha256sum -c SHA256SUMS --ignore-missing
mkdir -p "$HOME/tools" && unzip auto-pigeon-ericw-tools-1.<N>-linux-amd64.zip -d "$HOME/tools"
T="$HOME/tools/auto-pigeon-ericw-tools-1.<N>-linux-amd64/bin"
"$T/qbsp" "$HOME/my maps/room.map"       # writes room.bsp and room.prt beside the map
"$T/vis" "$HOME/my maps/room.bsp"        # full VIS; -fast is the quick, lower-quality mode
"$T/light" "$HOME/my maps/room.bsp"
"$T/qbsp" -leaktest "$HOME/my maps/room.map"   # exit status 1 and a .pts file when the map leaks
```

Windows (PowerShell):

```powershell
cd "$env:USERPROFILE\Downloads"
(Get-FileHash .\auto-pigeon-ericw-tools-1.<N>-windows-amd64.zip -Algorithm SHA256).Hash.ToLower()   # compare with SHA256SUMS
Expand-Archive .\auto-pigeon-ericw-tools-1.<N>-windows-amd64.zip -DestinationPath "C:\Tools"
$T = "C:\Tools\auto-pigeon-ericw-tools-1.<N>-windows-amd64\bin"
& "$T\qbsp.exe" "C:\My Maps\room.map"
& "$T\vis.exe" "C:\My Maps\room.bsp"
& "$T\light.exe" "C:\My Maps\room.bsp"
```

Without `-leaktest`, `qbsp` exits 0 on a leaking map; the leak is the `.pts` file and the missing `.prt`.
Every program prints `ericw-tools <upstream version>+auto-pigeon.1.<N>` in its first lines. A SHA-256 shows
the download is intact; these builds are not signed.

### Use in the Auto-Pigeon Companion

The Companion runs compilers you installed; it does not download or bundle these. Keep each release in
its own folder and give it its own profile; do not overwrite one toolchain with another.

1. Unpack the ZIP for your system to a folder of its own, as above.
2. In the Companion open **Profiles → New profile**, choose **A build tool**, and describe the tool with the
   programs `bin/qbsp`, `bin/vis`, `bin/light`, `bin/bspinfo` and `bin/bsputil` (*File inside its folder*). Starting
   from the built-in ericw-tools profile keeps its actions and options; give the capabilities names of their own
   so the new tool sits beside the built-in one. Install it and approve it.
3. On its Configure page, under *Where these programs are on this machine*, enter the unpacked
   `auto-pigeon-ericw-tools-1.<N>-<target>` folder and press **Use this folder**.
4. Add a build pipeline whose stages name that tool, approve it, and pick it in **Build**.

The programs find their libraries beside themselves, so the Companion needs no library path.

### How a release is made, and rebuilding one

`.github/workflows/auto-pigeon-release.yml` is the only build and release path. It freezes the commit and
version once, builds and runs the whole GoogleTest suite natively per target (plus an AddressSanitizer run on
Linux), packages each target, then, on a fresh runner per target, unpacks the ZIP into a path with spaces
and compiles a sealed room, a deliberately leaking room, the thin-brush reproducer, the two sliver reproducers
and the thin-material control with the unpacked programs, asking each for its solid, its water and its air. Only archives that passed are published, by one job, once; a rerun never replaces a published file.
Pull requests run the same validation with a read-only token and publish nothing.

The steps are plain Python (3.8+, standard library) and run the same way on a workstation:

```sh
git clone --recurse-submodules https://github.com/auto-pigeon/ericw-tools.git && cd ericw-tools
python3 ci/auto-pigeon/selftest.py
python3 ci/auto-pigeon/plan.py --sha HEAD --out ../plan.json
python3 ci/auto-pigeon/fetch_deps.py --platform linux-amd64 --dest ../deps
python3 ci/auto-pigeon/build.py --platform linux-amd64 --deps ../deps --build-dir ../build --plan ../plan.json --report ../build-report.json
python3 ci/auto-pigeon/package.py --platform linux-amd64 --deps ../deps --build-report ../build-report.json --plan ../plan.json --out ../dist
python3 ci/auto-pigeon/accept.py --platform linux-amd64 --archive ../dist/auto-pigeon-ericw-tools-1.<N>-linux-amd64.zip \
    --package-json ../dist/auto-pigeon-ericw-tools-1.<N>-linux-amd64.package.json --plan ../plan.json \
    --work "../acceptance work" --report ../acceptance.json
```

`ci/auto-pigeon/deps.lock.json` pins Embree 4.4.0 and oneTBB 2021.11.0 by URL and SHA-256. The source archive
of a release rebuilds without Git history; see `AUTO-PIGEON-SOURCE.md` inside it. The compiled program bytes
are not claimed to be reproducible.

---

## ericw-tools
 - Website:         http://ericwa.github.io/ericw-tools
 - Documentation:
   - 2.0.0-alpha: https://ericw-tools.readthedocs.io
   - 0.18: [qbsp](https://ericwa.github.io/ericw-tools/doc/qbsp.html), [vis](https://ericwa.github.io/ericw-tools/doc/vis.html), [light](https://ericwa.github.io/ericw-tools/doc/light.html), [bspinfo](https://ericwa.github.io/ericw-tools/doc/bspinfo.html), [bsputil](https://ericwa.github.io/ericw-tools/doc/bsputil.html)
 - Maintainer:      Eric Wasylishen (AKA ericw)
 - Email:           ewasylishen@gmail.com

### Original tyurtils:

 - Website: http://disenchant.net
 - Author:  Kevin Shanahan (AKA Tyrann)
 - Email:   tyrann@disenchant.net

## About

ericw-tools is a branch of Tyrann's quake 1 tools, focused on
adding lighting features, mostly borrowed from q3map2. There are a few
bugfixes for qbsp as well. Original readme follows:

A collection of command line utilities for building Quake levels and working
with various Quake file formats. I need to work on the documentation a bit
more, but below are some brief descriptions of the tools.

Included utilities:

 - qbsp    - Used for turning a .map file into a playable .bsp file.

 - light   - Used for lighting a level after the bsp stage. This util was previously known as TyrLite

 - vis     - Creates the potentially visible set (PVS) for a bsp.

 - bspinfo - Print stats about the data contained in a bsp file.

 - bsputil - Simple tool for manipulation of bsp file data

See the doc/ directory for more detailed descriptions of the various
tools capabilities.  See changelog.md for a brief overview of recent
changes or https://github.com/ericwa/ericw-tools for the full changelog and
source code.

## Compiling

Required dependencies:
- [Embree 4](https://github.com/RenderKit/embree)
- [oneTBB](https://github.com/uxlfoundation/oneTBB)

Optional dependencies:
- Python, Sphinx (for building manuals)
- Qt 6 (for `lightpreview` GUI)

Bundled dependencies:
- [fmt](https://github.com/fmtlib/fmt)
- [jsoncpp](https://github.com/open-source-parsers/jsoncpp)
- [nanobench](https://github.com/martinus/nanobench)
- [pareto](https://github.com/alandefreitas/pareto)
- [GoogleTest](https://github.com/google/googletest)
- [stb_image](https://github.com/nothings/stb/blob/master/stb_image.h)
- [stb_image_write](https://github.com/nothings/stb/blob/master/stb_image_write.h)

### Ubuntu 24.04

NOTE: Builds using Ubuntu's embree packages produce a significantly slower `light` (i.e. over twice as slow) than ones released on Embree's GitHub. See `build-linux-64.sh` for a better method. 

```bash
sudo apt update
sudo apt install libembree-dev libtbb-dev cmake build-essential g++ qt6-base-dev
git clone --recursive https://github.com/ericwa/ericw-tools
cd ericw-tools
mkdir build
cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j 8

# run tests
./tests/tests

# print qbsp help
./qbsp/qbsp --help

# launch lightpreview gui
./lightpreview/lightpreview
```

### Windows, obtaining required dependencies via vcpkg

Open a `cmd` window. First, obtain vcpkg and build the dependencies:

```bat
git clone https://github.com/microsoft/vcpkg
cd vcpkg
.\bootstrap-vcpkg.bat
.\vcpkg.exe install embree tbb
cd ..
```

Next, clone the ericw-tools git repository + submodules:

```bat
git clone --recursive https://github.com/ericwa/ericw-tools
```

Open the `ericw-tools` folder in VS2022 (or higher) as a CMake project.

Go to "Project -> CMake Settings". Under "CMake Toolchain File", press the "..." button and browse to `vcpkg\scripts\buildsystems\vcpkg.cmake`. Then press "Save" to save your CMakeSettings.json.

Once CMake finishes, you should be able to select e.g. `qbsp.exe (qbsp\qbsp.exe)` in the "Select Startup Item" dropdown in the toolbar. (I had to restart VS).

#### IDE Tips - CLion

- Modify the "Google Test" run/debug configuration template to have `--gtest_catch_exceptions=0`, otherwise the  
  debugger doesn't stop on exceptions (segfaults etc.)

  (see: https://youtrack.jetbrains.com/issue/CPP-29559/Clion-LLDB-does-not-break-on-SEH-exceptions-within-GTest)

### macOS 10.15+

```
brew install embree tbb qt@6 cmake
python3 -m pip install sphinx_rtd_theme
git clone --recursive https://github.com/ericwa/ericw-tools
cd ericw-tools
mkdir build
cd build
cmake .. -DCMAKE_PREFIX_PATH="$(brew --prefix embree);$(brew --prefix tbb)" -DCMAKE_BUILD_TYPE=Release
make
```

## Credits

- Kevin Shanahan (AKA Tyrann) for the original [tyrutils](http://disenchant.net/utils)
- id Software (original release of these tools is at https://github.com/id-Software/quake-tools) 
- rebb (ambient occlusion, qbsp improvements)
- q3map2 authors (AO, sunlight2, penumbra, deviance are from [q3map2](https://github.com/TTimo/GtkRadiant/tree/master/tools/quake3/q3map2))
- Spike (hexen 2 support, phong shading, various features)
- MH (surface lights based on MHColour)
- mfx, sock, Lunaran (testing)
- Thanks to users at [func_msgboard](http://www.celephais.net/board/forum.php) for feedback and testing

## License

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program; if not, write to the Free Software
    Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

Builds using Embree are licensed under GPLv3+ for compatibility with the
Apache license.
