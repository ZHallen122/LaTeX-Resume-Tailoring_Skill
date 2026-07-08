# changes.json — the change manifest

Every variant directory must contain a `changes.json` next to its `resume.tex`.
It is the contract that makes tailoring reviewable: `render_review.py` cross-checks
it against the actual file diff, and any edit not declared here is reported as an
**unexplained change** (exit code 3). An unexplained change means either the manifest
is incomplete or the edit had no justification and must be reverted.

## Top-level shape

```json
{
  "meta": {
    "variant": "strict",
    "label": "uber-swe1",
    "jd_title": "Software Engineer I — Uber, Applications Engineering",
    "jd_url": "https://jobs.uber.com/en/jobs/160028/",
    "source_resume": "/path/to/main.tex",
    "generated": "2026-07-08"
  },
  "jd_requirements": [
    { "id": "R1", "text": "1+ year with C++, Python, Java, Git", "priority": "required" },
    { "id": "R5", "text": "Distributed systems experience", "priority": "required" },
    { "id": "P1", "text": "Production reliability / monitoring", "priority": "preferred" }
  ],
  "changes": [ ... ],
  "keywords": [ ... ]
}
```

`meta.variant` names the tab in the HTML report (`strict`, `stretch`).
`jd_requirements` gives every requirement a short id (`R1`, `P2`, ...) so each
change can cite exactly what it serves.

## Change entries

One entry per edited content unit (bullet, skills line, header line):

```json
{
  "section": "PROFESSIONAL EXPERIENCE — TabbyML",
  "type": "rewrite",
  "before": "\\item Enhanced CLI to support sub-tasks for AI agents, enabling parallel task execution and improving efficiency.",
  "after": "\\item Extended the CLI with sub-task orchestration for AI agents, enabling parallel execution and adding integration tests around the task lifecycle.",
  "jd": ["R5", "R8"],
  "evidence": "Resume: TabbyML CLI sub-task bullet; testing scope confirmed by vault",
  "risk": "verified",
  "rationale": "Surfaces the JD's testing requirement (R8) with the same verified work; original said only 'improving efficiency'.",
  "defense": "I added the sub-task mode to the Pochi CLI and wrote the integration tests that exercised task spawn/cancel."
}
```

Field rules:

- `type`: `rewrite` | `add` | `remove` | `reorder` | `style`.
  - `rewrite` needs both `before` and `after` (raw LaTeX, `\item` prefix optional).
  - `add` needs `after` only; `remove` needs `before` only.
  - `reorder` declares a unit that moved but whose text changed at most trivially.
  - `style` is for pure fit/spacing edits (compression for one-page fit).
- `before`/`after` must be copied from the actual files closely enough that the
  validator can find them (fuzzy matching tolerates whitespace, not paraphrase).
- `jd`: list of `jd_requirements` ids this change serves. **Every content change
  must cite at least one.** A change that serves no JD requirement should not exist.
- `evidence`: where the underlying fact comes from (vault quote, resume line).
- `risk`:
  - `verified` — directly supported by the vault/resume.
  - `adjacent` — real work, reframed in JD terminology.
  - `needs-confirmation` — plausible but unverified; the report puts it on the
    pre-submission checklist. Not allowed in the strict variant.
- `rationale`: one sentence on why the new text beats the old one. "Stronger wording"
  is not a rationale; name the information gained.
- `defense`: required when `risk` is `adjacent` or `needs-confirmation` — one
  first-person sentence the candidate could actually say in an interview to back
  the claim. If no such sentence exists, the change is not allowed.

## Keywords

Declare the JD terms you targeted and where they landed. The validator checks each
`covered` term actually appears in the variant text and flags mismatches:

```json
{ "term": "distributed systems", "status": "covered", "where": "Kobe project, bullet 3" }
{ "term": "Go",                  "status": "covered", "where": "Technical Skills line" }
{ "term": "SVN",                 "status": "gap",     "where": "" }
```

`status`: `covered` | `adjacent` | `gap`. Report gaps honestly — a gap is
information for the user, not a defect to paper over.
