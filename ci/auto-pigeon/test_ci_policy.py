"""Fast behavioral checks for smoke qualification and publication guards."""
import copy
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import accept
import publish
from common import Failure, FOCUSED_TEST, validated_tests


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


if __name__ == "__main__":
    unittest.main()
