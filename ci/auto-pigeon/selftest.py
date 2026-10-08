#!/usr/bin/env python3
"""Cheap checks of the release scripts themselves; run by the plan job before anything is built.

    python3 ci/auto-pigeon/selftest.py
"""

import pathlib
import py_compile
import re
import struct
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bspread import Bsp  # noqa: E402
from common import PLATFORMS, REQUIRED_PLATFORMS, load_json  # noqa: E402


def check(condition, message):
    if not condition:
        sys.exit("selftest: " + message)


def tiny_bsp(path):
    """One plane x=0; hull 0 node and hull 1/2 clipnode: front (x>=0) EMPTY, back SOLID."""
    planes = struct.pack("<4fi", 1, 0, 0, 0, 0)
    nodes = struct.pack("<ihh6h2H", 0, -2, -1, 0, 0, 0, 0, 0, 0, 0, 0)   # children: leaf 1, leaf 0
    leafs = struct.pack("<ii6h2H4B", -2, -1, *([0] * 12)) + struct.pack("<ii6h2H4B", -1, -1, *([0] * 12))
    clip = struct.pack("<iHH", 0, 0xFFFF, 0xFFFE)                         # front -1 EMPTY, back -2 SOLID
    models = struct.pack("<9f4i3i", *([0.0] * 9), 0, 0, 0, 0, 2, 0, 0)
    lumps = {1: planes, 5: nodes, 9: clip, 10: leafs, 14: models}
    offset, table, body = 4 + 8 * 15, b"", b""
    for i in range(15):
        data = lumps.get(i, b"")
        table += struct.pack("<ii", offset + len(body), len(data))
        body += data
    path.write_bytes(struct.pack("<i", 29) + table + body)


def main():
    for script in sorted(HERE.glob("*.py")):
        py_compile.compile(str(script), doraise=True)
    lock = load_json(HERE / "deps.lock.json")
    for lib in ("embree", "onetbb"):
        for platform, entry in lock[lib]["binaries"].items():
            check(platform in PLATFORMS, "unknown platform %s in the lock" % platform)
            check(re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is not None, "bad digest for %s %s" % (lib, platform))
            check(entry["url"].startswith("https://github.com/") and "/releases/download/v%s/" % lock[lib]["version"] in entry["url"],
                  "%s %s is not a versioned release URL" % (lib, platform))
        check(re.fullmatch(r"[0-9a-f]{40}", lock[lib]["source_commit"]) is not None, "bad source commit for %s" % lib)
    for platform in PLATFORMS:
        check(platform in lock["embree"]["binaries"], "no Embree for %s" % platform)
        check(platform in lock["onetbb"]["binaries"] or platform in lock["onetbb"]["source_builds"], "no oneTBB for %s" % platform)
    check(set(REQUIRED_PLATFORMS) <= set(PLATFORMS), "required platforms are not all known")

    sealed = (HERE / "fixtures" / "sealed_room.map").read_text().splitlines()
    leak = (HERE / "fixtures" / "leak_room.map").read_text().splitlines()
    removed = [line for line in sealed[3:] if line not in leak[3:]]
    check(len(sealed) - len(leak) == 9 and removed[0] == "// north wall",
          "the leak fixture must be the sealed room minus exactly its north wall")

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "t.bsp"
        tiny_bsp(path)
        bsp = Bsp(path)
        for hull in (0, 1, 2):
            check(bsp.contents_name(hull, (5, 0, 0)) == "EMPTY" and bsp.contents_name(hull, (-5, 0, 0)) == "SOLID",
                  "the BSP reader misreads hull %d" % hull)

    workflow = (HERE.parent.parent / ".github" / "workflows" / "auto-pigeon-release.yml").read_text()
    for use in re.findall(r"uses:\s*(\S+)", workflow):
        check(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", use) is not None, "action %s is not pinned to a commit" % use)
    check(re.search(r"^\s*pull_request_target", workflow, re.M) is None, "pull_request_target must never be used")
    check(len(re.findall(r"^\s+contents: write\s*$", workflow, re.M)) == 1, "exactly one job may write repository contents")
    check(len(list((HERE.parent.parent / ".github" / "workflows").glob("*.y*ml"))) == 1, "there must be one workflow: one release path")
    print("selftest: ok")


if __name__ == "__main__":
    main()
