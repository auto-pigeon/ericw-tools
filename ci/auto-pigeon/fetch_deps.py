#!/usr/bin/env python3
"""Download, verify and unpack the pinned Embree and oneTBB for one target.

    python3 ci/auto-pigeon/fetch_deps.py --platform linux-amd64 --dest <dir> [--downloads <cache dir>]

Writes <dest>/deps.json: where each library's CMake package, runtime libraries and licences are,
and the identity (URL, SHA-256, version, source commit) of everything that was used.
"""

import argparse
import glob
import os
import pathlib
import shutil
import sys
import tarfile
import urllib.request
import zipfile

from common import HERE, PLATFORMS, Failure, load_json, log, require_native, run, sha256_file, write_json


def download(url, sha256, cache):
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / url.rsplit("/", 1)[1]
    if not (target.exists() and sha256_file(target) == sha256):
        log("downloading %s" % url)
        tmp = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(tmp, target)
    got = sha256_file(target)
    if got != sha256:
        raise Failure("SHA-256 mismatch for %s: expected %s, got %s" % (url, sha256, got))
    log("verified %s  %s" % (sha256, target.name))
    return target


def unpack(archive, dest, platform):
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    name = archive.name
    if name.endswith((".tgz", ".tar.gz")):
        with tarfile.open(archive) as t:
            try:
                t.extractall(dest, filter="tar")
            except TypeError:  # Python < 3.12
                t.extractall(dest)
    elif platform.startswith("macos"):
        # zipfile would write each symbolic link as a file holding the link text
        run(["unzip", "-q", archive, "-d", dest])
    else:
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    entries = [p for p in dest.iterdir()]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else dest


def one(pattern):
    hits = sorted(glob.glob(str(pattern)))
    if len(hits) != 1:
        raise Failure("expected exactly one match for %s, found %d" % (pattern, len(hits)))
    return pathlib.Path(hits[0])


def build_onetbb_from_source(lock, spec, dest, jobs):
    src = dest / "onetbb-src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    run(["git", "init", "-q"], cwd=src)
    run(["git", "fetch", "-q", "--depth", "1", lock["source_repository"] + ".git", lock["source_commit"]], cwd=src)
    run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=src)
    head = run(["git", "rev-parse", "HEAD"], cwd=src, capture=True)[1].strip()
    if head != lock["source_commit"]:
        raise Failure("oneTBB source is at %s, the lock pins %s" % (head, lock["source_commit"]))
    prefix = dest / "onetbb"
    run(["cmake", "-S", src, "-B", src / "build", "-DCMAKE_INSTALL_PREFIX=%s" % prefix, *spec["cmake_options"]])
    run(["cmake", "--build", src / "build", "--parallel", str(jobs)])
    run(["cmake", "--install", src / "build"])
    shutil.copy2(src / "LICENSE.txt", prefix / "LICENSE.txt")
    shutil.copy2(src / "third-party-programs.txt", prefix / "third-party-programs.txt")
    return prefix, {"built_from_source": True, "source_commit": head, "cmake_options": spec["cmake_options"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", required=True, choices=PLATFORMS)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--downloads", help="download cache directory (default <dest>/downloads)")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    platform = args.platform
    require_native(platform)
    dest = pathlib.Path(args.dest).resolve()
    cache = pathlib.Path(args.downloads).resolve() if args.downloads else dest / "downloads"
    lock = load_json(HERE / "deps.lock.json")

    e = lock["embree"]
    eb = e["binaries"][platform]
    embree = unpack(download(eb["url"], eb["sha256"], cache), dest / "embree", platform)
    embree_id = {"version": e["version"], "license": e["license"], "url": eb["url"], "sha256": eb["sha256"],
                 "source_repository": e["source_repository"], "source_commit": e["source_commit"]}

    t = lock["onetbb"]
    tbb_id = {"version": t["version"], "license": t["license"], "source_repository": t["source_repository"],
              "source_commit": t["source_commit"]}
    if platform in t["binaries"]:
        tb = t["binaries"][platform]
        tbb = unpack(download(tb["url"], tb["sha256"], cache), dest / "onetbb", platform)
        tbb_id.update({"url": tb["url"], "sha256": tb["sha256"], "built_from_source": False})
    else:
        tbb, extra = build_onetbb_from_source(t, t["source_builds"][platform], dest, args.jobs)
        tbb_id.update(extra)

    if platform == "windows-amd64":
        tbb_runtime = [tbb / "redist" / "intel64" / "vc14"]
        embree_runtime = [embree / "bin"]
    elif platform == "linux-amd64" and (tbb / "lib" / "intel64" / "gcc4.8").is_dir():
        tbb_runtime = [tbb / "lib" / "intel64" / "gcc4.8"]
        embree_runtime = [embree / "lib"]
    else:
        tbb_runtime = [tbb / "lib"]
        embree_runtime = [embree / "lib"]

    deps = {
        "schema": "auto-pigeon-ericw-deps-resolved/1",
        "platform": platform,
        "embree": dict(embree_id, cmake_dir=str(one(embree / "lib" / "cmake" / "embree-*")),
                       runtime_dirs=[str(p) for p in embree_runtime],
                       license_files={"LICENSE-embree.txt": str(embree / "doc" / "LICENSE.txt"),
                                      "third-party-programs-embree.txt": str(embree / "doc" / "third-party-programs.txt")}),
        "onetbb": dict(tbb_id, cmake_dir=str(tbb / "lib" / "cmake" / ("TBB" if (tbb / "lib" / "cmake" / "TBB").is_dir() else "tbb")),
                       runtime_dirs=[str(p) for p in tbb_runtime],
                       license_files={"LICENSE-oneTBB.txt": str(tbb / "LICENSE.txt"),
                                      "third-party-programs-oneTBB.txt": str(tbb / "third-party-programs.txt")}),
    }
    for lib in ("embree", "onetbb"):
        for label, path in deps[lib]["license_files"].items():
            if not os.path.isfile(path):
                raise Failure("missing licence file %s (%s)" % (path, label))
        if not os.path.isdir(deps[lib]["cmake_dir"]):
            raise Failure("missing CMake package directory %s" % deps[lib]["cmake_dir"])
    write_json(dest / "deps.json", deps)
    log("wrote %s" % (dest / "deps.json"))


if __name__ == "__main__":
    try:
        main()
    except Failure as e:
        sys.exit("fetch_deps: " + str(e))
