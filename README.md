# 🛡️ TerraSentinel

**AI-powered Infrastructure-as-Code pull-request reviewer.** TerraSentinel reviews
Terraform changes on every pull request and posts a single, self-updating comment
that explains the **security risks**, estimates the **cost impact**, and gives the
**exact fix** — grounded by a real static-analysis scanner so it cites genuine
issues instead of hallucinating.

> Static scanners tell you *what* rule failed. A human reviewer tells you *why it
> matters here*, *what the blast radius is*, *what it costs*, and *how to fix it*.
> TerraSentinel does the second part automatically, on top of the first.

---

## The problem it solves

Misconfigured infrastructure — a world-readable S3 bucket, an SSH port open to
`0.0.0.0/0`, an oversized RDS instance — is a leading cause of cloud breaches and
surprise bills. Tools like `checkov` and `tfsec` catch the rule violations, but
they dump terse rule IDs that reviewers skim past, and they say nothing about cost
or business impact. Pull requests get rubber-stamped, and the misconfiguration
ships.

TerraSentinel closes that gap by pairing a deterministic scanner with an LLM that
reasons about the *specific change*.

## How it works

```
   Developer opens a PR that changes  *.tf  files
                    │
                    ▼
        ┌────────────────────────┐
        │     GitHub Action       │   triggers on pull_request
        └───────────┬────────────┘
                    │
        ┌───────────▼─────────────────────────────────┐
        │ 1. Collect the Terraform diff (git)          │
        │ 2. Run static analysis (checkov / tfsec)     │  ← deterministic findings
        └───────────┬─────────────────────────────────┘
                    │  diff + file contents + findings  →  prompt
        ┌───────────▼────────────┐
        │   Claude (Opus 4.8)     │   explains · prioritizes · estimates cost · fixes
        └───────────┬────────────┘
                    │  validated JSON  →  Review
        ┌───────────▼────────────┐
        │  Post/update PR comment │   summary · risk score · findings · suggested fixes
        └─────────────────────────┘
```

**The grounding step is the point.** The scanner's findings are passed to the model
as ground truth. The model is instructed to build on them, may add issues it can
justify from the code, and must not invent resources or line numbers. That keeps
the AI accurate — the usual failure mode of "AI code review" tools.

## Features

- 🔎 **Static-analysis grounding** — auto-detects `checkov` or `tfsec`; degrades to
  AI-only if neither is present.
- 🧠 **Plain-English review** — every finding gets a *why it matters*, *blast
  radius*, *recommendation*, and a copy-pasteable **suggested fix**.
- 💰 **Cost awareness (FinOps)** — flags changes that move cloud spend and estimates
  the direction/magnitude.
- 📊 **Risk score + verdict** — a 0–100 score and `approve` / `comment` /
  `request_changes`.
- 🚦 **CI gate** — exits non-zero when findings reach a severity you choose
  (`--fail-on`), so risky PRs can be blocked.
- 💬 **One self-updating PR comment** — re-runs edit the same comment instead of
  spamming the thread.
- 🖥️ **Great local UX** — rich terminal output, plus `--scan-only` to run with no
  API key and no cost.
- 🔁 **Model-configurable** — Opus 4.8 by default; drop to Sonnet/Haiku for scale.

## Quick start (local)

```bash
# 1. Install (Python 3.10+)
pip install -e .
pip install checkov           # optional but recommended (the grounding scanner)

# 2. Try it with NO API key — static analysis only
terrasentinel examples/insecure --scan-only      # the insecure module: many findings
terrasentinel examples/secure   --scan-only      # the hardened version, for comparison

# 3. Full AI review: add your key, then run
cp .env.example .env          # then paste your key from console.anthropic.com
terrasentinel examples/insecure
```

> On bleeding-edge Python where `pip install checkov` fails to build, install it
> separately with `pipx install checkov`, run TerraSentinel without it (AI-only),
> or rely on the GitHub Action (which pins Python 3.12).

