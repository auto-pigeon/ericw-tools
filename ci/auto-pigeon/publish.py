#!/usr/bin/env python3
"""Publish ONE development prerelease from archives that were already built and accepted.

    python3 ci/auto-pigeon/publish.py --plan plan.json --artifacts <dir> --repo owner/name \
        --run-url <url> --out <dir> [--dry-run]

This never compiles, edits an archive or downloads a toolchain. It reads, for each target,
the candidate's package record and its native acceptance report, and publishes a target only
when the two agree with the plan and with the bytes on disk. Linux and Windows are required;
a macOS candidate that did not pass is left out and named in the notes.

A rerun never replaces anything: an existing tag must point at the planned commit, an existing
asset must be byte-identical, and a difference stops the job with an explanation.
"""

import argparse
import json
import os
import pathlib
import sys
import tempfile

from common import PLATFORMS, REQUIRED_PLATFORMS, Failure, load_json, log, run, sha256_file, write_json

LABELS = {"linux-amd64": "Linux x86-64", "windows-amd64": "Windows x86-64", "macos-amd64": "macOS Intel (x86-64)",
          "macos-arm64": "macOS Apple silicon (arm64)"}


def find(root, name):
    hits = sorted(root.rglob(name))
    return hits[0] if hits else None


def evaluate(platform, plan, artifacts):
    """(accepted record | None, reason when not accepted)."""
    asset = plan["assets"][platform]
    stem = asset[:-len(".zip")]
    package_path, zip_path = find(artifacts, stem + ".package.json"), find(artifacts, asset)
    report_path = find(artifacts, "acceptance-%s.json" % platform)
    if not package_path or not zip_path:
        return None, "no archive was built for this target in this run"
    if not report_path:
        return None, "the archive was built but its native acceptance produced no report"
    package, report = load_json(package_path), load_json(report_path)
    if report.get("verdict") != "pass":
        return None, "native acceptance failed: %s" % report.get("failure", "see the run")
    digest = sha256_file(zip_path)
    for label, got, want in (
        ("package commit", package["commit"], plan["commit"]), ("package version", package["version"], plan["version"]),
        ("package stamp", package["stamp"], plan["stamp"]), ("acceptance commit", report["commit"], plan["commit"]),
        ("acceptance stamp", report["stamp"], plan["stamp"]), ("accepted archive digest", report["archive_sha256"], digest),
        ("packaged archive digest", package["sha256"], digest), ("acceptance platform", report["platform"], platform),
    ):
        if got != want:
            raise Failure("%s: %s is %r, expected %r" % (platform, label, got, want))
    if not package.get("releasable"):
        raise Failure("%s: the archive is marked not releasable" % platform)
    if not all(step["status"] == "pass" for step in report["steps"]) or len(report["steps"]) < 9:
        raise Failure("%s: the acceptance report says pass but not all nine steps passed" % platform)
    full = package["tests"]["full"]
    return {"platform": platform, "name": asset, "path": str(zip_path), "size": zip_path.stat().st_size, "sha256": digest,
            "kind": "toolchain", "acceptance": {"verdict": "pass", "steps_passed": len(report["steps"]),
                                                "commands_run": len(report["commands"])},
            "tests": {"run": full["tests_run"], "failures": full["failures"], "skipped_by_the_suite": full["skipped"],
                      "focused": package["tests"]["focused"]["names_run"]}}, None


