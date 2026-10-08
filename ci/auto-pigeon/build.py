#!/usr/bin/env python3
"""Configure, build and TEST one native target, and write a machine-readable build report.

    python3 ci/auto-pigeon/build.py --platform linux-amd64 --deps <deps dir> --build-dir <dir> \
        --plan plan.json --report build-report.json [--asan] [--jobs 4]

The test step runs the real GoogleTest executable (CMake registers nothing with CTest, so a
green `ctest` would prove nothing), fails on zero executed tests, and then runs the thin-brush
regression by name and requires that exactly that test ran and passed.
"""

import argparse
import os
import pathlib
import platform as pyplatform
import re
import sys

from common import PLATFORMS, PROGRAMS, REPO, Failure, exe, load_json, log, require_native, run, sha256_file, write_json

FOCUSED_TEST = "testmapsQ1.cliphull2ThinFragmentSplit"


def cmake_cache(build_dir):
    cache = {}
    for line in (build_dir / "CMakeCache.txt").read_text(errors="replace").splitlines():
        m = re.match(r"([A-Za-z0-9_]+):[A-Z]+=(.*)", line)
        if m:
            cache[m.group(1)] = m.group(2)
    return cache


def compiler_identity(build_dir):
    ident = {}
    for f in build_dir.glob("CMakeFiles/*/CMakeCXXCompiler.cmake"):
        text = f.read_text(errors="replace")
        for key in ("CMAKE_CXX_COMPILER_ID", "CMAKE_CXX_COMPILER_VERSION", "CMAKE_CXX_COMPILER_ARCHITECTURE_ID"):
            m = re.search(r'set\(%s "([^"]*)"\)' % key, text)
            if m:
                ident[key] = m.group(1)
    return ident


def resolve_binary(build_dir, subdir, name, platform):
    """Single-config generators put it in <dir>/, Visual Studio in <dir>/Release/."""
    for candidate in (build_dir / subdir / exe(platform, name), build_dir / subdir / "Release" / exe(platform, name)):
        if candidate.is_file():
            return candidate
    raise Failure("built program not found: %s/%s" % (subdir, exe(platform, name)))


