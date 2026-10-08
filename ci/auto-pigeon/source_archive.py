#!/usr/bin/env python3
"""Write the exact-commit SOURCE archive: the fork, every submodule, the pinned GoogleTest.

    python3 ci/auto-pigeon/source_archive.py --plan plan.json --out <dist dir>

GitHub's automatic "Source code" downloads omit submodules, so they cannot rebuild this release.
This archive can: see AUTO-PIGEON-SOURCE.md at its root.
"""

import argparse
import gzip
import io
import pathlib
import shutil
import sys
import tarfile
import tempfile

from common import HERE, REPO, Failure, load_json, log, run, sha256_file, write_json

GUIDE = """# Auto-Pigeon EricW tools {version} — complete source

This archive is the source of release `{tag}` of the Auto-Pigeon fork of ericw-tools:

- the fork at commit `{commit}` (<{fork}>), which is upstream `{describe}` of <{upstream}> plus the fork's changes;
- every Git submodule at the commit that tree records ({submodules});
- GoogleTest {gtest_version} at commit `{gtest_commit}` in `3rdparty/googletest-pinned/` (the test program only);
- the release scripts in `ci/auto-pigeon/`, `ci/auto-pigeon/deps.lock.json` (URL, SHA-256, version, licence and
  source commit of the Embree and oneTBB packages the release was linked against) and
  `ci/auto-pigeon/release-plan.json` (the frozen plan of this release).

Embree {embree} and oneTBB {tbb} are separate Apache-2.0 libraries that the release ships unmodified; their
sources are <{embree_repo}> at `{embree_commit}` and <{tbb_repo}> at `{tbb_commit}`.

## Rebuild

Needs CMake 3.14 or newer, a C++20 compiler, Python 3.8 or newer, and network access for the two
pinned library downloads (no Git history is needed).

```sh
python3 ci/auto-pigeon/fetch_deps.py --platform linux-amd64 --dest "$PWD/../deps"
python3 ci/auto-pigeon/build.py --platform linux-amd64 --deps "$PWD/../deps" --build-dir "$PWD/../build" \\
    --plan ci/auto-pigeon/release-plan.json --report "$PWD/../build-report.json"
python3 ci/auto-pigeon/package.py --platform linux-amd64 --deps "$PWD/../deps" \\
    --build-report "$PWD/../build-report.json" --plan ci/auto-pigeon/release-plan.json --out "$PWD/../dist"
```

Use `windows-amd64`, `macos-amd64` or `macos-arm64` on those hosts. The scripts refuse to build a
target on a different operating system or CPU. Compiled program bytes are not claimed to be
reproducible; the archive layout, member order, times and modes are.

## Licence

ericw-tools is free software under the GNU General Public License version 2 or, at your option,
any later version (`COPYING`, `gpl_v3.txt`). Third-party licences are in each `3rdparty/` directory.
"""


