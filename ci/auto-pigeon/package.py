#!/usr/bin/env python3
"""Stage one built target as a relocatable toolchain and write its ZIP.

    python3 ci/auto-pigeon/package.py --platform linux-amd64 --deps <deps dir> \
        --build-report build-report.json --plan plan.json --out <dist dir>

Programs come from `cmake --install`; libraries are NOT globbed: each one is in the archive
because a shipped program or library asks for it by name, and it is copied from a declared
dependency directory, once. Two different files answering to one name is a failure unless
deps.lock.json's runtime_policy names it.
"""

import argparse
import os
import pathlib
import shutil
import stat
import sys
import zipfile

import closure
from common import HERE, PLATFORMS, PROGRAMS, REPO, Failure, exe, load_json, log, require_native, run, sha256_file, write_json

STATIC_LICENSES = {
    "COPYING": "COPYING",
    "gpl_v3.txt": "gpl_v3.txt",
    "LICENSE-fmt.txt": "3rdparty/fmt/LICENSE",
    "LICENSE-jsoncpp.txt": "3rdparty/jsoncpp/LICENSE",
    "LICENSE-pareto.txt": "3rdparty/pareto/LICENSE",
}
MSVC_NOTE = """Microsoft Visual C++ runtime

The files {files} in bin/ are the Microsoft Visual C++ runtime libraries, copied unmodified from
the Visual Studio redistributable directory of the build host:

  {source}

Microsoft permits distributing these files beside an application that needs them (the Visual
Studio "Distributable Code" list, app-local deployment). They are not part of ericw-tools and are
not covered by its licence. The Universal C Runtime is a component of Windows 10 and later and is
not shipped here.
"""


def find_msvc_redist():
    vswhere = pathlib.Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    root = run([vswhere, "-latest", "-products", "*", "-property", "installationPath"], capture=True)[1].strip().splitlines()[0]
    candidates = sorted(pathlib.Path(root, "VC", "Redist", "MSVC").glob("*/x64/Microsoft.VC*.CRT"),
                        key=lambda p: tuple(int(x) if x.isdigit() else 0 for x in p.parts[-3].split(".")))
    if not candidates:
        raise Failure("no MSVC redistributable directory under %s" % root)
    return candidates[-1]


def locate(name, sources, platform, policy):
    """Find the one file to ship for `name`. sources: [(label, dir)]."""
    hits = []
    for label, directory in sources:
        directory = pathlib.Path(directory)
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            same = entry.name.lower() == name.lower() if platform.startswith("windows") else entry.name == name
            if same and entry.exists():
                real = entry.resolve()
                hits.append({"label": label, "path": str(real), "sha256": sha256_file(real)})
    if not hits:
        return None, None
    digests = {h["sha256"] for h in hits}
    if len(digests) == 1:
        return hits[0], ({"name": name, "identical_copies": hits} if len(hits) > 1 else None)
    preferred = [h for h in hits if h["label"] == "onetbb"]
    if name in policy and len(preferred) == 1:
        return preferred[0], {"name": name, "resolved_by": "runtime_policy.prefer_onetbb_over_embree_bundled", "copies": hits}
    raise Failure("%s exists with different bytes in %s and no runtime policy covers it" % (name, ", ".join(h["path"] for h in hits)))


def is_system(platform, name):
    if platform.startswith("linux"):
        return name in closure.LINUX_SYSTEM
    if platform.startswith("windows"):
        if closure.WINDOWS_API_SET.match(name):
            return True
        if closure.WINDOWS_NEVER_SYSTEM.match(name):
            return False
        return (pathlib.Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name).is_file()
    return False  # macOS: needed_names() only returns @rpath names, which are never the system's


def fix_macho(path, platform, modified):
    info = closure.macho_info(path)
    changed = False
    for rpath in info["rpaths"]:
        if not rpath.startswith(("@loader_path", "@executable_path")):
            run(["install_name_tool", "-delete_rpath", rpath, path])
            changed = True
    if path.suffix != ".dylib" and not any(r.rstrip("/") == "@loader_path" for r in info["rpaths"]):
        run(["install_name_tool", "-add_rpath", "@loader_path", path])
        changed = True
    if changed:
        if platform == "macos-arm64":
            run(["codesign", "--force", "--sign", "-", path])  # ad hoc: arm64 refuses to run an unsigned or stale image
        modified.append(path.name)


