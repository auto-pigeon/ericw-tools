#!/usr/bin/env python3
"""Accept one candidate ZIP on its own native host, from the EXTRACTED archive only.

    python3 ci/auto-pigeon/accept.py --platform linux-amd64 --archive <zip> --package-json <json> \
        --plan plan.json --work <empty dir> --report acceptance.json

Nothing here reads a build tree. The archive is checked against its digest and manifest,
unpacked into a directory with spaces, moved, and its programs are run by absolute path from an
unrelated working directory with a search path reduced to the operating system's own. The
report's verdict is `pass` only when every step passed; the exit status says the same.
"""

import argparse
import os
import pathlib
import shutil
import stat
import struct
import sys
import zipfile

import closure
from bspread import Bsp
from common import HERE, PLATFORMS, PROGRAMS, REPO, Failure, exe, load_json, log, require_native, run, sha256_file, write_json

REQUIRED_MEMBERS = ("README.md", "build-info.json", "MANIFEST.json", "licenses/COPYING", "licenses/gpl_v3.txt",
                    "licenses/LICENSE-embree.txt", "licenses/LICENSE-oneTBB.txt")
# How each program answers a request for its usage, measured on this source line: the banner and
# the usage text on stdout, exit status 1 (bspinfo has no --help and prints usage for no argument).
ENTRY_POINTS = {name: (["--help"], 1) for name in PROGRAMS}
ENTRY_POINTS["bspinfo"] = ([], 1)
THIN_MAP = "q1_cliphull2_thin_fragment_split.map"
# The assertions of tests/test_qbsp.cc, testmapsQ1.cliphull2ThinFragmentSplit, on the released qbsp's output.
THIN_PROBES = (
    (2, (94.281089, 709.496103, 533.12), "SOLID", "hall wall where the hull 2 leak crossed it (0.1 early-out)"),
    (2, (142.074073, 738.287188, 533.105), "SOLID", "hall wall where the hull 2 leak crossed it (0.1 mid-winding clip)"),
    (0, (94.281089, 709.496103, 533.12), "SOLID", "the same wall in the point hull"),
    (0, (-105.055035, -702.129458, -4.023305), "EMPTY", "the light, hull 0"),
    (1, (-105.055035, -702.129458, -4.023305), "EMPTY", "the light, hull 1"),
    (2, (-105.055035, -702.129458, -4.023305), "EMPTY", "the light, hull 2"),
)
ROOM_PROBES = (
    ((0, 1, 2), (-160, -96, 32), "EMPTY", "player start"),
    # 64 units under the ceiling: open to the point and player hulls, and exactly on the expanded
    # ceiling of hull 2, so it is not asked there
    ((0, 1), (128, 96, 128), "EMPTY", "light"),
    ((0, 1, 2), (0, 0, 96), "SOLID", "inside the pillar"),
    ((0, 1, 2), (0, 0, -1000), "SOLID", "outside, below the floor (filled)"),
    ((0, 1, 2), (0, 600, 96), "SOLID", "outside, beyond the north wall (filled)"),
)


