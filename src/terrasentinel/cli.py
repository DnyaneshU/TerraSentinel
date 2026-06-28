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
    behavior.add_argument("--model", help="Claude model id (default: claude-opus-4-8).")

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
        default="high",
        help="Exit non-zero if findings reach this severity (default: high).",
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
    settings = load_settings(model_override=args.model)

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
                name, fs = run_scanner(target, args.scanner)
                scanner_name = name or scanner_name
                static_findings.extend(fs)
            if changed is not None:
                static_findings = [f for f in static_findings if matches_changed(f.file, changed)]
            static_findings = _dedupe(static_findings)

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
        return _gate_static(static_findings, args.fail_on)

    # 3b. Full AI review.
    err.print(f"[dim]Calling {settings.model}…[/dim]")
    result = review(
        settings,
        diff_text=diff_text,
        file_contents=file_contents,
        static_findings=static_findings,
    )

    if args.format == "json":
        console.print_json(result.model_dump_json())
    elif args.format == "markdown":
        console.print(render.review_to_markdown(result, scanner_name))
    else:
        render.print_review(result, scanner_name, console)

    markdown = render.review_to_markdown(result, scanner_name)
    _emit_side_outputs(args, markdown, err)
    return _gate_review(result, args.fail_on)


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
