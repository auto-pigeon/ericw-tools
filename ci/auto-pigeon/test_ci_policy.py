"""Fast behavioral checks for smoke qualification and publication guards."""
import copy
import json
import pathlib
import re
import tempfile
import unittest
from unittest.mock import patch

import accept
import publish
from common import Failure, FOCUSED_TEST, HERE, PLATFORMS, REPO, REQUIRED_PLATFORMS, SMOKE_ACCEPTANCE_STEPS, sha256_file, validated_tests

OPTIONAL_PLATFORMS = tuple(p for p in PLATFORMS if p not in REQUIRED_PLATFORMS)
WORKFLOW = REPO / ".github" / "workflows" / "auto-pigeon-release.yml"


def smoke_report():
    return {"test_mode": "smoke", "full_suite_skipped": True, "tests": {"focused": {
        "exit": 0, "tests_run": 1, "names_run": [FOCUSED_TEST], "failures": 0,
        "errors": 0, "skipped": 0}}}


class QualificationTests(unittest.TestCase):
    def test_smoke_is_explicit_and_full_is_preserved(self):
        report = smoke_report()
        self.assertEqual(validated_tests(report)["tests_run"], 1)
        report.update(test_mode="full", full_suite_skipped=False)
        report["tests"]["full"] = dict(report["tests"]["focused"], tests_run=432)
        self.assertEqual(validated_tests(report)["tests_run"], 432)

    def test_empty_failed_skipped_wrong_and_iteration_are_refused(self):
        for key, value in (("tests_run", 0), ("tests_run", 2), ("exit", 1),
                           ("failures", 1), ("errors", 1), ("skipped", 1),
                           ("names_run", ["unrelated.test"])):
            with self.subTest(key=key, value=value):
                report = smoke_report()
                report["tests"]["focused"][key] = value
                with self.assertRaises(Failure):
                    validated_tests(report)
        for mode in ("iteration", "unknown", "full"):
            with self.subTest(mode=mode):
                report = smoke_report()
                report["test_mode"] = mode
                with self.assertRaises(Failure):
                    validated_tests(report)
        report = smoke_report()
        report["tests"]["full"] = {}
        with self.assertRaises(Failure):
            validated_tests(report)

    def test_archive_smoke_selects_only_seven_steps(self):
        for smoke, expected in ((True, 7), (False, 11)):
            with self.subTest(smoke=smoke), tempfile.TemporaryDirectory() as tmp:
                obj = accept.Acceptance.__new__(accept.Acceptance)
                obj.smoke, obj.platform, obj.work = smoke, "linux-amd64", pathlib.Path(tmp)
                obj.report = {}
                observed = []
                obj.step = lambda name, fn: observed.append(name)
                with patch.object(accept, "require_native"):
                    obj.run_all()
                self.assertEqual(len(observed), expected)
                self.assertEqual(obj.report["verdict"], "pass")
                self.assertTrue(any("negative control" in name for name in observed))
                self.assertEqual(any("thin-fragment" in name for name in observed), not smoke)

    def test_publication_requires_complete_acceptance_and_matching_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            archive = root / "tools.zip"
            archive.write_bytes(b"test archive")
            from common import sha256_file
            digest = sha256_file(archive)
            plan = {"assets": {"linux-amd64": "tools.zip"}, "commit": "abc", "version": "1.1", "stamp": "stamp"}
            package = dict(smoke_report(), commit="abc", version="1.1", stamp="stamp", sha256=digest, releasable=True)
            report = {"commit": "abc", "stamp": "stamp", "platform": "linux-amd64", "verdict": "pass",
                      "archive_sha256": digest, "acceptance_mode": "smoke", "commands": [],
                      "steps": [{"name": str(i), "status": "pass"} for i in range(7)]}
            def write(p, r):
                (root / "tools.package.json").write_text(json.dumps(p))
                (root / "acceptance-linux-amd64.json").write_text(json.dumps(r))
            write(package, report)
            result, reason = publish.evaluate("linux-amd64", plan, root)
            self.assertIsNone(reason)
            self.assertEqual(result["test_mode"], "smoke")
            self.assertEqual(result["tests"]["run"], 1)
            for bad in (dict(report, steps=report["steps"][:-1]),
                        dict(report, archive_sha256="wrong"),
                        dict(report, steps=[report["steps"][0]] * 7)):
                write(package, bad)
                with self.assertRaises(Failure):
                    publish.evaluate("linux-amd64", plan, root)
            bad = copy.deepcopy(package)
            bad["tests"]["focused"]["skipped"] = 1
            write(bad, report)
            with self.assertRaises(Failure):
                publish.evaluate("linux-amd64", plan, root)