class Acceptance:
    def __init__(self, args):
        self.platform = args.platform
        self.work = pathlib.Path(args.work).resolve()
        self.report_path = args.report
        self.plan = load_json(args.plan)
        self.package = load_json(args.package_json)
        self.archive = pathlib.Path(args.archive).resolve()
        self.steps = []
        self.commands = []
        self.logs = self.work / "logs"
        self.report = {
            "schema": "auto-pigeon-ericw-acceptance/1", "platform": self.platform, "verdict": "fail",
            "commit": self.plan["commit"], "version": self.plan["version"], "stamp": self.plan["stamp"],
            "asset": self.archive.name, "steps": self.steps, "commands": self.commands,
        }

    # -- plumbing

    def step(self, name, function):
        log("\n=== %s" % name)
        entry = {"name": name, "status": "fail"}
        self.steps.append(entry)
        detail = function()
        entry["status"] = "pass"
        if detail is not None:
            entry["detail"] = detail
        log("--- pass: %s" % name)

    def clean_env(self):
        """The operating system's own search path and nothing of a build or dependency tree."""
        keep = {}
        if self.platform.startswith("windows"):
            root = os.environ.get("SystemRoot", r"C:\Windows")
            keep = {"SystemRoot": root, "windir": root, "PATH": root + r"\System32;" + root,
                    "SystemDrive": os.environ.get("SystemDrive", "C:"), "TEMP": str(self.work / "tmp"), "TMP": str(self.work / "tmp")}
            (self.work / "tmp").mkdir(exist_ok=True)
        else:
            keep = {"PATH": "/usr/bin:/bin", "HOME": str(self.work / "home"), "LANG": "C", "LC_ALL": "C"}
            (self.work / "home").mkdir(exist_ok=True)
            if self.platform.startswith("macos"):
                keep["DYLD_BIND_AT_LOAD"] = "1"  # resolve every symbol now, not at first use
        return keep

    def tool(self, name, args, cwd, expect=0, label=None):
        """Run an archived program by absolute path; wait; record; compare its exit status."""
        program = self.bin / exe(self.platform, name)
        label = label or "%02d-%s" % (len(self.commands) + 1, name)
        code, out, seconds = run([program, *args], cwd=cwd, env=self.clean_env(), check=False, capture=True, timeout=1800)
        self.logs.mkdir(parents=True, exist_ok=True)
        (self.logs / (label + ".log")).write_text(out, encoding="utf-8", errors="replace")
        record = {"label": label, "program": name, "args": [str(a) for a in args], "cwd": str(cwd), "exit": code,
                  "expected_exit": expect, "seconds": seconds, "output_lines": out.count("\n")}
        self.commands.append(record)
        log("    exit %d in %.2fs" % (code, seconds))
        if expect is not None and code != expect:
            record["output_tail"] = out.splitlines()[-40:]
            sys.stdout.write("\n".join(record["output_tail"]) + "\n")
            raise Failure("%s %s exited %d, expected %d" % (name, " ".join(str(a) for a in args), code, expect))
        return code, out

    # -- steps

    def verify_digest(self):
        got = sha256_file(self.archive)
        if got != self.package["sha256"]:
            raise Failure("archive SHA-256 %s is not the candidate's %s" % (got, self.package["sha256"]))
        for key in ("commit", "version", "stamp"):
            if self.package[key] != self.plan[key]:
                raise Failure("candidate %s=%r, plan says %r" % (key, self.package[key], self.plan[key]))
        if self.package["platform"] != self.platform or self.archive.name != self.plan["assets"][self.platform]:
            raise Failure("candidate is not this run's %s archive" % self.platform)
        if not self.package["releasable"]:
            raise Failure("the candidate is marked not releasable (modified tree or skipped tests)")
        return {"sha256": got, "size": self.archive.stat().st_size}

    def unpack_and_move(self):
        first = self.work / "unpack here" / "with spaces"
        first.mkdir(parents=True)
        stem = self.archive.name[:-len(".zip")]
        with zipfile.ZipFile(self.archive) as z:
            names = z.namelist()
            if any(n.startswith(("/", "\\")) or ".." in n.split("/") for n in names):
                raise Failure("archive has an unsafe member name")
            if {n.split("/")[0] for n in names} != {stem}:
                raise Failure("archive must hold exactly one top-level directory %s" % stem)
            for info in z.infolist():
                target = first / info.filename
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                mode = (info.external_attr >> 16) & 0o777
                if not self.platform.startswith("windows"):
                    os.chmod(target, mode or 0o644)
        final_parent = self.work / "moved after unpacking" / "Auto Pigeon tools"
        final_parent.mkdir(parents=True)
        shutil.move(str(first / stem), str(final_parent / stem))
        self.root = final_parent / stem
        self.bin = self.root / "bin"
        return {"unpacked_to": str(first / stem), "moved_to": str(self.root), "members": len(names)}

    def verify_manifest(self):
        manifest = load_json(self.root / "MANIFEST.json")
        build_info = load_json(self.root / "build-info.json")
        listed = {f["path"]: f for f in manifest["files"]}
        present = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()}
        if present - set(listed) != {"MANIFEST.json"} or set(listed) - present:
            raise Failure("archive contents and MANIFEST.json disagree: extra %s, missing %s" % (
                sorted(present - set(listed) - {"MANIFEST.json"}), sorted(set(listed) - present)))
        for rel, entry in listed.items():
            path = self.root / rel
            if path.is_symlink():
                raise Failure("%s is a symbolic link" % rel)
            if path.stat().st_size != entry["size"] or sha256_file(path) != entry["sha256"]:
                raise Failure("%s does not match its manifest entry" % rel)
            if not self.platform.startswith("windows"):
                mode = "%04o" % stat.S_IMODE(path.stat().st_mode)
                if mode != entry["mode"]:
                    raise Failure("%s has mode %s, the manifest says %s" % (rel, mode, entry["mode"]))
        required = list(REQUIRED_MEMBERS) + ["bin/" + exe(self.platform, n) for n in PROGRAMS]
        missing = [m for m in required if m not in present]
        if missing:
            raise Failure("required members are missing: %s" % missing)
        forbidden = [p for p in present if p.lower().endswith((".o", ".obj", ".a", ".lib", ".pdb", ".map", ".wad", ".bsp", ".pak", ".pk3", ".cmake"))
                     or "CMakeFiles" in p]
        if forbidden:
            raise Failure("the archive carries build or game material: %s" % forbidden)
        for key, want in (("commit", self.plan["commit"]), ("version", self.plan["version"]), ("stamp", self.plan["stamp"])):
            if manifest[key] != want:
                raise Failure("MANIFEST %s=%r, plan says %r" % (key, manifest[key], want))
        if (build_info["fork"]["commit"], build_info["version_banner"], build_info["target"], build_info["signed"]) != (
                self.plan["commit"], self.plan["stamp"], self.platform, False):
            raise Failure("build-info.json does not describe this plan and target")
        self.build_info = build_info
        return {"files": len(listed), "manifest_sha256": sha256_file(self.root / "MANIFEST.json")}

    def verify_architecture_and_closure(self):
        programs = [exe(self.platform, n) for n in PROGRAMS]
        if self.platform.startswith("linux"):
            for name in programs:
                with open(self.bin / name, "rb") as f:
                    head = f.read(20)
                if head[:5] != b"\x7fELF\x02" or struct.unpack_from("<H", head, 18)[0] != 62:
                    raise Failure("%s is not a 64-bit x86-64 ELF program" % name)
        return closure.check(self.platform, self.bin, programs)

    def entry_points(self):
        cwd = self.work / "unrelated cwd"
        cwd.mkdir(exist_ok=True)
        seen = {}
        for name in PROGRAMS:
            args, expect = ENTRY_POINTS[name]
            _, out = self.tool(name, args, cwd, expect=expect, label="entry-%s" % name)
            banner = "---- %s / ericw-tools %s ----" % (name, self.plan["stamp"])
            if banner not in out:
                found = [l for l in out.splitlines() if "ericw-tools" in l][:1]
                raise Failure("%s does not print %r (it printed %r)" % (name, banner, found))
            if "usage:" not in out.lower():
                raise Failure("%s printed no usage text" % name)
            seen[name] = banner
        self.help = {name: self.commands_output("entry-%s" % name) for name in ("vis", "light", "qbsp")}
        for name in ("vis", "light"):
            if "-threads n" not in self.help[name]:
                raise Failure("%s does not document -threads; refusing to pass it" % name)
        if "-fast" not in self.help["vis"] or "-leaktest" not in self.help["qbsp"] or "-forcegoodtree" not in self.help["qbsp"] or "-tjunc" not in self.help["qbsp"]:
            raise Failure("an option this acceptance relies on is not documented by the released program")
        return {"banners": seen, "one_source_identity": len(set(b.split(" / ", 1)[1] for b in seen.values())) == 1}

    def commands_output(self, label):
        return (self.logs / (label + ".log")).read_text(encoding="utf-8", errors="replace")

    def judge_sealed(self, directory, base):
        """The one rule for "this compile is a sealed build". Returns (ok, reasons)."""
        reasons = []
        bsp, prt, pts = directory / (base + ".bsp"), directory / (base + ".prt"), directory / (base + ".pts")
        if pts.exists():
            reasons.append("a leak pointfile %s was written" % pts.name)
        if not bsp.is_file():
            reasons.append("no BSP was written")
        if not prt.is_file():
            reasons.append("no portal file was written")
        elif prt.read_text(errors="replace").split()[:1] != ["PRT1"] or len(prt.read_text(errors="replace").split()) < 3:
            reasons.append("the portal file is not a PRT1 file with leaf and portal counts")
        return (not reasons), reasons

    def full_compile(self, directory, map_name, cwd, map_argument, label):
        map_argument = str(map_argument)
        """leaktest, then qbsp -> full VIS -> LIGHT -> inspection, on fresh outputs only."""
        base = map_name[:-len(".map")]
        before = {p.name for p in directory.iterdir()}
        if any(n.startswith(base + ".") and n != map_name for n in before):
            raise Failure("stale outputs for %s exist before the compile" % map_name)
        self.tool("qbsp", ["-leaktest", map_argument], cwd, label=label + "-qbsp-leaktest")
        ok, reasons = self.judge_sealed(directory, base)
        if not ok:
            raise Failure("leak test of %s: %s" % (map_name, "; ".join(reasons)))
        for p in directory.iterdir():
            if p.name not in before:
                p.unlink()
        _, qbsp_log = self.tool("qbsp", [map_argument], cwd, label=label + "-qbsp")
        ok, reasons = self.judge_sealed(directory, base)
        if not ok:
            raise Failure("%s is not a sealed build: %s" % (map_name, "; ".join(reasons)))
        if "Reached occupant" in qbsp_log or "Leak file written" in qbsp_log:
            raise Failure("qbsp reported a leak in %s" % map_name)
        bsp_path = directory / (base + ".bsp")
        bsp_argument = map_argument[:-len(".map")] + ".bsp"
        stage = {"qbsp_bsp_sha256": sha256_file(bsp_path)}
        bsp = Bsp(bsp_path)
        if bsp.size("textures") < 4000:
            raise Failure("the texture was not resolved from the WAD (texture lump is %d bytes)" % bsp.size("textures"))
        if bsp.size("visibility") != 0 or bsp.size("lighting") != 0:
            raise Failure("a fresh qbsp output already carries visibility or lighting data")
        _, vis_log = self.tool("vis", ["-threads", "4", bsp_argument], cwd, label=label + "-vis")
        if '"threads" was set to "4"' not in vis_log:
            raise Failure("vis did not acknowledge -threads 4")
        if '"fast" was set' in vis_log:
            raise Failure("vis ran in fast mode")
        stage["vis_bsp_sha256"] = sha256_file(bsp_path)
        bsp = Bsp(bsp_path)
        if bsp.size("visibility") <= 0 or stage["vis_bsp_sha256"] == stage["qbsp_bsp_sha256"]:
            raise Failure("full VIS wrote no visibility data")
        _, light_log = self.tool("light", ["-threads", "4", bsp_argument], cwd, label=label + "-light")
        if '"threads" was set to "4"' not in light_log:
            raise Failure("light did not acknowledge -threads 4")
        if "Embree_TraceInit" not in light_log:
            raise Failure("light did not initialise Embree")
        stage["light_bsp_sha256"] = sha256_file(bsp_path)
        bsp = Bsp(bsp_path)
        if bsp.size("lighting") <= 0 or stage["light_bsp_sha256"] == stage["vis_bsp_sha256"]:
            raise Failure("LIGHT wrote no lighting data")
        self.tool("bsputil", ["-check", bsp_argument], cwd, label=label + "-bsputil-check")
        self.tool("bspinfo", [bsp_argument], cwd, label=label + "-bspinfo")
        probes = []
        for hulls, point, want, what in ROOM_PROBES:
            for hull in hulls:
                got = bsp.contents_name(hull, point)
                probes.append({"hull": hull, "point": point, "what": what, "contents": got})
                if got != want:
                    raise Failure("%s: hull %d at %s (%s) is %s, expected %s" % (map_name, hull, point, what, got, want))
        stage.update({"format": bsp.format, "visibility_bytes": bsp.size("visibility"), "lighting_bytes": bsp.size("lighting"),
                      "texture_bytes": bsp.size("textures"), "probes_checked": len(probes), "hulls_sealed": [0, 1, 2]})
        return stage

    def case_dir(self, name, files):
        directory = self.work / name
        directory.mkdir(parents=True)
        for source, target in files:
            (directory / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, directory / target)
        return directory

    def sealed_room(self):
        d = self.case_dir("case-plain", [(HERE / "fixtures" / "sealed_room.map", "sealed_room.map"), (self.wad, "free_wad.wad")])
        self.plain = self.full_compile(d, "sealed_room.map", self.work / "unrelated cwd", d / "sealed_room.map", "plain")
        return self.plain

    def sealed_room_with_spaces(self):
        d = self.case_dir("case with spaces/a b c", [(HERE / "fixtures" / "sealed_room.map", "sealed room.map"), (self.wad, "free_wad.wad")])
        result = self.full_compile(d, "sealed room.map", d, "sealed room.map", "spaces")
        result["qbsp_output_identical_to_plain_run"] = result["qbsp_bsp_sha256"] == self.plain["qbsp_bsp_sha256"]
        if not result["qbsp_output_identical_to_plain_run"]:
            raise Failure("the same room compiled from a path with spaces gave a different BSP")
        return result

    def negative_control(self):
        d = self.case_dir("case-leak", [(HERE / "fixtures" / "leak_room.map", "leak_room.map"), (self.wad, "free_wad.wad")])
        code, out = self.tool("qbsp", [d / "leak_room.map"], self.work / "unrelated cwd", expect=None, label="leak-qbsp")
        pts = d / "leak_room.pts"
        if not pts.is_file() or pts.stat().st_size == 0:
            raise Failure("the room with a missing wall produced no leak pointfile; the leak detector saw nothing")
        if "Reached occupant" not in out:
            raise Failure("qbsp wrote a pointfile but did not report the occupant it reached")
        ok, reasons = self.judge_sealed(d, "leak_room")
        if ok:
            raise Failure("the sealed-build rule ACCEPTED the leaking room; the harness cannot tell a leak from a seal")
        strict, _ = self.tool("qbsp", ["-leaktest", d / "leak_room.map"], self.work / "unrelated cwd", expect=None, label="leak-qbsp-leaktest")
        if strict == 0:
            raise Failure("qbsp -leaktest exited 0 on a leaking room")
        return {"normal_qbsp_exit": code, "note": "this qbsp exits %d on a leak without -leaktest; the pointfile and the missing portal file are the indication" % code,
                "leaktest_exit": strict, "pointfile_bytes": pts.stat().st_size, "refused_as_sealed_because": reasons}

    def thin_fragment(self):
        results = {}
        for mode, extra in (("ordinary", []), ("forcegoodtree", ["-forcegoodtree"])):
            d = self.case_dir("case-thin-" + mode, [(REPO / "testmaps" / THIN_MAP, THIN_MAP), (self.wad, "deprecated/free_wad.wad")])
            # -tjunc none is this TEST's isolation of an unrelated T-junction spike (see tests/test_qbsp.cc); it is not a pipeline default
            self.tool("qbsp", ["-leaktest", "-tjunc", "none", *extra, d / THIN_MAP], self.work / "unrelated cwd", label="thin-" + mode)
            base = THIN_MAP[:-len(".map")]
            ok, reasons = self.judge_sealed(d, base)
            if not ok:
                raise Failure("thin-fragment reproducer (%s): %s" % (mode, "; ".join(reasons)))
            bsp = Bsp(d / (base + ".bsp"))
            probes = []
            for hull, point, want, what in THIN_PROBES:
                got = bsp.contents_name(hull, point)
                probes.append({"hull": hull, "point": point, "what": what, "contents": got})
                if got != want:
                    raise Failure("thin-fragment reproducer (%s): hull %d at %s (%s) is %s, expected %s" % (mode, hull, point, what, got, want))
            results[mode] = {"bsp_sha256": sha256_file(d / (base + ".bsp")), "probes": probes}
        return results

    def run_all(self):
        require_native(self.platform)
        if self.work.exists() and any(self.work.iterdir()):
            raise Failure("the work directory %s is not empty" % self.work)
        self.work.mkdir(parents=True, exist_ok=True)
        self.wad = REPO / "testmaps" / "deprecated" / "free_wad.wad"
        self.step("1a. the archive is this run's candidate (digest, plan, platform)", self.verify_digest)
        self.step("1b. unpack into a path with spaces, then move it", self.unpack_and_move)
        self.step("1c. manifest, required members, licences, modes, provenance", self.verify_manifest)
        self.step("1d. architecture and runtime closure of all six programs", self.verify_architecture_and_closure)
        self.step("2. help entry points and one version identity", self.entry_points)
        self.step("3. sealed room: leak test, qbsp, full VIS, LIGHT, inspection", self.sealed_room)
        self.step("4. the same room from a path and working directory with spaces", self.sealed_room_with_spaces)
        self.step("5. negative control: the room with a missing wall leaks and is refused", self.negative_control)
        self.step("6. thin-fragment reproducer, ordinary and -forcegoodtree, hulls 0-2", self.thin_fragment)
        self.report["verdict"] = "pass"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", required=True, choices=PLATFORMS)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--package-json", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    acceptance = Acceptance(args)
    try:
        acceptance.run_all()
    except (Failure, ValueError, OSError, KeyError) as e:
        acceptance.report["failure"] = "%s: %s" % (type(e).__name__, e)
        log("\nACCEPTANCE FAILED: %s" % e)
    finally:
        acceptance.report["archive_sha256"] = acceptance.package.get("sha256")
        write_json(args.report, acceptance.report)
    passed = acceptance.report["verdict"] == "pass"
    log("\nverdict: %s (%d/%d steps passed)" % (acceptance.report["verdict"], sum(s["status"] == "pass" for s in acceptance.steps), 9))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
