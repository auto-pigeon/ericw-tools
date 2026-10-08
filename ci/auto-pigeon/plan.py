#!/usr/bin/env python3
"""Freeze one release plan: the exact commit, its version, tag, asset names and version stamp.

Planned once per workflow run; every build, test, manifest and the source archive read this
file and nothing recomputes it. Usage:

    python3 ci/auto-pigeon/plan.py --sha <full sha> --out plan.json [--publish true|false]
"""

import argparse
import os
import re
import sys

from common import ASSET_PREFIX, PLATFORMS, REPO, REQUIRED_PLATFORMS, TAG_PREFIX, Failure, load_json, log, run, write_json, HERE


def git(*args):
    return run(["git", *args], cwd=REPO, capture=True)[1].strip()


def make_plan(sha, publish):
    full = git("rev-parse", "--verify", sha + "^{commit}")
    if not re.fullmatch(r"[0-9a-f]{40}", full):
        raise Failure("not a full commit id: %r" % full)
    if git("rev-parse", "--is-shallow-repository") != "false":
        raise Failure("shallow checkout: the version is the commit count, so full history is required")
    count = int(git("rev-list", "--count", full))
    version = "1.%d" % count
    # The upstream label this commit descends from. Our own release tags are lightweight and are
    # excluded by name as well, so creating one can never change a later run's answer.
    describe = git("describe", "--tags", "--abbrev=8", "--match", "[0-9]*", "--exclude", TAG_PREFIX + "*", full)
    if not re.fullmatch(r"[0-9A-Za-z.\-]+", describe):
        raise Failure("unexpected upstream describe output: %r" % describe)
    upstream_tag = git("describe", "--tags", "--abbrev=0", "--match", "[0-9]*", "--exclude", TAG_PREFIX + "*", full)
    stamp = "%s+auto-pigeon.%s" % (describe, version)
    lock = load_json(HERE / "deps.lock.json")
    cmake_pin = re.search(r"GIT_TAG\s+([0-9a-f]{40})", (REPO / "3rdparty" / "CMakeLists.txt").read_text())
    if not cmake_pin or cmake_pin.group(1) != lock["googletest"]["commit"]:
        raise Failure("deps.lock.json and 3rdparty/CMakeLists.txt disagree about the GoogleTest commit")
    submodules = {}
    for line in git("ls-tree", "-r", full).splitlines():
        meta, path = line.split("\t", 1)
        mode, kind, obj = meta.split()
        if kind == "commit":
            submodules[path] = obj
    return {
        "schema": "auto-pigeon-ericw-plan/1",
        "commit": full,
        "commit_time": int(git("show", "-s", "--format=%ct", full)),
        "commit_count": count,
        "version": version,
        "tag": TAG_PREFIX + version,
        "asset_prefix": "%s-%s" % (ASSET_PREFIX, version),
        "stamp": stamp,
        "upstream_describe": describe,
        "upstream_base_tag": upstream_tag,
        "upstream_repository": "https://github.com/ericwa/ericw-tools",
        "fork_repository": "https://github.com/auto-pigeon/ericw-tools",
        "platforms": list(PLATFORMS),
        "required_platforms": list(REQUIRED_PLATFORMS),
        "assets": {p: "%s-%s-%s.zip" % (ASSET_PREFIX, version, p) for p in PLATFORMS},
        "source_asset": "%s-%s-source.tar.gz" % (ASSET_PREFIX, version),
        "submodules": submodules,
        "publish": bool(publish),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--publish", default="false", choices=("true", "false"))
    args = ap.parse_args()
    plan = make_plan(args.sha, args.publish == "true")
    write_json(args.out, plan)
    for key in ("commit", "version", "tag", "stamp", "publish"):
        log("%s=%s" % (key, str(plan[key]).lower() if isinstance(plan[key], bool) else plan[key]))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            for key in ("commit", "version", "tag", "stamp"):
                f.write("%s=%s\n" % (key, plan[key]))
            f.write("publish=%s\n" % ("true" if plan["publish"] else "false"))


if __name__ == "__main__":
    try:
        main()
    except Failure as e:
        sys.exit("plan: " + str(e))
