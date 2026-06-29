# Team guardrails

Plain-English policies for this organisation. TerraSentinel enforces these on
every pull request, on top of the static scanner's rules. Write them however you
like — no DSL, no Rego, just sentences.

## Security
- Nothing may be reachable from the public internet (`0.0.0.0/0`) on admin ports — SSH (22) or RDP (3389).
- Databases must never be publicly accessible, and must be encrypted at rest.
- No passwords, secrets, or access keys may be hard-coded in Terraform — use variables or a secrets manager.
- IAM policies must follow least privilege: no wildcard `"*"` actions or resources.

## Cost (FinOps)
- A single pull request should not add more than ~$1,000/month of new cloud spend without a written justification.
- Right-size by default; flag oversized database or compute instance classes.

## Tagging & compliance
- Every billable resource must carry an `Environment` and an `Owner` tag.