def workflow_jobs(text):
    """The job graph of the workflow as written. Standard library only: Plan runs this before anything is installed."""
    jobs = {}
    for block in re.split(r"(?m)^(?=  [\w-]+:\n)", text.split("\njobs:\n", 1)[1]):
        head = re.match(r"  ([\w-]+):\n", block)
        if not head:
            continue
        needs = re.search(r"(?m)^    needs: (.+)$", block)
        condition = re.search(r"(?m)^    if: (.+)$", block)
        tolerated = re.search(r"(?m)^    continue-on-error: (.+)$", block)
        entries = re.findall(r"- \{ platform: ([\w-]+), runner: [\w.-]+(?:, candidate: (true|false))?", block)
        jobs[head.group(1)] = {
            "needs": [n.strip() for n in needs.group(1).strip("[]").split(",")] if needs else [],
            "if": re.sub(r"^\$\{\{\s*|\s*\}\}$", "", condition.group(1).strip()) if condition else None,
            "tolerated": tolerated.group(1).strip() if tolerated else None,
            "targets": [p for p, _ in entries], "candidates": [p for p, c in entries if c == "true"], "text": block,
        }
    return jobs


def evaluate(expression, values, cancelled):
    """GitHub's job-level condition language as far as this workflow uses it; anything else raises instead of guessing."""
    tokens = re.findall(r"'[^']*'|&&|\|\||==|!=|!|\(|\)|[A-Za-z_][\w.-]*", expression)
    if "".join(tokens) != re.sub(r"\s+", "", re.sub(r"'[^']*'", lambda m: m.group(0).replace(" ", ""), expression)):
        raise ValueError("this condition is outside the understood subset: %s" % expression)
    words = {"&&": "and", "||": "or", "!": "not"}
    python = " ".join(words.get(t, t if t[0] in "'=!()" or t in ("always", "cancelled") else repr(values.get(t, ""))) for t in tokens)
    return bool(eval(python, {"__builtins__": {}}, {"always": lambda: True, "cancelled": lambda: cancelled}))


def ancestors(jobs, name):
    found = set()
    for need in jobs[name]["needs"]:
        found |= {need} | ancestors(jobs, need)
    return found


def aggregate(outcomes):
    """A matrix job's result: anything but success in it, and the order among those is not something to depend on."""
    bad = [o for o in outcomes if o != "success"]
    return bad[0] if bad else "success"


def simulate(builds=(), accepts=(), source="success", run_cancelled=False, event="push", ref="refs/heads/main", publish_planned="true", text=None):
    """Walk the real conditions over per-target outcomes and return {job: result}.

    `continue-on-error` is modelled as GitHub applies it: it forgives a job that ran and failed. A job cancelled before it
    got a runner has no step for it to forgive, which is what run 41418779022 showed. A target with no archive fails its
    acceptance at the artifact download unless the scenario says the acceptance was cancelled.
    """
    builds, accepts = dict(builds), dict(accepts)
    jobs = workflow_jobs(text or WORKFLOW.read_text())
    results = {}
    while len(results) < len(jobs):
        for name, job in jobs.items():
            if name in results or not set(job["needs"]) <= set(results):
                continue
            values = {"github.ref": ref, "github.event_name": event}
            for need in job["needs"]:
                values["needs.%s.result" % need] = results[need]
                if need == "plan":
                    values["needs.plan.outputs.publish"] = publish_planned
            status_free = job["if"] is None or not re.search(r"\b(always|cancelled|failure|success)\(", job["if"])
            implicit = all(results[a] == "success" for a in ancestors(jobs, name)) and not run_cancelled
            started_before_any_cancel = name == "plan" or job["needs"] == ["plan"]
            wanted = (implicit if status_free else True) and (job["if"] is None or evaluate(job["if"], values, run_cancelled))
            if not wanted and not started_before_any_cancel:
                results[name] = "skipped"
            elif job["targets"]:
                is_build = name.startswith("build")
                outcomes = []
                for platform in job["targets"]:
                    built = builds.get(platform, "success") == "success"
                    outcome = builds.get(platform, "success") if is_build else accepts.get(platform, "success" if built else "failure")
                    forgiven = job["tolerated"] == "true" or (job["tolerated"] == "${{ matrix.candidate }}" and platform in job["candidates"])
                    outcomes.append("success" if forgiven and outcome == "failure" else outcome)
                results[name] = aggregate(outcomes)
            else:
                results[name] = source if name == "source" else "success"
    return results