def run_gtest(tests, build_dir, label, extra, env):
    report = build_dir / ("gtest-%s.json" % label)
    if report.exists():
        report.unlink()
    code, _, seconds = run([tests, "--gtest_output=json:%s" % report, *extra], cwd=build_dir, env=env, check=False)
    if not report.is_file():
        raise Failure("%s: the test program wrote no report (exit %d)" % (label, code))
    data = load_json(report)
    ran = sum(1 for s in data.get("testsuites", []) for t in s.get("testsuite", []) if t.get("status") == "RUN")
    result = {
        "exit": code,
        "seconds": seconds,
        "tests_reported": data.get("tests", 0),
        "tests_run": ran,
        "failures": data.get("failures", 0),
        "errors": data.get("errors", 0),
        "disabled": data.get("disabled", 0),
        "skipped": sum(1 for s in data.get("testsuites", []) for t in s.get("testsuite", []) if t.get("result") == "SKIPPED"),
        "names_run": sorted("%s.%s" % (s["name"], t["name"]) for s in data.get("testsuites", [])
                             for t in s.get("testsuite", []) if t.get("status") == "RUN") if ran <= 5 else None,
    }
    log("%s: exit %d, %d run, %d failures, %d errors, %d disabled, %d skipped, %.1fs" % (
        label, code, ran, result["failures"], result["errors"], result["disabled"], result["skipped"], seconds))
    if ran == 0:
        raise Failure("%s: zero tests executed" % label)
    if code != 0 or result["failures"] or result["errors"]:
        raise Failure("%s: tests failed (exit %d, %d failures, %d errors)" % (label, code, result["failures"], result["errors"]))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", required=True, choices=PLATFORMS)
    ap.add_argument("--deps", required=True)
    ap.add_argument("--build-dir", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--asan", action="store_true", help="RelWithDebInfo + AddressSanitizer; test coverage only, never packaged")
    ap.add_argument("--jobs", type=int, default=4, help="CMake build parallelism (not a compiler-tool flag)")
    ap.add_argument("--skip-full-suite", action="store_true", help="local iteration only; the report says so and package.py refuses it")
    args = ap.parse_args()
    platform = args.platform
    require_native(platform)
    plan = load_json(args.plan)
    deps = load_json(pathlib.Path(args.deps) / "deps.json")
    if deps["platform"] != platform:
        raise Failure("dependencies were fetched for %s" % deps["platform"])
    build_dir = pathlib.Path(args.build_dir).resolve()
    from_source_archive = (REPO / "AUTO-PIGEON-SOURCE.md").is_file() and not (REPO / ".git").exists()
    if from_source_archive:
        dirty = ""  # an unpacked source archive has no history; its release-plan.json names the commit
    else:
        head = run(["git", "rev-parse", "HEAD"], cwd=REPO, capture=True)[1].strip()
        if head != plan["commit"]:
            raise Failure("checkout is at %s, the plan froze %s" % (head, plan["commit"]))
        dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO, capture=True)[1].strip()

    build_type = "RelWithDebInfo" if args.asan else "Release"
    options = [
        "-DCMAKE_BUILD_TYPE=%s" % build_type,
        "-DDISABLE_TESTS=OFF",
        "-DDISABLE_DOCS=ON",
        "-DENABLE_LIGHTPREVIEW=OFF",
        # maputil links Lua when CMake finds one; a system liblua would be a hidden dependency
        "-DCMAKE_DISABLE_FIND_PACKAGE_Lua=ON",
        "-Dembree_DIR=%s" % deps["embree"]["cmake_dir"],
        "-DTBB_DIR=%s" % deps["onetbb"]["cmake_dir"],
        "-DERICWTOOLS_RELEASE_STAMP=%s" % plan["stamp"],
    ]
    if args.asan:
        options.append("-DERICWTOOLS_ASAN=YES")
    if not platform.startswith("windows"):
        # oneTBB's package config calls find_library without NO_DEFAULT_PATH, so a libtbb installed
        # on the build host would win over the pinned one. CMAKE_LIBRARY_PATH is searched first.
        options.append("-DCMAKE_LIBRARY_PATH=%s" % ";".join(deps["onetbb"]["runtime_dirs"]))
    pinned_googletest = REPO / "3rdparty" / "googletest-pinned"
    if pinned_googletest.is_dir():
        # the source archive carries the pinned GoogleTest, so the build needs no network for it
        options.append("-DFETCHCONTENT_SOURCE_DIR_GOOGLETEST=%s" % pinned_googletest)
    generator = []
    if platform == "windows-amd64":
        generator = ["-G", "Visual Studio 17 2022", "-A", "x64"]
    elif platform == "macos-amd64":
        options.append("-DCMAKE_OSX_ARCHITECTURES=x86_64")
    elif platform == "macos-arm64":
        options.append("-DCMAKE_OSX_ARCHITECTURES=arm64")

    timings = {}
    _, _, timings["configure"] = run(["cmake", "-S", REPO, "-B", build_dir, *generator, *options])
    _, _, timings["build"] = run(["cmake", "--build", build_dir, "--config", build_type, "--parallel", str(args.jobs)])

    programs = {}
    for name in PROGRAMS:
        path = resolve_binary(build_dir, name, name, platform)
        programs[name] = {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
    tests = resolve_binary(build_dir, "tests", "tests", platform)

    # The libraries CMake copied beside the test program are the ones the tests load. Each must be
    # a byte-for-byte copy of a pinned dependency file, never something found on the build host.
    pinned = {}
    for dep in ("onetbb", "embree"):
        for directory in deps[dep]["runtime_dirs"]:
            for entry in pathlib.Path(directory).iterdir():
                if entry.is_file():
                    pinned.setdefault(sha256_file(entry), "%s:%s" % (dep, entry.name))
    tested_libraries = {}
    for entry in sorted(tests.parent.iterdir()):
        if entry.is_file() and (entry.suffix.lower() in (".dll", ".dylib") or ".so" in entry.name):
            digest = sha256_file(entry)
            if digest not in pinned:
                raise Failure("%s beside the test program is not a pinned dependency file (sha256 %s)" % (entry.name, digest))
            tested_libraries[entry.name] = {"sha256": digest, "pinned_file": pinned[digest]}

    env = dict(os.environ)
    if args.asan:
        # inherited from the upstream script: the tools are not yet free of leaks at exit
        env["ASAN_OPTIONS"] = "detect_leaks=false"
    suites = {}
    if not args.skip_full_suite:
        suites["full"] = run_gtest(tests, build_dir, "full", [], env)
    focused = run_gtest(tests, build_dir, "focused", ["--gtest_filter=" + FOCUSED_TEST], env)
    if focused["tests_run"] != 1 or focused["names_run"] != [FOCUSED_TEST]:
        raise Failure("the focused run did not execute exactly %s: %r" % (FOCUSED_TEST, focused["names_run"]))
    suites["focused"] = focused

    cache = cmake_cache(build_dir)
    cmake_version = run(["cmake", "--version"], capture=True)[1].splitlines()[0]
    report = {
        "schema": "auto-pigeon-ericw-build-report/1",
        "platform": platform,
        "commit": plan["commit"],
        "version": plan["version"],
        "stamp": plan["stamp"],
        "tracked_files_modified": bool(dirty),
        "asan": args.asan,
        "from_source_archive": from_source_archive,
        "build_type": build_type,
        "full_suite_skipped": args.skip_full_suite,
        "cmake_options": options,
        "cmake_generator": cache.get("CMAKE_GENERATOR", ""),
        "cmake_version": cmake_version,
        "compiler": compiler_identity(build_dir),
        "cxx_flags": cache.get("CMAKE_CXX_FLAGS", ""),
        "cxx_flags_release": cache.get("CMAKE_CXX_FLAGS_RELEASE", ""),
        "osx_deployment_target": cache.get("CMAKE_OSX_DEPLOYMENT_TARGET", ""),
        "host": {"system": pyplatform.system(), "release": pyplatform.release(), "version": pyplatform.version(),
                 "machine": pyplatform.machine(), "runner_image": os.environ.get("ImageOS", ""),
                 "runner_image_version": os.environ.get("ImageVersion", "")},
        "build_parallel_jobs": args.jobs,
        "timings_seconds": timings,
        "programs": programs,
        "tests_program": str(tests),
        "tested_libraries": tested_libraries,
        "tests": suites,
        "focused_test": FOCUSED_TEST,
        "build_dir": str(build_dir),
    }
    write_json(args.report, report)
    log("wrote %s" % args.report)


if __name__ == "__main__":
    try:
        main()
    except Failure as e:
        sys.exit("build: " + str(e))