## Usage

```bash
terrasentinel [PATH] [options]

# Review a directory or a single file
terrasentinel ./infra
terrasentinel ./infra/main.tf

# Review only what changed in this PR/branch (uses git)
terrasentinel --diff --base origin/main

# Static analysis only — no API key, no cost
terrasentinel ./infra --scan-only

# Pick output + a CI gate
terrasentinel ./infra --format markdown --fail-on high
```

| Option | Description |
|---|---|
| `--diff` / `--base REF` | Review only `*.tf` files changed vs a base git ref |
| `--scan-only` | Run the scanner only; skip the AI (no key needed) |
| `--no-scan` | Skip the scanner; let the AI review the code directly |
| `--scanner checkov\|tfsec` | Force a specific scanner |
| `--model NAME` | Claude model id (default `claude-opus-4-8`) |
| `--format text\|markdown\|json` | Output format for stdout |
| `--output FILE` | Write the markdown report to a file |
| `--post-pr` | Post/update the review comment on the GitHub PR |
| `--fail-on critical\|high\|medium\|low\|none` | Exit non-zero at/above this severity (default `high`) |

**Exit codes:** `0` clean / below threshold · `2` findings at/above `--fail-on`
(use this to block a PR) · `1` error.

## Use it in CI (GitHub Action)

Add `ANTHROPIC_API_KEY` to your repo secrets, then drop this workflow in
`.github/workflows/terrasentinel.yml`:

```yaml
name: TerraSentinel review
on:
  pull_request:
    paths: ["**/*.tf"]
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: your-username/terrasentinel@v1
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          fail-on: high
          # scan-only: "true"   # run with no API key (static analysis only)
```

The action installs checkov, runs the review on the PR diff, and posts the comment.

## Configuration

Set via environment or a local `.env` (see [`.env.example`](.env.example)):

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Required for AI review (not for `--scan-only`) |
| `TERRASENTINEL_MODEL` | Override the model (`claude-sonnet-4-6`, `claude-haiku-4-5`, …) |
| `TERRASENTINEL_MAX_TOKENS` | Max output tokens (default 8000) |
| `GITHUB_TOKEN` / `GITHUB_REPOSITORY` / `GITHUB_PR_NUMBER` | For `--post-pr` (the Action sets these automatically) |

## Cost

A single PR review is a few thousand tokens — typically **a few cents on Opus 4.8**.
Switch to `claude-sonnet-4-6` or `claude-haiku-4-5` via `--model` /
`TERRASENTINEL_MODEL` if you run it at high volume.

## Development

```bash
pip install -e ".[dev]"
pytest -q          # unit tests (the AI layer is mocked — no API calls)
ruff check .       # lint
```

The test suite runs fully offline: the Claude client is mocked, so the whole
pipeline (scanner parsing → prompt build → JSON validation → gating → rendering)
is exercised without an API key.

## Architecture

| Module | Responsibility |
|---|---|
| `scanner.py` | Detect & run checkov/tfsec, normalize findings |
| `collect.py` | Gather `*.tf` files, contents, and git diffs |
| `reviewer.py` | Build the prompt, call Claude, validate the JSON review |
| `models.py` | Pydantic models (`StaticFinding`, `Finding`, `Review`) |
| `render.py` | Rich terminal output + GitHub markdown |
| `github.py` | Post/update the PR comment via the GitHub REST API |
| `cli.py` | Wire it together; modes, formats, and CI gating |

## Roadmap

- [ ] Precise cost numbers via [Infracost](https://www.infracost.io/) integration
- [ ] Inline PR review comments (line-anchored), in addition to the summary comment
- [ ] Support more IaC: CloudFormation, Kubernetes manifests, Helm
- [ ] Baseline/ignore file to suppress accepted findings
- [ ] SARIF output for GitHub code scanning

## License

MIT — see [LICENSE](LICENSE).
