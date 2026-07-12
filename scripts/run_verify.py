#!/usr/bin/env python3
"""Glue between the forgeproof-verify composite action and the vendored
ForgeProof verifier engine.

Contract (stdlib only, always exits 0 — the enforce step reads the
``should-fail`` output):

Environment in:
    INPUT_BUNDLE          glob for .rpack bundles, relative to project root
                          (default: .forgeproof/*.rpack)
    INPUT_STRICT          "true"/"false" — pass --strict to the engine
    INPUT_REQUIRE_BUNDLE  "true"/"false" — zero matched bundles is a failure
    INPUT_PROJECT_ROOT    directory bundle paths anchor to (default: .)
    VERIFIER              path to the vendored engine (verifier/forgeproof.py)
    GITHUB_OUTPUT         step-outputs file (optional, for local runs)
    GITHUB_STEP_SUMMARY   job-summary file (optional, for local runs)

Outputs written to GITHUB_OUTPUT:
    verified      "true" iff every matched bundle verified
    complete      "true" iff every matched bundle was complete (chain +
                  all artifacts present)
    bundle-path   first matched bundle, relative to the project root
    report        aggregated markdown report (heredoc syntax)
    should-fail   "true" iff the enforce step must fail the job

The per-bundle markdown report comes from the engine itself
(``verify --format markdown``) — it is the single source of truth and is
never re-rendered here. The glue only renders the no-bundle notices and
the engine-crash fallback, where there is no engine report to reuse.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

PLUGIN_URL = "https://github.com/ryanjmichie-git/forgeproof-plugin"
DEFAULT_PATTERN = ".forgeproof/*.rpack"
STDERR_EXCERPT_LIMIT = 2000


def env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("true", "1", "yes")


def md_inline(value: object) -> str:
    """Neutralize caller-controlled text for inline markdown: the report
    lands in PR comments, so strip code-span/emphasis metacharacters and
    keep it to one line."""
    text = str(value)
    for ch in "`*_[]<>|":
        text = text.replace(ch, "")
    return " ".join(text.split()) or "(empty)"


def md_fence(text: str) -> str:
    """Wrap engine stderr in a fenced block that its content cannot close."""
    excerpt = text.strip()
    if len(excerpt) > STDERR_EXCERPT_LIMIT:
        excerpt = excerpt[:STDERR_EXCERPT_LIMIT] + "\n... (truncated)"
    fence = "````"
    while fence in excerpt:
        fence += "`"
    return f"{fence}text\n{excerpt}\n{fence}"


def find_bundles(root: Path, pattern: str) -> list[Path]:
    """Glob bundles under the project root. Deterministic order."""
    normalized = pattern.replace("\\", "/").lstrip("/")
    try:
        matches = sorted(p for p in root.glob(normalized) if p.is_file())
    except (ValueError, NotImplementedError):
        matches = []
    if not matches:
        # A literal path with glob-special characters ([ ] etc.) would not
        # glob-match itself; honor it when it names a real file.
        direct = root / normalized
        if direct.is_file():
            matches = [direct]
    return matches


def run_engine(verifier: str, bundle: Path, root: Path, strict: bool,
               fmt: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, verifier, "verify",
           "--rpack", str(bundle),
           "--project-root", str(root),
           "--format", fmt]
    if strict:
        cmd.append("--strict")
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def verify_bundle(verifier: str, bundle: Path, root: Path,
                  strict: bool) -> dict:
    """Run the engine twice (json for status, markdown for the report).

    Returns {verified, complete, report, log_line}. Fails closed: any
    engine crash or unparseable stdout counts as not verified.
    """
    rel = bundle_rel(bundle, root)
    proc = run_engine(verifier, bundle, root, strict, "json")
    data = None
    if proc.stdout.strip():
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        report = "\n".join([
            "## ❌ VERIFICATION ERROR",
            "",
            f"The verifier engine did not produce a parseable result for "
            f"`{md_inline(rel)}` (exit code {proc.returncode}).",
            "",
            "**Engine stderr:**",
            "",
            md_fence(proc.stderr or "(no stderr)"),
        ])
        return {"verified": False, "complete": False, "report": report,
                "log_line": f"{rel}: ENGINE ERROR (exit {proc.returncode})"}

    verified = data.get("verified") is True
    complete = data.get("complete") is True

    md_proc = run_engine(verifier, bundle, root, strict, "markdown")
    report = md_proc.stdout.strip()
    if not report:
        report = "\n".join([
            "## ❌ VERIFICATION ERROR",
            "",
            f"The verifier engine produced no markdown report for "
            f"`{md_inline(rel)}` (exit code {md_proc.returncode}).",
            "",
            "**Engine stderr:**",
            "",
            md_fence(md_proc.stderr or "(no stderr)"),
        ])
        verified = complete = False

    status = "VERIFIED" if verified else "FAILED"
    suffix = "complete" if complete else "incomplete"
    return {"verified": verified, "complete": complete, "report": report,
            "log_line": f"{rel}: {status} ({suffix})"}


def bundle_rel(bundle: Path, root: Path) -> str:
    try:
        return bundle.relative_to(root).as_posix()
    except ValueError:
        return bundle.as_posix()


def no_bundle_report(pattern: str, root: str, required: bool) -> str:
    where = f"`{md_inline(pattern)}` under `{md_inline(root)}`"
    if not required:
        return "\n".join([
            "## ⚠️ NO PROVENANCE BUNDLE FOUND (not required)",
            "",
            f"No `.rpack` bundle matched {where}, and `require-bundle` is "
            "`false`, so this check passes without verifying anything.",
            "",
            f"To add provenance to future PRs, see the "
            f"[ForgeProof plugin]({PLUGIN_URL}).",
        ])
    return "\n".join([
        "## ❌ NO PROVENANCE BUNDLE FOUND",
        "",
        f"No `.rpack` bundle matched {where}, and this check requires a "
        "ForgeProof provenance bundle on the PR branch.",
        "",
        "**How to produce a bundle**",
        "",
        f"1. Install the [ForgeProof plugin]({PLUGIN_URL}) in Claude Code.",
        "2. Run `/forgeproof:run <issue-number>` — it generates the code "
        "with a signed provenance chain and finishes with a seal commit "
        "that adds `.forgeproof/chain-<issue>.json` and "
        "`.forgeproof/issue-<issue>.rpack`.",
        "3. Push that seal commit to the PR branch (`/forgeproof:push` "
        "opens the PR with the bundle included).",
        "",
        "If this repository does not use ForgeProof, set the action input "
        '`require-bundle: "false"`.',
    ])


def write_outputs(outputs: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    lines = []
    for key, value in outputs.items():
        if "\n" in value or "\r" in value:
            delimiter = f"GHEOF_{uuid.uuid4().hex}"
            while delimiter in value:
                delimiter = f"GHEOF_{uuid.uuid4().hex}"
            lines.append(f"{key}<<{delimiter}")
            lines.append(value)
            lines.append(delimiter)
        else:
            lines.append(f"{key}={value}")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_summary(report: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(report + "\n")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    pattern = os.environ.get("INPUT_BUNDLE", "").strip() or DEFAULT_PATTERN
    strict = env_flag("INPUT_STRICT", "true")
    require_bundle = env_flag("INPUT_REQUIRE_BUNDLE", "true")
    root_input = os.environ.get("INPUT_PROJECT_ROOT", "").strip() or "."
    verifier = os.environ.get("VERIFIER", "")

    root = Path(root_input)
    print(f"forgeproof-verify: pattern={pattern!r} project-root={root_input!r} "
          f"strict={strict} require-bundle={require_bundle}")

    if not verifier or not Path(verifier).is_file():
        report = "\n".join([
            "## ❌ VERIFICATION ERROR",
            "",
            f"Vendored verifier engine not found at `{md_inline(verifier)}`. "
            "This is a bug in the action itself.",
        ])
        print("forgeproof-verify: FATAL: verifier engine not found")
        write_outputs({"verified": "false", "complete": "false",
                       "bundle-path": "", "should-fail": "true",
                       "report": report})
        write_summary(report)
        return 0

    bundles = find_bundles(root, pattern)

    if not bundles:
        report = no_bundle_report(pattern, root_input, require_bundle)
        should_fail = require_bundle
        print(f"forgeproof-verify: no bundle matched "
              f"({'FAIL' if should_fail else 'pass — not required'})")
        write_outputs({
            "verified": "false" if require_bundle else "true",
            "complete": "false",
            "bundle-path": "",
            "should-fail": "true" if should_fail else "false",
            "report": report,
        })
        write_summary(report)
        return 0

    print(f"forgeproof-verify: {len(bundles)} bundle(s) matched")
    results = [verify_bundle(verifier, b, root, strict) for b in bundles]
    for res in results:
        print(f"forgeproof-verify:   {res['log_line']}")

    all_verified = all(r["verified"] for r in results)
    all_complete = all(r["complete"] for r in results)

    if len(results) == 1:
        report = results[0]["report"]
    else:
        parts = [f"# ForgeProof verification — {len(results)} bundles, "
                 f"{'all verified' if all_verified else 'FAILURES'}"]
        for bundle, res in zip(bundles, results):
            parts.append(f"\n---\n\n**Bundle file:** "
                         f"`{md_inline(bundle_rel(bundle, root))}`\n")
            parts.append(res["report"])
        report = "\n".join(parts)

    print(f"forgeproof-verify: result "
          f"{'PASS' if all_verified else 'FAIL'} "
          f"({'complete' if all_complete else 'incomplete'})")

    write_outputs({
        "verified": "true" if all_verified else "false",
        "complete": "true" if all_complete else "false",
        "bundle-path": bundle_rel(bundles[0], root),
        "should-fail": "false" if all_verified else "true",
        "report": report,
    })
    write_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
