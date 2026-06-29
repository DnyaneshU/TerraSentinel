"""Rendering: pretty terminal output (rich) and GitHub-flavored markdown."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .models import Finding, Review, StaticFinding, severity_rank

# Hidden marker so the GitHub integration can find and update its own comment.
COMMENT_MARKER = "<!-- terrasentinel:review -->"

_SEV_STYLE = {
    "critical": "bold white on red",
    "high": "bold red",
    "medium": "yellow",
    "low": "cyan",
    "info": "dim",
}
_SEV_EMOJI = {
    "critical": "🛑",
    "high": "🔴",
    "medium": "🟠",
    "low": "🔵",
    "info": "⚪",
}
_VERDICT_LABEL = {
    "approve": "✅ Approve",
    "comment": "💬 Comment",
    "request_changes": "⛔ Request changes",
}


# --------------------------------------------------------------------------- #
# Terminal (rich)
# --------------------------------------------------------------------------- #
def print_review(
    review: Review,
    scanner_name: str | None,
    console: Console | None = None,
    verification: object | None = None,
) -> None:
    console = console or Console()
    score = review.clamped_risk()
    header = (
        f"[bold]Risk score:[/bold] {_score_markup(score)}    "
        f"[bold]Verdict:[/bold] {_VERDICT_LABEL.get(review.verdict.value, review.verdict.value)}"
    )
    console.print(Panel(f"{review.summary}\n\n{header}", title="TerraSentinel review", expand=False))
    console.print(f"[bold]Cost impact:[/bold] {review.cost_impact}")
    if verification is not None:
        console.print(f"[bold]🔧 Verified fixes:[/bold] {_verification_summary(verification)}")

    if not review.findings:
        console.print("\n[green]No findings. Looks clean.[/green]")
        _print_footer(console, scanner_name)
        return

    table = Table(title=f"\nFindings ({len(review.findings)})", show_lines=True, expand=True)
    table.add_column("Sev", no_wrap=True)
    table.add_column("Title")
    table.add_column("Location", no_wrap=True)
    table.add_column("Fix", no_wrap=True)
    table.add_column("Why it matters")
    for f in _sorted(review.findings):
        sev = f.severity.value
        title = ("📜 " if f.guardrail else "") + f.title
        table.add_row(
            f"[{_SEV_STYLE.get(sev, '')}]{_SEV_EMOJI.get(sev, '')} {sev}[/]",
            title,
            _loc(f.file, f.line),
            _fix_badge_markup(f),
            f.explanation,
        )
    console.print(table)

    for f in _sorted(review.findings):
        if f.suggested_fix:
            verified = " [green](✓ verified)[/]" if f.fix_verified else ""
            console.print(
                Panel(
                    f.suggested_fix,
                    title=f"Suggested fix{verified} — {f.title} ({_loc(f.file, f.line)})",
                    border_style="green",
                    expand=False,
                )
            )
    _print_footer(console, scanner_name)


def print_static_findings(
    findings: list[StaticFinding], scanner_name: str | None, console: Console | None = None
) -> None:
    console = console or Console()
    if not findings:
        console.print("[green]No static-analysis findings.[/green]")
        _print_footer(console, scanner_name)
        return
    table = Table(title=f"Static findings ({len(findings)})", show_lines=True, expand=True)
    table.add_column("Sev", no_wrap=True)
    table.add_column("Check", no_wrap=True)
    table.add_column("Title")
    table.add_column("Location", no_wrap=True)
    for f in sorted(findings, key=lambda x: -severity_rank(x.severity)):
        sev = (f.severity or "unrated").lower()
        table.add_row(
            f"[{_SEV_STYLE.get(sev, 'dim')}]{sev}[/]",
            f.check_id,
            f.title,
            _loc(f.file, f.line),
        )
    console.print(table)
    _print_footer(console, scanner_name)


# --------------------------------------------------------------------------- #
# Markdown (GitHub)
# --------------------------------------------------------------------------- #
def review_to_markdown(
    review: Review, scanner_name: str | None, verification: object | None = None
) -> str:
    score = review.clamped_risk()
    lines = [
        COMMENT_MARKER,
        "## 🛡️ TerraSentinel review",
        "",
        f"**Verdict:** {_VERDICT_LABEL.get(review.verdict.value, review.verdict.value)} "
        f"&nbsp;·&nbsp; **Risk score:** {score}/100 {_score_bar(score)}",
        "",
        review.summary,
        "",
        f"**💰 Cost impact:** {review.cost_impact}",
    ]
    if verification is not None:
        lines.append("")
        lines.append(f"**🔧 Verified fixes:** {_verification_summary(verification)}")
    lines.append("")

    if not review.findings:
        lines.append("✅ **No findings.** This change looks clean.")
    else:
        lines.append(f"### Findings ({len(review.findings)})")
        lines.append("")
        lines.append("| Severity | Title | Location | Fix | Category |")
        lines.append("|---|---|---|---|---|")
        for f in _sorted(review.findings):
            sev = f.severity.value
            title = ("📜 " if f.guardrail else "") + _md(f.title)
            lines.append(
                f"| {_SEV_EMOJI.get(sev, '')} {sev} | {title} | "
                f"`{_loc(f.file, f.line)}` | {_fix_badge_md(f)} | {f.category} |"
            )
        lines.append("")
        for f in _sorted(review.findings):
            lines.extend(_finding_detail_md(f))

    lines.append("")
    lines.append(f"<sub>{_footer_text(scanner_name)}</sub>")
    return "\n".join(lines)


def static_findings_to_markdown(
    findings: list[StaticFinding], scanner_name: str | None
) -> str:
    lines = [COMMENT_MARKER, "## 🛡️ TerraSentinel — static scan", ""]
    if not findings:
        lines.append("✅ No static-analysis findings.")
    else:
        lines.append(f"Found **{len(findings)}** issue(s):")
        lines.append("")
        lines.append("| Severity | Check | Title | Location |")
        lines.append("|---|---|---|---|")
        for f in sorted(findings, key=lambda x: -severity_rank(x.severity)):
            sev = (f.severity or "unrated").lower()
            lines.append(
                f"| {_SEV_EMOJI.get(sev, '')} {sev} | `{f.check_id}` | "
                f"{_md(f.title)} | `{_loc(f.file, f.line)}` |"
            )
    lines.append("")
    lines.append(f"<sub>{_footer_text(scanner_name)}</sub>")
    return "\n".join(lines)


def _finding_detail_md(f: Finding) -> list[str]:
    sev = f.severity.value
    out = [
        f"<details><summary>{_SEV_EMOJI.get(sev, '')} <b>{_md(f.title)}</b> "
        f"({sev}, <code>{_loc(f.file, f.line)}</code>)</summary>",
        "",
        f"**Why it matters:** {f.explanation}",
        "",
        f"**Blast radius:** {f.blast_radius}",
        "",
        f"**Recommendation:** {f.recommendation}",
    ]
    if f.guardrail:
        out += ["", f"**📜 Violates guardrail:** {f.guardrail}"]
    if f.suggested_fix:
        label = "**Suggested fix** (✅ verified by re-scan):" if f.fix_verified else "**Suggested fix:**"
        out += ["", label, "", "```hcl", f.suggested_fix, "```"]
    if f.related_check_id:
        out += ["", f"<sub>scanner check: `{f.related_check_id}`</sub>"]
    out += ["", "</details>", ""]
    return out


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _sorted(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: -severity_rank(f.severity.value))


def _loc(file: str, line: int | None) -> str:
    return f"{file}:{line}" if line else file


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _score_markup(score: int) -> str:
    if score >= 70:
        return f"[bold red]{score}/100[/]"
    if score >= 40:
        return f"[yellow]{score}/100[/]"
    return f"[green]{score}/100[/]"


def _score_bar(score: int) -> str:
    filled = max(0, min(10, round(score / 10)))
    block = "🟥" if score >= 70 else "🟧" if score >= 40 else "🟩"
    return block * filled + "⬜" * (10 - filled)


def _footer_text(scanner_name: str | None) -> str:
    grounding = f"grounded by {scanner_name}" if scanner_name else "AI-only (no scanner found)"
    return f"Generated by TerraSentinel - {grounding}"


def _fix_badge_markup(f: Finding) -> str:
    if f.fix_verified is True:
        return "[green]✓ verified[/]"
    if f.fix_verified is False:
        return "[red]✗ unverified[/]"
    return "[dim]proposed[/]" if f.suggested_fix else ""


def _fix_badge_md(f: Finding) -> str:
    if f.fix_verified is True:
        return "✅ verified"
    if f.fix_verified is False:
        return "⚠️ unverified"
    return "proposed" if f.suggested_fix else "—"


def _verification_summary(v: object) -> str:
    return (
        f"resolved {v.resolved_count}/{v.before} scanner findings, "
        f"introduced {v.introduced_count} new (re-scanned)"
    )


def _print_footer(console: Console, scanner_name: str | None) -> None:
    console.print(f"\n[dim]{_footer_text(scanner_name)}[/dim]")
