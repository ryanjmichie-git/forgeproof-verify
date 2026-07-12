# forgeproof-verify

[![ci](https://github.com/ryanjmichie-git/forgeproof-verify/actions/workflows/ci.yml/badge.svg)](https://github.com/ryanjmichie-git/forgeproof-verify/actions/workflows/ci.yml)

GitHub Action that verifies [ForgeProof](https://github.com/ryanjmichie-git/forgeproof-plugin)
`.rpack` provenance bundles on pull requests.

ForgeProof seals AI-generated code into Ed25519-signed, SHA-256 hash-chained
provenance bundles committed under `.forgeproof/` on the PR branch. This
action re-verifies those bundles inside CI: the bundle's root digest and
signature, the sealed provenance chain (hash + block linkage), and the
SHA-256 of every recorded artifact in the checkout. It fails the check on
tampering, posts a human-readable audit report as a PR comment, and writes
the same report to the job summary.

## Usage

```yaml
name: forgeproof
on: pull_request

permissions:
  contents: read
  pull-requests: write   # only needed for the PR comment

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ryanjmichie-git/forgeproof-verify@v1
        # Supply-chain best practice: pin the full commit SHA instead of
        # the tag, e.g.
        #   uses: ryanjmichie-git/forgeproof-verify@<40-char commit SHA>  # v1.0.0
```

That is the whole integration for the default layout (bundles under
`.forgeproof/` at the repository root).

## Inputs

| Input | Default | Description |
|---|---|---|
| `bundle` | `.forgeproof/*.rpack` | Glob for bundles, relative to `project-root`. Every matched bundle must verify. |
| `strict` | `"true"` | Fail when provenance evidence (chain file or recorded artifacts) is missing from the checkout, not just tampered. See [Strict semantics](#strict-semantics-integrity-vs-completeness). |
| `require-bundle` | `"true"` | Fail when no bundle matches the glob. Set to `"false"` for repos where only some PRs carry provenance. |
| `comment` | `"true"` | Post the audit report as a PR comment on `pull_request` events. Comment failures never fail the check. |
| `project-root` | `"."` | Directory the bundle glob and recorded artifact paths anchor to. |
| `github-token` | `${{ github.token }}` | Token used to post the PR comment. Needs `pull-requests: write`; irrelevant when `comment` is `"false"`. |

## Outputs

| Output | Description |
|---|---|
| `verified` | `"true"` iff every matched bundle verified. See [the vacuous case](#the-vacuous-case) below. |
| `complete` | `"true"` iff every matched bundle was complete: provenance chain and all recorded artifacts present in the checkout. |
| `bundle-path` | First matched bundle, relative to `project-root`. Empty when none matched. |
| `report` | The full markdown audit report. Ends with a hidden `<!-- forgeproof-verify -->` marker (see [PR comments](#pr-comments)). |
| `should-fail` | `"true"` iff the enforce step fails the check: a bundle failed verification, or none matched while `require-bundle` is `"true"`. |
| `summary-bytes` | Bytes the verify step appended to the job summary (always > 0 inside GitHub Actions). |

> **Warning — treat `bundle-path` and `report` as attacker-influenced on
> fork PRs.** Bundle file names and bundle contents come from the PR
> branch. Never interpolate these outputs into a script with `${{ }}`
> (classic expression injection):
>
> ```yaml
> # UNSAFE — do not do this:
> #   run: echo "${{ steps.fp.outputs.report }}"
>
> # Safe — pass through env instead:
> - name: Use the report
>   env:
>     REPORT: ${{ steps.fp.outputs.report }}
>   run: printf '%s\n' "$REPORT"
> ```

### The vacuous case

With zero matched bundles and `require-bundle: "false"`, nothing was
verified: the action passes with `verified: "true"` (vacuously — there was
no bundle to fail) and `complete: "false"`. Gate on `complete` if your
workflow must distinguish "verified provenance" from "no provenance
present".

## Fork PRs

The red/green check works on fork PRs with no extra configuration: the
verify and enforce steps only need `contents: read`. The default token on a
fork PR is read-only, so the PR *comment* is skipped (with a workflow
notice) — the report is always available in the job summary regardless.
This action never uses `pull_request_target`.

## PR comments

The report comment is upserted, not spammed: every report ends with a
hidden `<!-- forgeproof-verify -->` HTML marker, and the comment step
looks for an existing PR comment containing that marker authored by the
token's identity (`github-actions[bot]` for the default token) and edits
it in place; only if none exists does it post a new comment. Comment
failures never fail the check.

## Strict semantics: integrity vs completeness

The verifier distinguishes two failure classes:

- **Integrity** — evidence that is present but wrong: root digest or
  signature mismatch, modified chain file, broken block linkage, artifact
  hash mismatch. This always fails verification, strict or not.
- **Completeness** — evidence that is missing: the chain file or a recorded
  artifact is not in the checkout (normal when verifying a bundle copied
  from another repository). With `strict: "true"` (the default, and the
  right setting for verifying a ForgeProof PR in its own repository)
  missing evidence fails the check. With `strict: "false"` it verifies
  what is present and reports `complete: "false"`.

See the [ForgeProof plugin](https://github.com/ryanjmichie-git/forgeproof-plugin)
for how bundles are produced and sealed.

## The vendored verifier

Verification runs on a vendored, stdlib-only copy of the ForgeProof engine
(`verifier/forgeproof.py`) — no dependencies are installed at action run
time. `verifier/UPSTREAM` records the upstream repo, ref, and SHA-256; the
`sync-check` CI job proves the vendored copy is byte-identical to upstream
at that ref on every push.

## License

[MIT](LICENSE)
