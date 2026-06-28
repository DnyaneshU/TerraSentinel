"""The AI layer: turn a diff + file contents + static findings into a Review.

Design choices for robustness:
  * Uses the always-available `messages.create` endpoint (works across SDK
    versions) rather than a newer helper, and asks for a strict JSON object.
  * Validates the JSON against the Pydantic `Review` model.
  * Retries once, feeding the validation error back, if the first parse fails.
  * Surfaces refusals and truncation explicitly instead of returning garbage.
"""

from __future__ import annotations

import json

from .config import Settings
from .models import Review, StaticFinding

SYSTEM_PROMPT = """\
You are a meticulous senior DevSecOps engineer reviewing a Terraform / \
Infrastructure-as-Code change. You combine three things a plain static scanner cannot:

1. JUDGEMENT — you decide how much each issue actually matters for THIS change,
   assigning a severity even when the scanner left it blank.
2. EXPLANATION — you explain, in plain English a busy developer can act on, why an
   issue matters and what its blast radius is (what breaks, who is exposed).
3. REMEDIATION — you give the concrete corrected Terraform, not vague advice.

You are also cost-aware (FinOps): call out changes that meaningfully raise or lower
cloud spend and estimate the direction and rough magnitude.

GROUNDING RULES — accuracy over volume:
- The static-analysis findings provided to you are factual. Build on them.
- You MAY add issues the scanner missed, but only ones you can justify directly
  from the provided code or diff. Never invent resources, attributes, or line
  numbers that are not present. If you are unsure, leave it out.
- If a static finding is a false positive in context, you may downgrade it to
  'info' and say why — but do not silently drop it.
- If nothing is wrong, return an empty findings list, verdict 'approve', and a
  low risk score. Do not manufacture problems.

Respond with ONLY a single JSON object (no prose, no markdown fences) matching:
{
  "summary": "2-4 sentence overview of the change and its risk",
  "risk_score": 0-100 integer (0 safe, 100 critical),
  "verdict": "approve" | "comment" | "request_changes",
  "cost_impact": "plain-English estimate of monthly cost delta, or 'negligible'",
  "findings": [
    {
      "title": "short title",
      "severity": "critical" | "high" | "medium" | "low" | "info",
      "category": "security | cost | reliability | best-practice | compliance",
      "file": "path/to/file.tf",
      "line": integer or null,
      "explanation": "why this matters for THIS change",
      "blast_radius": "what could go wrong / who is affected",
      "recommendation": "what to do",
      "suggested_fix": "corrected Terraform snippet or null",
      "related_check_id": "scanner check id if applicable, else null"
    }
  ]
}
"""


def review(
    settings: Settings,
    *,
    diff_text: str | None,
    file_contents: dict[str, str],
    static_findings: list[StaticFinding],
) -> Review:
    if not settings.has_api_key:
        raise ReviewError(
            "No ANTHROPIC_API_KEY found. Set it in your environment or .env, "
            "or run with --scan-only to skip the AI review."
        )

    try:
        import anthropic
    except ImportError as e:  # pragma: no cover - anthropic is a core dependency
        raise ReviewError("The 'anthropic' package is not installed.") from e

    client = anthropic.Anthropic(api_key=settings.api_key)
    user_prompt = _build_prompt(diff_text, file_contents, static_findings)
    messages = [{"role": "user", "content": user_prompt}]

    last_error: str | None = None
    for attempt in range(2):
        if last_error:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous response could not be parsed: {last_error}\n"
                        "Respond again with ONLY the JSON object."
                    ),
                }
            )
        try:
            resp = client.messages.create(
                model=settings.model,
                max_tokens=settings.max_tokens,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
        except anthropic.APIError as e:  # rate limit, auth, server, etc.
            raise ReviewError(f"Claude API error: {e}") from e

        if resp.stop_reason == "refusal":
            raise ReviewError("Claude declined to review this content (safety refusal).")

        text = _text_of(resp)
        if resp.stop_reason == "max_tokens":
            last_error = "response was truncated (increase TERRASENTINEL_MAX_TOKENS)"

        try:
            return _parse_review(text)
        except (ValueError, json.JSONDecodeError) as e:
            last_error = str(e)
            messages.append({"role": "assistant", "content": text})

    raise ReviewError(f"Could not get a valid review from Claude: {last_error}")


def _build_prompt(
    diff_text: str | None,
    file_contents: dict[str, str],
    static_findings: list[StaticFinding],
) -> str:
    parts: list[str] = []

    if static_findings:
        parts.append("## Static-analysis findings (ground truth)\n")
        for f in static_findings:
            sev = f.severity or "unrated"
            loc = f"{f.file}:{f.line}" if f.line else f.file
            parts.append(
                f"- [{f.check_id}] ({sev}) {f.title} — {loc}"
                + (f" — resource {f.resource}" if f.resource else "")
                + (f" — {f.guideline}" if f.guideline else "")
            )
        parts.append("")
    else:
        parts.append(
            "## Static-analysis findings\n"
            "(No external scanner findings were available. Review the code directly "
            "but stay grounded — only flag issues visible in the code below.)\n"
        )

    if diff_text and diff_text.strip():
        parts.append("## Diff being reviewed\n")
        parts.append("```diff")
        parts.append(diff_text.strip()[:30_000])
        parts.append("```")
        parts.append("")

    if file_contents:
        parts.append("## Full file contents for context\n")
        for path, content in file_contents.items():
            parts.append(f"### {path}")
            parts.append("```hcl")
            parts.append(content)
            parts.append("```")
            parts.append("")

    parts.append(
        "Produce the JSON review now. Prioritize the highest-impact issues. "
        "Keep explanations concrete and specific to this code."
    )
    return "\n".join(parts)


def _text_of(resp: object) -> str:
    blocks = getattr(resp, "content", []) or []
    texts = [getattr(b, "text", "") for b in blocks if getattr(b, "type", None) == "text"]
    return "\n".join(t for t in texts if t).strip()


def _parse_review(text: str) -> Review:
    payload = _extract_json(text)
    data = json.loads(payload)
    return Review.model_validate(data)


def _extract_json(text: str) -> str:
    """Pull a JSON object out of the model's reply, tolerating stray fences/prose."""
    t = text.strip()
    if t.startswith("```"):
        # drop the opening fence line and the trailing fence
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    t = t.strip()
    if t.startswith("{") and t.endswith("}"):
        return t
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start : end + 1]
    raise ValueError("no JSON object found in model response")


class ReviewError(RuntimeError):
    """Raised when the AI review cannot be produced."""
