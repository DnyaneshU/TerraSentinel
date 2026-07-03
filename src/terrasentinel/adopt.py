"""EXPERIMENTAL prototype — Adopt unmanaged AWS infrastructure into Terraform.

The gap (confirmed still open in 2026): resources created outside Terraform aren't
in state, so Terraform won't manage them. The native `terraform plan
-generate-config-out` — and tools like the now-deprecated Terraformer — emit
verbose HCL with absolute IDs and no references between resources: "a starting
point, not a finished product."

This prototype takes a description of discovered resources and uses Claude to
produce (1) clean, idiomatic Terraform and (2) matching `import` blocks, then runs
an OFFLINE consistency check that every import block targets a real resource block.
It never reads or writes cloud state — output is generate-only, meant for a
human-reviewed PR.

This is a proof-of-concept to judge feasibility before building the full feature
(live discovery via the AWS API, provider wiring, etc.).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from .config import Settings, load_settings
from .reviewer import ReviewError, _extract_json, _text_of

ADOPT_SYSTEM_PROMPT = """\
You are a senior platform engineer adopting EXISTING AWS infrastructure into
Terraform. You are given a list of already-provisioned resources (type, real
cloud id, and key attributes). Produce production-quality Terraform, not a raw
dump.

Requirements:
- Write clean, idiomatic HCL. Use references between resources (e.g.
  aws_s3_bucket.data.id) instead of hard-coded absolute IDs wherever a
  relationship exists.
- Give resources sensible, human-readable local names.
- Omit read-only / computed / default attributes that Terraform manages itself.
- Lift values that clearly belong in variables into a variables block.
- For EVERY resource, emit a matching Terraform import block (Terraform 1.5+):
      import {
        to = <resource_type>.<local_name>
        id = "<real cloud id>"
      }
  The `to` address MUST exactly match a resource block you generated.

Respond with ONLY a single JSON object (no prose, no markdown fences):
{"main_tf": "<clean resource/variable HCL>", "imports_tf": "<all import blocks>", "notes": "<short caveats a reviewer should know>"}
"""


@dataclass
class AdoptResult:
    main_tf: str
    imports_tf: str
    notes: str = ""


def generate_adoption(settings: Settings, resources: list[dict]) -> AdoptResult:
    """Use Claude to turn discovered resources into clean HCL + import blocks."""
    if not settings.has_api_key:
        raise ReviewError("No ANTHROPIC_API_KEY found - required to generate adoption config.")
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise ReviewError("The 'anthropic' package is not installed.") from e

    client = anthropic.Anthropic(api_key=settings.api_key)
    messages = [{"role": "user", "content": _build_adopt_prompt(resources)}]

    last_error: str | None = None
    for _ in range(2):
        if last_error:
            messages.append(
                {
                    "role": "user",
                    "content": f"Your previous response could not be parsed: {last_error}. "
                    "Respond again with ONLY the JSON object.",
                }
            )
        try:
            resp = client.messages.create(
                model=settings.model,
                max_tokens=settings.max_tokens,
                system=ADOPT_SYSTEM_PROMPT,
                messages=messages,
            )
        except anthropic.APIError as e:
            raise ReviewError(f"Claude API error: {e}") from e

        if resp.stop_reason == "refusal":
            raise ReviewError("Claude declined to generate adoption config (safety refusal).")

        text = _text_of(resp)
        try:
            data = json.loads(_extract_json(text))
            return AdoptResult(
                main_tf=str(data["main_tf"]),
                imports_tf=str(data["imports_tf"]),
                notes=str(data.get("notes", "")),
            )
        except (ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
            last_error = str(e)
            messages.append({"role": "assistant", "content": text})

    raise ReviewError(f"Could not get valid adoption config from Claude: {last_error}")


def check_import_consistency(result: AdoptResult) -> tuple[list[str], list[str]]:
    """Offline proof: every import block must target a resource that was generated.

    Returns (matched_addresses, unmatched_addresses).
    """
    resource_addrs = {
        f"{m.group(1)}.{m.group(2)}"
        for m in re.finditer(r'resource\s+"([^"]+)"\s+"([^"]+)"', result.main_tf)
    }
    import_addrs = [
        m.group(1) for m in re.finditer(r"to\s*=\s*([A-Za-z0-9_]+\.[A-Za-z0-9_]+)", result.imports_tf)
    ]
    matched = [a for a in import_addrs if a in resource_addrs]
    unmatched = [a for a in import_addrs if a not in resource_addrs]
    return matched, unmatched


def _build_adopt_prompt(resources: list[dict]) -> str:
    parts = [
        "Adopt these existing AWS resources into Terraform.",
        "",
        "## Discovered resources (JSON)",
        "```json",
        json.dumps(resources, indent=2),
        "```",
        "",
        'Return ONLY: {"main_tf": "...", "imports_tf": "...", "notes": "..."}',
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Prototype CLI:  terrasentinel-adopt <discovered.json> [--out DIR]
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="terrasentinel-adopt",
        description="EXPERIMENTAL: adopt existing AWS resources into clean Terraform + import blocks.",
    )
    parser.add_argument("input", help="Path to a JSON file describing discovered resources.")
    parser.add_argument("--out", default=".", help="Directory to write generated.tf / imports.tf.")
    parser.add_argument("--model", help="Claude model id (default: claude-opus-4-8).")
    args = parser.parse_args(argv)

    console = Console()
    err = Console(stderr=True)

    try:
        resources = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        err.print(f"[red]Could not read {args.input}:[/red] {e}")
        return 1
    if not isinstance(resources, list) or not resources:
        err.print("[red]Input must be a non-empty JSON array of resources.[/red]")
        return 1

    settings = load_settings(model_override=args.model)
    err.print(f"[dim]Adopting {len(resources)} resource(s) with {settings.model}…[/dim]")
    try:
        result = generate_adoption(settings, resources)
    except ReviewError as e:
        err.print(f"[red]Error:[/red] {e}")
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "generated.tf").write_text(result.main_tf, encoding="utf-8")
    (out / "imports.tf").write_text(result.imports_tf, encoding="utf-8")

    matched, unmatched = check_import_consistency(result)
    console.print(f"[green]Wrote[/green] {out / 'generated.tf'} and {out / 'imports.tf'}")
    console.print(
        f"[bold]Import consistency:[/bold] {len(matched)} import block(s) match a "
        f"generated resource"
        + (f"; [red]{len(unmatched)} do NOT: {', '.join(unmatched)}[/red]" if unmatched else ".")
    )
    if result.notes:
        console.print(f"[dim]Notes: {result.notes}[/dim]")
    console.print(
        "\n[yellow]Prototype - review the generated Terraform and run "
        "`terraform plan` before applying. Never commit import blocks blindly.[/yellow]"
    )
    return 0 if not unmatched else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
