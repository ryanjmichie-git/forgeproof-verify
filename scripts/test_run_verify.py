#!/usr/bin/env python3
"""Unit tests for scripts/run_verify.py — stdlib only, no pytest.

Each test drives the glue script through the exact env contract the
composite action uses (INPUT_* variables + GITHUB_OUTPUT/
GITHUB_STEP_SUMMARY temp files) against the real frozen fixtures in this
repository. Plain asserts; the process exits nonzero on any failure.

Run:  python scripts/test_run_verify.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUN_VERIFY = REPO / "scripts" / "run_verify.py"
VERIFIER = REPO / "verifier" / "forgeproof.py"

FIXTURES = {
    "v110": {"chain": "chain-998.json", "rpack": "issue-998.rpack",
             "artifact": "src/example2.py"},
    "v101": {"chain": "chain-999.json", "rpack": "issue-999.rpack",
             "artifact": "src/example.py"},
}

OUTPUT_KEYS = {"verified", "complete", "bundle-path", "report",
               "should-fail", "summary-bytes"}

COMMENT_MARKER = "<!-- forgeproof-verify -->"


def deploy(name: str, root: Path) -> None:
    """Deploy a fixture into the layout verify expects:
    <root>/.forgeproof/{chain,rpack} + <root>/src/<artifact>."""
    fx = REPO / "fixtures" / name
    spec = FIXTURES[name]
    (root / ".forgeproof").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    shutil.copy(fx / spec["chain"], root / ".forgeproof" / spec["chain"])
    shutil.copy(fx / spec["rpack"], root / ".forgeproof" / spec["rpack"])
    artifact = Path(spec["artifact"])
    shutil.copy(fx / artifact, root / artifact)


def parse_outputs(text: str) -> dict[str, str]:
    """Parse a GITHUB_OUTPUT file, including heredoc-delimited values."""
    outputs: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            key, delim = line.split("<<", 1)
            i += 1
            buf = []
            while i < len(lines) and lines[i] != delim:
                buf.append(lines[i])
                i += 1
            outputs[key] = "\n".join(buf)
        elif "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value
        i += 1
    return outputs


def run_glue(project_root, bundle=None, strict=None, require_bundle=None,
             verifier=None, workdir=None, extra_env=None):
    """Run the glue with the action's env contract. Returns
    (proc, outputs, summary)."""
    with tempfile.TemporaryDirectory() as td:
        out_file = Path(td) / "github_output"
        summary_file = Path(td) / "github_step_summary"
        out_file.touch()
        summary_file.touch()

        env = os.environ.copy()
        for key in ("INPUT_BUNDLE", "INPUT_STRICT", "INPUT_REQUIRE_BUNDLE",
                    "INPUT_PROJECT_ROOT", "VERIFIER", "GITHUB_OUTPUT",
                    "GITHUB_STEP_SUMMARY", "FP_TEST_FORCE_CRASH"):
            env.pop(key, None)
        env["INPUT_PROJECT_ROOT"] = str(project_root)
        env["VERIFIER"] = str(verifier if verifier is not None else VERIFIER)
        env["GITHUB_OUTPUT"] = str(out_file)
        env["GITHUB_STEP_SUMMARY"] = str(summary_file)
        if bundle is not None:
            env["INPUT_BUNDLE"] = bundle
        if strict is not None:
            env["INPUT_STRICT"] = strict
        if require_bundle is not None:
            env["INPUT_REQUIRE_BUNDLE"] = require_bundle
        if extra_env:
            env.update(extra_env)

        proc = subprocess.run(
            [sys.executable, str(RUN_VERIFY)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, cwd=workdir or REPO)
        outputs = parse_outputs(out_file.read_text(encoding="utf-8"))
        summary = summary_file.read_text(encoding="utf-8")
    assert proc.returncode == 0, (
        f"glue must always exit 0, got {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
    return proc, outputs, summary


def assert_all_outputs(outputs: dict[str, str]) -> None:
    missing = OUTPUT_KEYS - set(outputs)
    assert not missing, f"missing outputs: {missing} (got {set(outputs)})"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_green_v110():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        deploy("v110", root)
        proc, outputs, summary = run_glue(root)
        assert_all_outputs(outputs)
        assert outputs["verified"] == "true", outputs
        assert outputs["complete"] == "true", outputs
        assert outputs["should-fail"] == "false", outputs
        assert outputs["bundle-path"] == ".forgeproof/issue-998.rpack", outputs
        assert "VERIFIED" in outputs["report"], outputs["report"][:400]
        assert outputs["report"].rstrip().endswith(COMMENT_MARKER), \
            outputs["report"][-120:]
        assert int(outputs["summary-bytes"]) > 0, outputs
        assert int(outputs["summary-bytes"]) == len(
            summary.encode("utf-8")), outputs
        assert "VERIFIED" in summary
        assert "PASS" in proc.stdout
        assert "summary written:" in proc.stdout


def test_green_v101():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        deploy("v101", root)
        _, outputs, _ = run_glue(root)
        assert outputs["verified"] == "true", outputs
        assert outputs["complete"] == "true", outputs
        assert outputs["bundle-path"] == ".forgeproof/issue-999.rpack", outputs


def test_strict_incomplete():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        deploy("v110", root)
        (root / "src" / "example2.py").unlink()

        _, outputs, _ = run_glue(root, strict="true")
        assert outputs["verified"] == "false", outputs
        assert outputs["complete"] == "false", outputs
        assert outputs["should-fail"] == "true", outputs

        _, outputs, _ = run_glue(root, strict="false")
        assert outputs["verified"] == "true", outputs
        assert outputs["complete"] == "false", outputs
        assert outputs["should-fail"] == "false", outputs


def test_tampered():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        deploy("v110", root)
        artifact = root / "src" / "example2.py"
        data = bytearray(artifact.read_bytes())
        data[0] ^= 0xFF
        artifact.write_bytes(bytes(data))

        _, outputs, summary = run_glue(root)
        assert outputs["verified"] == "false", outputs
        assert outputs["should-fail"] == "true", outputs
        assert "TAMPER" in outputs["report"], outputs["report"][:400]
        assert "TAMPER" in summary


def test_no_bundle_required():
    with tempfile.TemporaryDirectory() as td:
        _, outputs, summary = run_glue(Path(td), require_bundle="true")
        assert_all_outputs(outputs)
        assert outputs["verified"] == "false", outputs
        assert outputs["should-fail"] == "true", outputs
        assert outputs["bundle-path"] == "", outputs
        assert "forgeproof-plugin" in outputs["report"], outputs["report"]
        assert "/forgeproof:run" in outputs["report"], outputs["report"]
        assert "NO PROVENANCE BUNDLE FOUND" in summary


def test_no_bundle_not_required():
    with tempfile.TemporaryDirectory() as td:
        _, outputs, _ = run_glue(Path(td), require_bundle="false")
        assert outputs["should-fail"] == "false", outputs
        assert outputs["verified"] == "true", outputs
        assert outputs["complete"] == "false", outputs


def test_multiple_bundles_green():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        deploy("v110", root)
        deploy("v101", root)
        _, outputs, _ = run_glue(root)
        assert outputs["verified"] == "true", outputs
        assert outputs["complete"] == "true", outputs
        assert outputs["should-fail"] == "false", outputs
        # sorted order: issue-998 before issue-999
        assert outputs["bundle-path"] == ".forgeproof/issue-998.rpack", outputs
        assert "Bundle file:" in outputs["report"], outputs["report"][:400]
        assert outputs["report"].count("issue #") >= 2, outputs["report"]


def test_multiple_bundles_one_tampered():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        deploy("v110", root)
        deploy("v101", root)
        artifact = root / "src" / "example.py"  # v101's artifact
        data = bytearray(artifact.read_bytes())
        data[0] ^= 0xFF
        artifact.write_bytes(bytes(data))

        _, outputs, _ = run_glue(root)
        assert outputs["verified"] == "false", outputs
        assert outputs["should-fail"] == "true", outputs
        assert "TAMPER" in outputs["report"], outputs["report"][:600]


def test_path_with_spaces():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "project root with spaces"
        deploy("v110", root)
        _, outputs, _ = run_glue(root)
        assert outputs["verified"] == "true", outputs
        assert outputs["complete"] == "true", outputs
        assert outputs["should-fail"] == "false", outputs


def test_engine_error_unparseable():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        deploy("v110", root)
        broken = Path(td) / "broken_engine.py"
        broken.write_text(
            "import sys\n"
            "sys.stderr.write('boom: engine exploded')\n"
            "sys.stdout.write('this is not json')\n"
            "sys.exit(2)\n",
            encoding="utf-8")

        _, outputs, summary = run_glue(root, verifier=broken)
        assert_all_outputs(outputs)
        assert outputs["verified"] == "false", outputs
        assert outputs["should-fail"] == "true", outputs
        assert "boom: engine exploded" in outputs["report"], outputs["report"]
        assert "VERIFICATION ERROR" in summary


def test_empty_string_strict_defaults_true():
    # The action always sets INPUT_*, so "" (e.g. an unset ${{ vars.X }})
    # must behave as the default (strict=true), never fail open.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        deploy("v110", root)
        (root / "src" / "example2.py").unlink()  # strict-only failure
        _, outputs, _ = run_glue(root, strict="")
        assert outputs["verified"] == "false", outputs
        assert outputs["should-fail"] == "true", outputs


def test_empty_string_require_bundle_defaults_true():
    with tempfile.TemporaryDirectory() as td:
        _, outputs, _ = run_glue(Path(td), require_bundle="")
        assert outputs["verified"] == "false", outputs
        assert outputs["should-fail"] == "true", outputs


def test_glue_crash_fails_closed():
    # FP_TEST_FORCE_CRASH=1 makes main() raise after input parsing; the
    # top-level handler must still ship a report and should-fail=true,
    # and the process must still exit 0 (run_glue asserts that).
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        deploy("v110", root)
        _, outputs, summary = run_glue(
            root, extra_env={"FP_TEST_FORCE_CRASH": "1"})
        assert_all_outputs(outputs)
        assert outputs["verified"] == "false", outputs
        assert outputs["complete"] == "false", outputs
        assert outputs["should-fail"] == "true", outputs
        assert "INTERNAL ERROR" in outputs["report"], outputs["report"][:400]
        assert "RuntimeError" in outputs["report"], outputs["report"][:400]
        assert "INTERNAL ERROR" in summary
        assert outputs["report"].rstrip().endswith(COMMENT_MARKER), \
            outputs["report"][-120:]


TESTS = [
    test_green_v110,
    test_green_v101,
    test_strict_incomplete,
    test_tampered,
    test_no_bundle_required,
    test_no_bundle_not_required,
    test_multiple_bundles_green,
    test_multiple_bundles_one_tampered,
    test_path_with_spaces,
    test_engine_error_unparseable,
    test_empty_string_strict_defaults_true,
    test_empty_string_require_bundle_defaults_true,
    test_glue_crash_fails_closed,
]


def main() -> int:
    failures = 0
    for test in TESTS:
        name = test.__name__
        try:
            test()
        except Exception:
            failures += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        else:
            print(f"ok    {name}")
    total = len(TESTS)
    print(f"\n{total - failures}/{total} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