def git_archive_into(repo, commit, target):
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
        name = tmp.name
    run(["git", "archive", "--format=tar", "-o", name, commit], cwd=repo)
    with tarfile.open(name) as t:
        try:
            t.extractall(target, filter="tar")
        except TypeError:
            t.extractall(target)
    pathlib.Path(name).unlink()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mark-rebuilt", metavar="BUILD_REPORT",
                    help="record, in the archive's .source.json, that the unpacked archive built and passed its tests")
    args = ap.parse_args()
    plan = load_json(args.plan)
    lock = load_json(HERE / "deps.lock.json")
    out = pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    stem = plan["source_asset"][:-len(".tar.gz")]
    if args.mark_rebuilt:
        report = load_json(args.mark_rebuilt)
        full = report["tests"].get("full")
        if not report["from_source_archive"] or report["commit"] != plan["commit"] or not full or full["failures"] or full["exit"]:
            raise Failure("that build report is not a passing build of the unpacked source archive of this plan")
        record = load_json(out / (stem + ".source.json"))
        record["rebuild_verified"] = {"platform": report["platform"], "tests_run": full["tests_run"], "failures": full["failures"],
                                      "focused": report["tests"]["focused"]["names_run"]}
        write_json(out / (stem + ".source.json"), record)
        log("recorded: the source archive rebuilt and passed %d tests" % full["tests_run"])
        return
    work = pathlib.Path(tempfile.mkdtemp(prefix="ap-source-"))
    root = work / stem
    git_archive_into(REPO, plan["commit"], root)
    for path, commit in sorted(plan["submodules"].items()):
        sub = REPO / path
        head = run(["git", "rev-parse", "HEAD"], cwd=sub, capture=True)[1].strip()
        if head != commit:
            raise Failure("submodule %s is at %s, the release commit records %s" % (path, head, commit))
        nested = run(["git", "ls-tree", "-r", commit], cwd=sub, capture=True)[1]
        if any(line.split()[1] == "commit" for line in nested.splitlines()):
            raise Failure("submodule %s has submodules of its own; this script does not descend" % path)
        git_archive_into(sub, commit, root / path)
    gtest = work / "googletest.git"
    gtest.mkdir()
    run(["git", "init", "-q"], cwd=gtest)
    run(["git", "fetch", "-q", "--depth", "1", lock["googletest"]["repository"], lock["googletest"]["commit"]], cwd=gtest)
    fetched = run(["git", "rev-parse", "FETCH_HEAD"], cwd=gtest, capture=True)[1].strip()
    if fetched != lock["googletest"]["commit"]:
        raise Failure("GoogleTest fetch returned %s" % fetched)
    git_archive_into(gtest, fetched, root / "3rdparty" / "googletest-pinned")
    shutil.copyfile(args.plan, root / "ci" / "auto-pigeon" / "release-plan.json")
    (root / "AUTO-PIGEON-SOURCE.md").write_text(GUIDE.format(
        version=plan["version"], tag=plan["tag"], commit=plan["commit"], fork=plan["fork_repository"],
        describe=plan["upstream_describe"], upstream=plan["upstream_repository"],
        submodules=", ".join("`%s` %s" % (p, c[:12]) for p, c in sorted(plan["submodules"].items())),
        gtest_version=lock["googletest"]["version"], gtest_commit=lock["googletest"]["commit"],
        embree=lock["embree"]["version"], tbb=lock["onetbb"]["version"],
        embree_repo=lock["embree"]["source_repository"], embree_commit=lock["embree"]["source_commit"],
        tbb_repo=lock["onetbb"]["source_repository"], tbb_commit=lock["onetbb"]["source_commit"]), encoding="utf-8", newline="\n")

    target = out / plan["source_asset"]
    entries = sorted(p for p in root.rglob("*"))
    with open(target, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for p in [root] + entries:
            info = tar.gettarinfo(str(p), arcname=stem + ("/" + p.relative_to(root).as_posix() if p != root else ""))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = plan["commit_time"]
            if info.isfile():
                info.mode = 0o755 if info.mode & 0o100 else 0o644
                with open(p, "rb") as f:
                    tar.addfile(info, f)
            else:
                if info.isdir():
                    info.mode = 0o755
                tar.addfile(info)
    files = sum(1 for p in entries if p.is_file())
    write_json(out / (stem + ".source.json"), {
        "schema": "auto-pigeon-ericw-source/1", "asset": target.name, "size": target.stat().st_size,
        "sha256": sha256_file(target), "commit": plan["commit"], "version": plan["version"], "files": files,
        "submodules": plan["submodules"], "googletest_commit": lock["googletest"]["commit"]})
    shutil.rmtree(work)
    log("wrote %s  %d files  sha256 %s" % (target.name, files, sha256_file(target)))


if __name__ == "__main__":
    try:
        main()
    except Failure as e:
        sys.exit("source_archive: " + str(e))