def tested_identity(platform, tests_program, shipped):
    """Did the GoogleTest run load the same library bytes that are being shipped?"""
    tests_program = pathlib.Path(tests_program)
    result = {}
    if platform.startswith("linux"):
        env = {k: v for k, v in os.environ.items() if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD")}
        out = run(["ldd", tests_program], capture=True, env=env)[1]
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[1] == "=>" and parts[0] in shipped:
                result[parts[0]] = sha256_file(parts[2])
    else:
        beside = {p.name.lower(): p for p in tests_program.parent.iterdir() if p.is_file()}
        digests = {sha256_file(p) for p in beside.values() if p.suffix.lower() in (".dll", ".dylib")}
        for name, digest in shipped.items():
            if name.lower() in beside:
                result[name] = sha256_file(beside[name.lower()])
            elif digest in digests:
                result[name] = digest
    problems = ["%s: tests loaded %s, the archive ships %s" % (n, result[n], shipped[n]) for n in result if result[n] != shipped[n]]
    if problems:
        raise Failure("the tested and the shipped runtime libraries differ:\n  " + "\n  ".join(problems))
    return {name: (name in result) for name in sorted(shipped)}


def requirements_text(platform, closure_report, msvc):
    if platform.startswith("linux"):
        b = closure_report["symbol_baseline"]
        return ("x86-64 Linux with glibc **%s** or newer and a libstdc++ providing `GLIBCXX_%s` (measured from the shipped "
                "files; built on Ubuntu 22.04). Embree, oneTBB and tbbmalloc are in `bin/` and are found through the "
                "programs' `$ORIGIN` run path; `LD_LIBRARY_PATH` is not needed." % (b.get("GLIBC", "?"), b.get("GLIBCXX", "?")))
    if platform.startswith("windows"):
        return ("64-bit Windows 10 or later. Embree, oneTBB, tbbmalloc and the Visual C++ runtime (%s) are in `bin\\`; "
                "no Visual Studio or redistributable installer is needed." % ", ".join(msvc))
    arch = "Intel (x86_64)" if platform == "macos-amd64" else "Apple silicon (arm64)"
    return ("%s Mac running macOS **%s** or newer (the highest minimum declared by the shipped files, measured with "
            "`otool`). The build is not signed or notarised: after downloading with a browser, clear the quarantine flag "
            "with `xattr -dr com.apple.quarantine <folder>`." % (arch, closure_report.get("minimum_macos") or "unknown"))


def usage_text(platform, stem):
    if platform.startswith("windows"):
        return ("```powershell\n"
                "& \"C:\\Tools\\%s\\bin\\qbsp.exe\" \"C:\\Maps\\my map.map\"\n"
                "& \"C:\\Tools\\%s\\bin\\vis.exe\" \"C:\\Maps\\my map.bsp\"\n"
                "& \"C:\\Tools\\%s\\bin\\light.exe\" \"C:\\Maps\\my map.bsp\"\n```" % (stem, stem, stem))
    return ("```sh\n"
            "\"$HOME/tools/%s/bin/qbsp\" \"$HOME/maps/my map.map\"\n"
            "\"$HOME/tools/%s/bin/vis\" \"$HOME/maps/my map.bsp\"\n"
            "\"$HOME/tools/%s/bin/light\" \"$HOME/maps/my map.bsp\"\n```" % (stem, stem, stem))


def write_zip(root, zip_path, timestamp):
    """Fixed member order, times and modes. The program bytes inside are whatever the compiler wrote."""
    import time
    date = time.gmtime(max(timestamp, 315532800))[:6]
    members = sorted(p for p in root.rglob("*"))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        def add_dir(rel):
            info = zipfile.ZipInfo(rel + "/", date)
            info.create_system = 3
            info.external_attr = ((stat.S_IFDIR | 0o755) << 16) | 0x10
            z.writestr(info, b"")
        add_dir(root.name)
        for p in members:
            rel = root.name + "/" + p.relative_to(root).as_posix()
            if p.is_symlink():
                raise Failure("refusing to archive the symbolic link %s" % rel)
            if p.is_dir():
                add_dir(rel)
                continue
            mode = 0o755 if (p.parent.name == "bin" and p.suffix not in (".dll", ".so", ".dylib") and ".so." not in p.name) else 0o644
            info = zipfile.ZipInfo(rel, date)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | mode) << 16
            z.writestr(info, p.read_bytes(), compresslevel=9)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", required=True, choices=PLATFORMS)
    ap.add_argument("--deps", required=True)
    ap.add_argument("--build-report", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-local", action="store_true",
                    help="accept a modified tree or a skipped full suite; the result is marked not releasable")
    args = ap.parse_args()
    platform = args.platform
    require_native(platform)
    plan = load_json(args.plan)
    deps = load_json(pathlib.Path(args.deps) / "deps.json")
    report = load_json(args.build_report)
    lock = load_json(HERE / "deps.lock.json")
    for key in ("commit", "version", "stamp"):
        if report[key] != plan[key]:
            raise Failure("build report %s=%r does not match the plan's %r" % (key, report[key], plan[key]))
    if report["platform"] != platform or deps["platform"] != platform:
        raise Failure("report/dependencies are not for %s" % platform)
    if report["asan"]:
        raise Failure("an AddressSanitizer build is never packaged")
    releasable = not (report["tracked_files_modified"] or report["full_suite_skipped"])
    if not releasable and not args.allow_local:
        raise Failure("the build is from a modified tree or skipped the full test suite; not packaging it")

    build_dir = pathlib.Path(report["build_dir"])
    out = pathlib.Path(args.out).resolve()
    stem = plan["assets"][platform][:-len(".zip")]
    stage = out / "stage-install"
    root = out / stem
    for d in (stage, root):
        if d.exists():
            shutil.rmtree(d)
    run(["cmake", "--install", build_dir, "--config", report["build_type"], "--prefix", stage])
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    programs = [exe(platform, n) for n in PROGRAMS]
    for name in programs:
        src = stage / name
        if not src.is_file():
            raise Failure("cmake --install did not produce %s" % name)
        shutil.copyfile(src, bin_dir / name)
        os.chmod(bin_dir / name, 0o755)

    sources = [("onetbb", d) for d in deps["onetbb"]["runtime_dirs"]] + [("embree", d) for d in deps["embree"]["runtime_dirs"]]
    msvc_dir = None
    if platform.startswith("windows"):
        msvc_dir = find_msvc_redist()
        sources.append(("msvc-redist", str(msvc_dir)))
    policy = set(lock["runtime_policy"]["prefer_onetbb_over_embree_bundled"])
    libraries, decisions, todo, modified = {}, [], list(programs), []
    if platform.startswith("macos"):
        for name in programs:
            fix_macho(bin_dir / name, platform, modified)
    while todo:
        current = todo.pop()
        for name in closure.needed_names(platform, bin_dir / current):
            key = name.lower() if platform.startswith("windows") else name
            if key in libraries or (bin_dir / name).exists():
                continue
            hit, decision = locate(name, sources, platform, policy)
            if hit is None:
                if is_system(platform, name):
                    continue
                raise Failure("%s needs %s, which no declared dependency directory provides" % (current, name))
            if decision:
                decisions.append(decision)
            shutil.copyfile(hit["path"], bin_dir / name)
            os.chmod(bin_dir / name, 0o644)
            libraries[key] = {"file": name, "from": hit["label"], "source_sha256": hit["sha256"]}
            if platform.startswith("macos"):
                fix_macho(bin_dir / name, platform, modified)
            todo.append(name)

    tested = tested_identity(platform, report["tests_program"], {v["file"]: v["source_sha256"] for v in libraries.values()})

    lic = root / "licenses"
    lic.mkdir()
    for name, rel in STATIC_LICENSES.items():
        shutil.copyfile(REPO / rel, lic / name)
    for dep in ("embree", "onetbb"):
        for name, path in deps[dep]["license_files"].items():
            shutil.copyfile(path, lic / name)
    msvc_files = sorted(v["file"] for v in libraries.values() if v["from"] == "msvc-redist")
    if msvc_files:
        (lic / "NOTICE-msvc-runtime.txt").write_text(MSVC_NOTE.format(files=", ".join(msvc_files), source="<Visual Studio>\\VC" + str(msvc_dir).split("VC", 1)[-1]), encoding="utf-8", newline="\n")
    shutil.copyfile(REPO / "README.md", lic / "README-upstream.md")

    closure_report = closure.check(platform, bin_dir, programs)
    if closure_report.get("not_reached_from_programs"):
        raise Failure("bin/ holds files no program reaches: %s" % closure_report["not_reached_from_programs"])

    text = (HERE / "ARCHIVE-README.md.in").read_text(encoding="utf-8")
    for key, value in {
        "VERSION": plan["version"], "PLATFORM": platform, "TAG": plan["tag"], "COMMIT": plan["commit"],
        "FORK": plan["fork_repository"], "UPSTREAM": plan["upstream_repository"],
        "UPSTREAM_DESCRIBE": plan["upstream_describe"], "STAMP": plan["stamp"], "STEM": stem,
        "SOURCE_ASSET": plan["source_asset"],
        "REQUIREMENTS": requirements_text(platform, closure_report, msvc_files), "USAGE": usage_text(platform, stem),
    }.items():
        text = text.replace("@%s@" % key, value)
    (root / "README.md").write_text(text, encoding="utf-8", newline="\n")

    cpu = ("x86-64 baseline (SSE2); no -march/-mtune flag is passed to the compiler. Embree selects SSE2/SSE4.2/AVX/AVX2/AVX-512 "
           "kernels at run time from the CPU it finds." if platform.endswith("amd64") else
           "arm64 (ARMv8-A with NEON), the compiler's default for Apple silicon; no -mcpu flag is passed.")
    build_info = {
        "schema": "auto-pigeon-ericw-build-info/1",
        "product": "Auto-Pigeon fork of ericw-tools",
        "release": {"version": plan["version"], "tag": plan["tag"], "asset": plan["assets"][platform]},
        "fork": {"repository": plan["fork_repository"], "commit": plan["commit"], "commit_count": plan["commit_count"]},
        "upstream": {"repository": plan["upstream_repository"], "describe": plan["upstream_describe"], "base_tag": plan["upstream_base_tag"]},
        "version_banner": plan["stamp"],
        "target": platform,
        "releasable": releasable,
        "signed": False,
        "signing_note": "unsigned development build; SHA-256 digests are integrity checks, not signatures",
        "build": {key: report[key] for key in ("build_type", "cmake_options", "cmake_generator", "cmake_version", "compiler",
                                                  "cxx_flags", "cxx_flags_release", "osx_deployment_target", "host", "build_parallel_jobs")},
        "cpu_baseline": cpu,
        "tests": report["tests"],
        "dependencies": {
            "embree": {k: v for k, v in deps["embree"].items() if k not in ("cmake_dir", "runtime_dirs", "license_files")},
            "onetbb": {k: v for k, v in deps["onetbb"].items() if k not in ("cmake_dir", "runtime_dirs", "license_files")},
            "googletest": dict(lock["googletest"], note="test program only; not linked into the released programs"),
            "submodules": plan["submodules"],
            "msvc_runtime": ({"files": msvc_files, "redist_version": msvc_dir.parts[-3]} if msvc_dir else None),
        },
        "runtime_libraries": sorted(libraries.values(), key=lambda v: v["file"]),
        "runtime_library_decisions": decisions,
        "runtime_libraries_tested_by_the_test_suite": tested,
        "files_modified_after_build": sorted(set(modified)),
        "runtime_closure": closure_report,
    }
    for options in (build_info["build"]["cmake_options"],):
        build_info["build"]["cmake_options"] = [o if not o.startswith(("-Dembree_DIR=", "-DTBB_DIR=", "-DCMAKE_LIBRARY_PATH=")) else o.split("=")[0] + "=<dependency directory>" for o in options]
    build_info["build"]["host"] = {k: v for k, v in report["host"].items()}
    scrub = lambda o: (  # noqa: E731 - no private build-host paths in a published file
        {k: scrub(v) for k, v in o.items()} if isinstance(o, dict) else [scrub(v) for v in o] if isinstance(o, list)
        else o.replace(str(bin_dir.resolve()), "<archive>/bin").replace(str(bin_dir), "<archive>/bin") if isinstance(o, str) else o)
    build_info = scrub(build_info)
    for decision in build_info["runtime_library_decisions"]:
        for copy in decision.get("copies", []) + decision.get("identical_copies", []):
            copy["path"] = pathlib.PurePath(copy["path"]).name
    write_json(root / "build-info.json", build_info)

    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            is_exec = rel.startswith("bin/") and p.name in programs
            files.append({"path": rel, "size": p.stat().st_size, "sha256": sha256_file(p), "mode": "0755" if is_exec else "0644"})
    write_json(root / "MANIFEST.json", {
        "schema": "auto-pigeon-ericw-archive-manifest/1", "root": stem, "platform": platform, "commit": plan["commit"],
        "version": plan["version"], "stamp": plan["stamp"], "programs": ["bin/" + n for n in programs],
        "note": "every file of the archive except this one", "files": files})

    zip_path = out / plan["assets"][platform]
    write_zip(root, zip_path, plan["commit_time"])
    package = {
        "schema": "auto-pigeon-ericw-package/1", "asset": zip_path.name, "size": zip_path.stat().st_size,
        "sha256": sha256_file(zip_path), "platform": platform, "commit": plan["commit"], "version": plan["version"],
        "stamp": plan["stamp"], "releasable": releasable, "manifest_sha256": sha256_file(root / "MANIFEST.json"),
        "tests": report["tests"], "members": len(files) + 1,
    }
    write_json(out / (stem + ".package.json"), package)
    shutil.copyfile(args.build_report, out / (stem + ".build-report.json"))
    shutil.rmtree(stage)
    log("packaged %s  %d bytes  sha256 %s" % (zip_path.name, package["size"], package["sha256"]))


if __name__ == "__main__":
    try:
        main()
    except Failure as e:
        sys.exit("package: " + str(e))
