"""Command-line interface for TerraSentinel."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from . import __version__, render
from .collect import (
    GitError,
    find_tf_files,
    git_changed_tf_files,
    git_diff,
    load_guardrails,
    matches_changed,
    read_files,
    resolve_base_ref,
)
from .config import load_settings
from .models import Review, StaticFinding, severity_rank
from .reviewer import ReviewError, review
from .scanner import ScannerError, detect_scanner, run_scanner

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_GATE = 2  # findings/verdict at or above the --fail-on threshold

_FAIL_ON_CHOICES = ["critical", "high", "medium", "low", "none"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="terrasentinel",
        description="AI-powered Terraform/IaC pull-request reviewer.",
    )
    p.add_argument("path", nargs="?", default=".", help="File or directory to review (default: .)")
    p.add_argument("--version", action="version", version=f"terrasentinel {__version__}")

    mode = p.add_argument_group("what to review")
    mode.add_argument(
        "--diff",
        action="store_true",
        help="Review only *.tf files changed vs --base (uses git).",
    )
    mode.add_argument("--base", help="Base git ref for --diff (default: auto-detect origin/main).")

    behavior = p.add_argument_group("behavior")
    behavior.add_argument(
        "--scan-only",
        action="store_true",
        help="Run static analysis only; skip the AI review (no API key needed).",
    )
    behavior.add_argument(
        "--no-scan", action="store_true", help="Skip static analysis; AI reviews the code directly."
    )
    behavior.add_argument("--scanner", choices=["checkov", "tfsec"], help="Force a specific scanner.")
    behavior.add_argument(
        "--framework",
        action="append",
        metavar="NAME",
        help="IaC framework(s) to scan: terraform (default), kubernetes, cloudformation, "
        "serverless, … Repeatable or comma-separated.",
    )
    behavior.add_argument("--config", help="Path to a .terrasentinel.yml config file.")
    behavior.add_argument("--model", help="Claude model id (default: claude-opus-4-8).")
    behavior.add_argument(
        "--guardrails",
        help="Path to a plain-English guardrails file (default: auto-detect guardrails.md).",
    )

    fixes = p.add_argument_group("verified fixes")
    fixes.add_argument(
        "--verify-fixes",
        action="store_true",
        help="Generate fixes and re-scan to confirm they resolve findings (needs API key + scanner).",
    )
    fixes.add_argument(
        "--fix",
        action="store_true",
        help="Like --verify-fixes, but also write the corrected files to disk.",
    )

    out = p.add_argument_group("output")
    out.add_argument(
        "--format", choices=["text", "markdown", "json"], default="text", help="stdout format."
    )
    out.add_argument("--output", help="Write the markdown report to this file.")
    out.add_argument(
        "--post-pr", action="store_true", help="Post/update a comment on the GitHub PR (CI use)."
    )
    out.add_argument(
        "--fail-on",
        choices=_FAIL_ON_CHOICES,
        default=None,
        help="Exit non-zero if findings reach this severity (default: high, or config).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    err = Console(stderr=True)

    try:
        return _run(args, console, err)
    except (ScannerError, GitError, ReviewError) as e:
        err.print(f"[red]Error:[/red] {e}")
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover
        err.print("[yellow]Interrupted.[/yellow]")
        return EXIT_ERROR


def _run(args: argparse.Namespace, console: Console, err: Console) -> int:
    settings = load_settings(config_path=args.config, target=args.path, model_override=args.model)
    if settings.config_path:
        err.print(f"[dim]Using config {settings.config_path}.[/dim]")
    # Resolve precedence: explicit CLI flag > config/env > default.
    fail_on = args.fail_on or settings.fail_on
    frameworks = _resolve_frameworks(args.framework) or settings.frameworks
    ignore = set(settings.ignore)

    # 1. Determine targets + collect material.
    diff_text: str | None = None
    if args.diff:
        base = resolve_base_ref(args.base)
        changed = git_changed_tf_files(base)
        if not changed:
            console.print(f"[green]No Terraform changes vs {base}. Nothing to review.[/green]")
            return EXIT_OK
        existing = [c for c in changed if Path(c).exists()]
        file_contents = read_files([Path(c) for c in existing])
        diff_text = git_diff(base, files=changed)
        scan_targets = existing
        err.print(f"[dim]Reviewing {len(changed)} changed file(s) vs {base}.[/dim]")
    else:
        files = find_tf_files(args.path)
        if not files:
            err.print(f"[yellow]No .tf files found under {args.path}.[/yellow]")
            return EXIT_OK
        file_contents = read_files(files)
        scan_targets = [args.path]
        changed = None

    # 2. Static analysis (grounding), unless disabled.
    scanner_name: str | None = None
    static_findings: list[StaticFinding] = []
    if not args.no_scan:
        if args.scanner is None and detect_scanner() is None:
            err.print("[yellow]No scanner (checkov/tfsec) found — running AI-only.[/yellow]")
        else:
            for target in scan_targets:
                name, fs = run_scanner(target, args.scanner, frameworks)
                scanner_name = name or scanner_name
                static_findings.extend(fs)
            if changed is not None:
                static_findings = [f for f in static_findings if matches_changed(f.file, changed)]
            static_findings = _dedupe(static_findings)
            if ignore:  # suppress accepted findings (from config `ignore:`)
                static_findings = [f for f in static_findings if f.check_id not in ignore]

    # 3a. Scan-only mode: report static findings and gate.
    if args.scan_only:
        if args.format == "json":
            console.print_json(
                data=[f.model_dump() for f in static_findings]
            )
        else:
            render.print_static_findings(static_findings, scanner_name, console)
        markdown = render.static_findings_to_markdown(static_findings, scanner_name)
        _emit_side_outputs(args, markdown, err)
        return _gate_static(static_findings, fail_on)

    # 3b. Full AI review (with optional plain-English guardrails).
    gr_path, guardrails = load_guardrails(args.path, args.guardrails)
    if guardrails:
        err.print(f"[dim]Enforcing guardrails from {gr_path}.[/dim]")

    err.print(f"[dim]Calling {settings.model}…[/dim]")
    result = review(
        settings,
        diff_text=diff_text,
        file_contents=file_contents,
        static_findings=static_findings,
        guardrails=guardrails,
    )
    if ignore:  # apply suppressions to AI findings too
        result.findings = [f for f in result.findings if f.related_check_id not in ignore]

    # Verified fixes: propose corrected files, then re-scan to prove they work.
    verification = None
    if args.verify_fixes or args.fix:
        verification = _run_verification(
            settings, file_contents, static_findings, scanner_name, result, args, err
        )

    if args.format == "json":
        console.print_json(result.model_dump_json())
    elif args.format == "markdown":
        console.print(render.review_to_markdown(result, scanner_name, verification))
    else:
        render.print_review(result, scanner_name, console, verification)

    markdown = render.review_to_markdown(result, scanner_name, verification)
    _emit_side_outputs(args, markdown, err)
    return _gate_review(result, fail_on)


def _emit_side_outputs(args: argparse.Namespace, markdown: str, err: Console) -> None:
    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
        err.print(f"[dim]Wrote report to {args.output}[/dim]")
    if args.post_pr:
        from .github import GitHubError, post_or_update_comment, resolve_pr_context

        try:
            repo, pr, token = resolve_pr_context()
            url = post_or_update_comment(repo, pr, token, markdown)
            err.print(f"[green]Posted review to PR #{pr}:[/green] {url}")
        except GitHubError as e:
            err.print(f"[red]Could not post PR comment:[/red] {e}")


def _run_verification(
    settings,
    file_contents: dict[str, str],
    static_findings: list[StaticFinding],
    scanner_name: str | None,
    result: Review,
    args: argparse.Namespace,
    err: Console,
):
    """Propose fixes, re-scan to verify, mark findings, and optionally write fixes."""
    from .remediate import propose_fixes, verify_fixes

    if scanner_name is None or not static_findings:
        err.print("[yellow]Fix verification skipped: needs a scanner with findings.[/yellow]")
        return None

    err.print("[dim]Generating and verifying fixes…[/dim]")
    try:
        corrected = propose_fixes(settings, file_contents, static_findings)
        vr = verify_fixes(static_findings, file_contents, corrected, scanner_name)
    except ReviewError as e:
        err.print(f"[yellow]Fix verification skipped: {e}[/yellow]")
        return None

    # Mark only scanner-backed findings as verified/unverified; leave AI-only as proposed.
    for f in result.findings:
        if f.related_check_id:
            f.fix_verified = vr.resolves(f.related_check_id, f.file)

    if args.fix:
        written = _apply_fixes(corrected, file_contents, err)
        if written:
            err.print(f"[green]Wrote {len(written)} fixed file(s):[/green] {', '.join(written)}")

    return vr


def _apply_fixes(
    corrected: dict[str, str], file_contents: dict[str, str], err: Console
) -> list[str]:
    written: list[str] = []
    by_name = {Path(k).name: k for k in file_contents}
    for path, content in corrected.items():
        target = path if path in file_contents else by_name.get(Path(path).name)
        if target is None:
            err.print(f"[yellow]Skipping unknown fixed file: {path}[/yellow]")
            continue
        try:
            Path(target).write_text(content, encoding="utf-8")
            written.append(target)
        except OSError as e:
            err.print(f"[yellow]Could not write {target}: {e}[/yellow]")
    return written


def _resolve_frameworks(values: list[str] | None) -> list[str] | None:
    """Flatten repeated and comma-separated --framework values into a list."""
    if not values:
        return None
    out: list[str] = []
    for v in values:
        out.extend(part.strip() for part in v.split(",") if part.strip())
    return out or None


def _gate_static(findings: list[StaticFinding], fail_on: str) -> int:
    if fail_on == "none" or not findings:
        return EXIT_OK
    threshold = severity_rank(fail_on)
    # checkov Community Edition usually omits severity. A check it flagged is still a
    # real failure, so treat an unrated finding as 'high' — that way the default
    # (--fail-on high) blocks on real findings, while --fail-on critical still won't.
    for f in findings:
        rank = severity_rank(f.severity) if f.severity else severity_rank("high")
        if rank >= threshold:
            return EXIT_GATE
    return EXIT_OK


def _gate_review(result: Review, fail_on: str) -> int:
    if result.verdict.value == "request_changes":
        return EXIT_GATE
    if fail_on == "none":
        return EXIT_OK
    top = result.max_severity()
    if top and severity_rank(top.value) >= severity_rank(fail_on):
        return EXIT_GATE
    return EXIT_OK


def _dedupe(findings: list[StaticFinding]) -> list[StaticFinding]:
    seen: set[tuple[str, str, int | None]] = set()
    out: list[StaticFinding] = []
    for f in findings:
        key = (f.check_id, f.file, f.line)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