class WorkflowGraphTests(unittest.TestCase):
    """The conditions the workflow really has, walked over the outcomes GitHub can report."""

    def assertRan(self, results, *names):
        for name in names:
            self.assertNotEqual(results.get(name, "skipped"), "skipped", "%s did not run: %s" % (name, results))

    def assertSkipped(self, results, *names):
        for name in names:
            self.assertEqual(results.get(name, "skipped"), "skipped", "%s ran: %s" % (name, results))

    def test_every_target_passing_publishes(self):
        results = simulate()
        self.assertEqual(results["accept"], "success")
        self.assertRan(results, "accept", "accept-candidate", "publish")

    def test_an_optional_build_that_never_got_a_runner_does_not_stop_the_required_targets(self):
        for platform in OPTIONAL_PLATFORMS:
            for outcome in ("cancelled", "failure"):
                with self.subTest(platform=platform, outcome=outcome):
                    results = simulate(builds={platform: outcome})
                    self.assertRan(results, "publish")
                    self.assertEqual(results["accept"], "success")
                    self.assertRan(results, "accept-candidate")

    def test_every_optional_target_unavailable_still_accepts_and_publishes_the_required_ones(self):
        results = simulate(builds={p: "cancelled" for p in OPTIONAL_PLATFORMS})
        self.assertEqual(results["accept"], "success")
        self.assertRan(results, "publish")

    def test_an_optional_acceptance_that_failed_or_lost_its_runner_does_not_stop_publication(self):
        for outcome in ("cancelled", "failure"):
            with self.subTest(outcome=outcome):
                self.assertRan(simulate(accepts={OPTIONAL_PLATFORMS[-1]: outcome}), "publish")

    def test_a_required_target_that_failed_was_cancelled_or_did_not_run_blocks_publication(self):
        for platform in REQUIRED_PLATFORMS:
            for outcome in ("failure", "cancelled", "skipped"):
                with self.subTest(stage="build", platform=platform, outcome=outcome):
                    self.assertSkipped(simulate(builds={platform: outcome}), "publish")
                with self.subTest(stage="accept", platform=platform, outcome=outcome):
                    self.assertSkipped(simulate(accepts={platform: outcome}), "publish")

    def test_the_source_archive_is_required(self):
        for outcome in ("failure", "cancelled"):
            with self.subTest(outcome=outcome):
                self.assertSkipped(simulate(source=outcome), "publish")

    def test_cancelling_the_workflow_stops_everything_after_it(self):
        # the operator's case the optional handling must not swallow: required done, a macOS job still in flight
        for builds in ({}, {OPTIONAL_PLATFORMS[-1]: "cancelled"}, {p: "cancelled" for p in PLATFORMS}):
            with self.subTest(builds=builds):
                self.assertSkipped(simulate(builds=builds, run_cancelled=True), "accept", "accept-candidate", "publish")

    def test_validation_runs_acceptance_and_never_publishes(self):
        for kwargs in ({"event": "pull_request", "ref": "refs/pull/1/merge", "publish_planned": "false"},
                       {"event": "workflow_dispatch", "publish_planned": "false"},
                       {"event": "workflow_dispatch", "ref": "refs/heads/other", "publish_planned": "true"}):
            with self.subTest(kwargs=kwargs):
                results = simulate(**kwargs)
                self.assertRan(results, "accept", "accept-candidate")
                self.assertSkipped(results, "publish")

    def test_required_and_optional_targets_are_separate_chains(self):
        jobs = workflow_jobs(WORKFLOW.read_text())
        self.assertEqual(tuple(jobs["build"]["targets"]), REQUIRED_PLATFORMS)
        self.assertEqual(tuple(jobs["accept"]["targets"]), REQUIRED_PLATFORMS)
        self.assertEqual(tuple(jobs["build-candidate"]["targets"]), OPTIONAL_PLATFORMS)
        self.assertEqual(tuple(jobs["accept-candidate"]["targets"]), OPTIONAL_PLATFORMS)
        # a required target is never forgiven, and nothing optional is an ancestor of its acceptance, so no status
        # function is involved in reaching it: GitHub's default is the whole condition
        for name in ("build", "accept"):
            self.assertIsNone(jobs[name]["tolerated"])
            self.assertIsNone(jobs[name]["if"])
        self.assertEqual(ancestors(jobs, "accept"), {"plan", "build"})
        for name in ("build-candidate", "accept-candidate"):
            self.assertEqual(jobs[name]["tolerated"], "true")

    def test_required_and_optional_jobs_run_the_same_steps(self):
        text = WORKFLOW.read_text()
        self.assertEqual(len(re.findall(r"(?m)^    steps: &build-steps$", text)), 1)
        self.assertEqual(len(re.findall(r"(?m)^    steps: \*build-steps$", text)), 1)
        self.assertEqual(len(re.findall(r"(?m)^    steps: &accept-steps$", text)), 1)
        self.assertEqual(len(re.findall(r"(?m)^    steps: \*accept-steps$", text)), 1)

    def test_nothing_runs_past_a_cancelled_workflow_and_publication_names_its_gates(self):
        jobs = workflow_jobs(WORKFLOW.read_text())
        self.assertFalse([n for n, j in jobs.items() if j["if"] and "always()" in j["if"]], "a job-level always() outlives a cancelled run")
        for name in ("accept-candidate", "publish"):
            self.assertTrue(jobs[name]["if"].startswith("!cancelled() && "), name)
        for gate in ("needs.accept.result == 'success'", "needs.source.result == 'success'",
                     "needs.plan.outputs.publish == 'true'", "github.ref == 'refs/heads/main'", "github.event_name != 'pull_request'"):
            self.assertIn(gate, jobs["publish"]["if"])
        # publication reads the optional targets' reports, so it has to wait for them, and writes only from its own job
        self.assertEqual(set(jobs["publish"]["needs"]), {"plan", "source", "accept", "accept-candidate"})
        self.assertEqual([n for n, j in jobs.items() if "contents: write" in j["text"]], ["publish"])

    def test_automatic_ci_stays_light(self):
        text = WORKFLOW.read_text()
        self.assertEqual(re.findall(r"(?m)^  (\w+):", text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]), ["push", "pull_request", "workflow_dispatch"])
        self.assertEqual(len(re.findall(r"--test-mode smoke", text)), 1)
        self.assertNotRegex(text, r"--test-mode (?!smoke)|--asan|--mark-rebuilt|schedule:|  asan:")
        self.assertRegex(re.search(r"accept\.py .*", text).group(0), r"--smoke$")


