## Auto-Pigeon fork of ericw-tools

This repository is the **Auto-Pigeon fork** of [ericwa/ericw-tools](https://github.com/ericwa/ericw-tools).
It follows upstream `main` and adds one compiler change, which upstream has not reviewed or accepted:
`qbsp`'s `SplitBrush` splits thin brush fragments with `PLANESIDE_EPSILON` instead of `0.1`, closing a
clip-hull leak through valid thin geometry (regression test `testmapsQ1.cliphull2ThinFragmentSplit`).
The branch `fix/thin-brush-splits` holds exactly that change on top of upstream, for an upstream pull request.
Everything below this section is upstream's own README.

### Downloads

Builds are on the [Releases page](https://github.com/auto-pigeon/ericw-tools/releases). Every push to `main`
that passes its gates publishes an **unsigned development prerelease** named `auto-pigeon-ericw-v1.<N>`,
where `<N>` is the number of commits in `main`.

| Target | Asset | Status |
| --- | --- | --- |
| Linux x86-64 | `auto-pigeon-ericw-tools-1.<N>-linux-amd64.zip` | required: no release without it |
| Windows x86-64 | `auto-pigeon-ericw-tools-1.<N>-windows-amd64.zip` | required: no release without it |
| macOS Intel | `auto-pigeon-ericw-tools-1.<N>-macos-amd64.zip` | published only when it passed on a native Intel runner |
| macOS Apple silicon | `auto-pigeon-ericw-tools-1.<N>-macos-arm64.zip` | candidate; published only when it passed natively |
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

The Companion runs compilers you installed; it does not download or bundle these. In the Companion open
**Profiles → New profile → A build tool**, start from the built-in ericw-tools profile, set each program's
*File inside its folder* to `bin/qbsp`, `bin/vis`, `bin/light`, `bin/bspinfo`, `bin/bsputil`, install and approve
it, then under *Where these programs are on this machine* give the unpacked
`auto-pigeon-ericw-tools-1.<N>-<target>` folder and press **Use this folder**. Keep an older toolchain in its own
folder and profile; do not overwrite one release with another.

### How a release is made, and rebuilding one

`.github/workflows/auto-pigeon-release.yml` is the only build and release path. It freezes the commit and
version once, builds and runs the whole GoogleTest suite natively per target (plus an AddressSanitizer run on
Linux), packages each target, then, on a fresh runner per target, unpacks the ZIP into a path with spaces
and compiles a sealed room, a deliberately leaking room and the thin-brush reproducer with the unpacked
programs. Only archives that passed are published, by one job, once; a rerun never replaces a published file.
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