def notes(plan, accepted, missing, source, run_url):
    lines = [
        "**Unsigned development build of the Auto-Pigeon fork of ericw-tools.** This is not an upstream release: "
        "upstream is [ericwa/ericw-tools](%s), and upstream has not reviewed or accepted the fork's change." % plan["upstream_repository"],
        "",
        "- Fork commit: [`%s`](%s/commit/%s)" % (plan["commit"], plan["fork_repository"], plan["commit"]),
        "- Upstream base: `%s`" % plan["upstream_describe"],
        "- Every program prints `ericw-tools %s`" % plan["stamp"],
        "",
        "### What the fork changes",
        "",
        "`qbsp`'s `SplitBrush` splits thin brush fragments with `PLANESIDE_EPSILON` instead of `0.1`. With the old value a "
        "sliver thinner than 0.1 unit could be kept whole on one side of a split while the node volume was divided, leaving an "
        "empty crack through a valid wall, which the clip hulls then leaked through. The regression test "
        "`testmapsQ1.cliphull2ThinFragmentSplit` and its map are part of the source. This fixes that defect; it is not a claim "
        "that every map now compiles.",
        "",
        "### Downloads",
        "",
        "| Target | Archive | Native tests | Accepted from the extracted ZIP |",
        "| --- | --- | --- | --- |",
    ]
    for a in accepted:
        lines.append("| %s | `%s` | %d run, %d failed | yes: %d steps on a native runner |" % (
            LABELS[a["platform"]], a["name"], a["tests"]["run"], a["tests"]["failures"], a["acceptance"]["steps_passed"]))
    for platform, reason in missing:
        lines.append("| %s | **not published** | | %s |" % (LABELS[platform], reason))
    lines += [
        "",
        "Each ZIP holds `qbsp`, `vis`, `light`, `bspinfo`, `bsputil` and `maputil` with the Embree, oneTBB and (on Windows) "
        "Visual C++ runtime libraries they load, the licences, a README and `build-info.json`. No Windows or Linux arm64 "
        "build exists. A target listed as not published has no supported binary in this release.",
        "",
        "`%s` is the complete corresponding source: the fork at this commit with all submodules, the pinned GoogleTest and "
        "the build recipes. GitHub's automatic \"Source code\" links below omit the submodules and cannot rebuild it." % source["name"],
        "",
        "### Verify",
        "",
        "```sh\nsha256sum -c SHA256SUMS --ignore-missing        # Linux\nshasum -a 256 -c SHA256SUMS --ignore-missing    # macOS\n```",
        "```powershell\n(Get-FileHash .\\%s -Algorithm SHA256).Hash.ToLower()   # compare with SHA256SUMS\n```" % plan["assets"]["windows-amd64"],
        "",
        "A SHA-256 is an integrity check, not a signature. `release-manifest.json` records every asset, its digest and its "
        "acceptance verdict.",
        "",
        "Built, tested and accepted by %s." % run_url,
    ]
    return "\n".join(lines) + "\n"