class PublicationTests(unittest.TestCase):
    """publish.py over real files laid out as download-artifact leaves them, in its dry-run decision path."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.artifacts = self.root / "artifacts"
        self.plan = {
            "commit": "c" * 40, "version": "1.9", "stamp": "2.0+auto-pigeon.1.9", "tag": "auto-pigeon-ericw-v1.9", "publish": True,
            "assets": {p: "tools-1.9-%s.zip" % p for p in PLATFORMS}, "source_asset": "tools-1.9-source.tar.gz",
            "upstream_repository": "https://example.invalid/upstream", "fork_repository": "https://example.invalid/fork",
            "upstream_describe": "2.0", "platforms": list(PLATFORMS), "required_platforms": list(REQUIRED_PLATFORMS),
        }
        self.write("plan/plan.json", self.plan)
        source = self.artifacts / "source" / self.plan["source_asset"]
        source.parent.mkdir(parents=True)
        source.write_bytes(b"source")
        self.write("source/tools-1.9-source.source.json", {"commit": self.plan["commit"], "sha256": sha256_file(source), "files": 1})
        for platform in PLATFORMS:
            self.candidate(platform)
            self.accepted(platform)

    def write(self, relative, data):
        path = self.artifacts / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def candidate(self, platform, /, **changes):
        archive = self.artifacts / ("candidate-" + platform) / self.plan["assets"][platform]
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(("archive for " + platform).encode())
        package = dict(smoke_report(), commit=self.plan["commit"], version=self.plan["version"], stamp=self.plan["stamp"],
                       sha256=sha256_file(archive), releasable=True)
        package.update(changes)
        self.write("candidate-%s/%s.package.json" % (platform, self.plan["assets"][platform][:-len(".zip")]), package)

    def accepted(self, platform, /, **changes):
        archive = self.artifacts / ("candidate-" + platform) / self.plan["assets"][platform]
        report = {"commit": self.plan["commit"], "stamp": self.plan["stamp"], "platform": platform, "verdict": "pass",
                  "archive_sha256": sha256_file(archive), "acceptance_mode": "smoke", "commands": [],
                  "steps": [{"name": str(i), "status": "pass"} for i in range(SMOKE_ACCEPTANCE_STEPS)]}
        report.update(changes)
        self.write("acceptance-%s/acceptance-%s.json" % (platform, platform), report)

    def remove(self, artifact):
        for path in sorted((self.artifacts / artifact).iterdir()):
            path.unlink()
        (self.artifacts / artifact).rmdir()

    def decide(self):
        out = self.root / "release"
        argv = ["publish.py", "--plan", str(self.artifacts / "plan" / "plan.json"), "--artifacts", str(self.artifacts),
                "--repo", "owner/name", "--run-url", "https://example.invalid/run", "--out", str(out), "--dry-run"]
        with patch("sys.argv", argv), patch("builtins.print"):
            publish.main()
        manifest = json.loads((out / "release-manifest.json").read_text())
        return manifest, (out / "release-notes.md").read_text(), (out / "SHA256SUMS").read_text()

    def test_every_accepted_target_is_published(self):
        manifest, _, sums = self.decide()
        self.assertEqual({p: t["status"] for p, t in manifest["targets"].items()}, {p: "published" for p in PLATFORMS})
        for platform in PLATFORMS:
            self.assertIn(self.plan["assets"][platform], sums)

    def assertOmitted(self, platform, because):
        manifest, notes, sums = self.decide()
        target = manifest["targets"][platform]
        self.assertEqual(target["status"], "not_published")
        self.assertIn(because, target["reason"])
        self.assertNotIn("asset", target)
        self.assertNotIn(self.plan["assets"][platform], [a["name"] for a in manifest["assets"]])
        self.assertNotIn(self.plan["assets"][platform], sums)
        self.assertRegex(notes, r"\| %s \| \*\*not published\*\* \|" % re.escape(publish.LABELS[platform]))
        for other in REQUIRED_PLATFORMS:
            self.assertEqual(manifest["targets"][other]["status"], "published")
        return manifest

    def test_an_optional_target_with_no_archive_is_named_and_omitted(self):
        # the run that prompted this: the job never got a runner, so no artifact exists and no report either
        self.remove("candidate-macos-arm64")
        self.remove("acceptance-macos-arm64")
        manifest = self.assertOmitted("macos-arm64", "no archive of it reached this job")
        self.assertEqual(manifest["targets"]["macos-amd64"]["status"], "published", "the other optional target is still accepted")

    def test_an_optional_target_whose_acceptance_never_reported_is_named_and_omitted(self):
        self.remove("acceptance-macos-amd64")
        self.assertOmitted("macos-amd64", "produced no report")

    def test_an_optional_target_that_failed_acceptance_is_named_and_omitted(self):
        self.accepted("macos-arm64", verdict="fail", failure="Failure: the sealed room leaked")
        self.assertOmitted("macos-arm64", "the sealed room leaked")

    def test_a_required_target_with_missing_or_failed_evidence_blocks_publication(self):
        for platform in REQUIRED_PLATFORMS:
            for case in ("no archive", "no report", "failed"):
                with self.subTest(platform=platform, case=case):
                    self.candidate(platform)
                    self.accepted(platform)
                    if case == "no archive":
                        self.remove("candidate-" + platform)
                    elif case == "no report":
                        self.remove("acceptance-" + platform)
                    else:
                        self.accepted(platform, verdict="fail", failure="Failure: broken")
                    with self.assertRaisesRegex(Failure, "required target %s cannot be published" % platform):
                        self.decide()
                    self.candidate(platform)
                    self.accepted(platform)

    def test_identity_mismatch_refuses_the_release_for_required_and_optional_targets_alike(self):
        for platform in ("linux-amd64", "macos-arm64"):
            good = sha256_file(self.artifacts / ("candidate-" + platform) / self.plan["assets"][platform])
            wrong = "0" * 64
            for package_change, report_change in (
                ({"commit": "d" * 40}, {}), ({"version": "1.8"}, {}), ({"stamp": "other"}, {}), ({"sha256": wrong}, {}),
                ({}, {"commit": "d" * 40}), ({}, {"stamp": "other"}), ({}, {"archive_sha256": wrong}),
                ({}, {"platform": "windows-amd64" if platform != "windows-amd64" else "linux-amd64"}),
            ):
                with self.subTest(platform=platform, package=package_change, report=report_change):
                    self.candidate(platform, **package_change)
                    self.accepted(platform, **report_change)
                    with self.assertRaisesRegex(Failure, platform):
                        self.decide()
            self.candidate(platform)
            self.accepted(platform)
            self.assertEqual(sha256_file(self.artifacts / ("candidate-" + platform) / self.plan["assets"][platform]), good)

    def test_the_source_archive_is_required(self):
        self.remove("source")
        with self.assertRaisesRegex(Failure, "source archive is missing"):
            self.decide()


if __name__ == "__main__":
    unittest.main()