def gh(args, check=True):
    return run(["gh", *args], capture=True, check=check)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--artifacts", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--run-url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true", help="decide and write the manifest, notes and sums; touch nothing remote")
    args = ap.parse_args()
    plan = load_json(args.plan)
    artifacts = pathlib.Path(args.artifacts).resolve()
    out = pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not args.dry_run:
        if not plan["publish"]:
            raise Failure("this run's plan does not publish")
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise Failure("releases are published from main only, not from %r" % os.environ.get("GITHUB_REF"))

    accepted, missing = [], []
    for platform in PLATFORMS:
        record, reason = evaluate(platform, plan, artifacts)
        if record:
            accepted.append(record)
        elif platform in REQUIRED_PLATFORMS:
            raise Failure("required target %s cannot be published: %s" % (platform, reason))
        else:
            missing.append((platform, reason))
    source_path, source_meta = find(artifacts, plan["source_asset"]), find(artifacts, plan["source_asset"][:-len(".tar.gz")] + ".source.json")
    if not source_path or not source_meta:
        raise Failure("the source archive is missing")
    meta = load_json(source_meta)
    if meta["commit"] != plan["commit"] or meta["sha256"] != sha256_file(source_path):
        raise Failure("the source archive does not match its record or the plan")
    source = {"name": source_path.name, "path": str(source_path), "size": source_path.stat().st_size, "sha256": meta["sha256"],
              "kind": "source", "files": meta["files"], "rebuilt_and_tested_from_the_archive": bool(meta.get("rebuild_verified"))}

    manifest = {
        "schema": "auto-pigeon-ericw-release-manifest/1",
        "tag": plan["tag"], "version": plan["version"], "commit": plan["commit"], "version_banner": plan["stamp"],
        "upstream": {"repository": plan["upstream_repository"], "describe": plan["upstream_describe"]},
        "fork": plan["fork_repository"], "prerelease": True, "signed": False, "built_by": args.run_url,
        "assets": [{k: v for k, v in a.items() if k != "path"} for a in accepted + [source]],
        "targets": dict({a["platform"]: {"status": "published", "asset": a["name"]} for a in accepted},
                        **{p: {"status": "not_published", "reason": r} for p, r in missing}),
    }
    manifest_path = out / "release-manifest.json"
    write_json(manifest_path, manifest)
    uploads = [pathlib.Path(a["path"]) for a in accepted] + [source_path, manifest_path]
    sums_path = out / "SHA256SUMS"
    with open(sums_path, "w", encoding="utf-8", newline="\n") as f:
        for p in uploads:  # SHA256SUMS does not list itself
            f.write("%s  %s\n" % (sha256_file(p), p.name))
    uploads.append(sums_path)
    notes_path = out / "release-notes.md"
    notes_path.write_text(notes(plan, accepted, missing, source, args.run_url), encoding="utf-8", newline="\n")
    log("targets: %s" % json.dumps(manifest["targets"], sort_keys=True))
    if args.dry_run:
        log("dry run: nothing was published")
        return

    repo, tag = args.repo, plan["tag"]
    code, ref, _ = gh(["api", "repos/%s/git/ref/tags/%s" % (repo, tag), "--jq", ".object.sha"], check=False)
    if code == 0 and ref.strip() != plan["commit"]:
        raise Failure("tag %s already exists and points at %s, not at the planned %s; refusing to move it" % (tag, ref.strip(), plan["commit"]))
    code, existing, _ = gh(["release", "view", tag, "--repo", repo, "--json", "assets,isDraft,targetCommitish"], check=False)
    remote = {}
    if code == 0:
        info = json.loads(existing)
        remote = {a["name"]: a for a in info["assets"]}
        log("release %s already exists (draft=%s) with %d asset(s); comparing, never replacing" % (tag, info["isDraft"], len(remote)))
        with tempfile.TemporaryDirectory() as tmp:
            for p in uploads:
                if p.name not in remote:
                    continue
                gh(["release", "download", tag, "--repo", repo, "--pattern", p.name, "--dir", tmp])
                theirs, ours = sha256_file(pathlib.Path(tmp) / p.name), sha256_file(p)
                if theirs != ours:
                    raise Failure(
                        "asset %s of the existing release %s has SHA-256 %s; this run produced %s. The published asset is kept "
                        "and nothing was uploaded. If only the run metadata differs this is expected for a second run of one "
                        "commit; if a toolchain archive differs, the build is not byte-reproducible, which is a finding, not a "
                        "licence to replace a published binary." % (p.name, tag, theirs, ours))
        todo = [p for p in uploads if p.name not in remote]
        if todo:
            gh(["release", "upload", tag, "--repo", repo, *[str(p) for p in todo]])
    else:
        gh(["release", "create", tag, "--repo", repo, "--target", plan["commit"], "--draft", "--prerelease",
            "--title", "Auto-Pigeon EricW tools %s (development build)" % plan["version"],
            "--notes-file", str(notes_path), *[str(p) for p in uploads]])
    gh(["release", "edit", tag, "--repo", repo, "--draft=false", "--prerelease"])

    # Observe the publication instead of trusting the commands above.
    _, view, _ = gh(["release", "view", tag, "--repo", repo, "--json", "assets,isDraft,isPrerelease,url,tagName"])
    info = json.loads(view)
    _, ref, _ = gh(["api", "repos/%s/git/ref/tags/%s" % (repo, tag), "--jq", ".object.sha"])
    if info["isDraft"] or not info["isPrerelease"] or ref.strip() != plan["commit"]:
        raise Failure("the release is not a published prerelease at the planned commit: %s, tag at %s" % (view, ref.strip()))
    with tempfile.TemporaryDirectory() as tmp:
        gh(["release", "download", tag, "--repo", repo, "--dir", tmp])
        published = []
        for p in uploads:
            got = sha256_file(pathlib.Path(tmp) / p.name)
            if got != sha256_file(p):
                raise Failure("the published %s does not have the digest that was uploaded" % p.name)
            published.append({"name": p.name, "sha256": got, "size": p.stat().st_size,
                              "url": "https://github.com/%s/releases/download/%s/%s" % (repo, tag, p.name)})
    write_json(out / "publish-report.json", {"release_url": info["url"], "tag": tag, "commit": plan["commit"], "assets": published,
                                             "targets": manifest["targets"]})
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Published %s\n\n%s\n\n" % (tag, info["url"]))
            for a in published:
                f.write("- `%s`  `%s`\n" % (a["sha256"], a["name"]))
    log("published %s with %d assets" % (info["url"], len(published)))


if __name__ == "__main__":
    try:
        main()
    except Failure as e:
        sys.exit("publish: " + str(e))
