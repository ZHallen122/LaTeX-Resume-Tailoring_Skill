# LaTeX Resume Tailoring Skill

An agent skill (Claude Code and Codex) for tailoring an existing `resume.tex` to a target job description. It preserves the resume's template and factual boundaries, produces **strict** and **stretch** variants, and — the core of v0.2 — makes every edit **observable and accountable**:

- Every edit must be declared in a `changes.json` manifest: which bullet changed, which JD requirement it serves, what evidence backs it, and its risk level (`verified` / `adjacent` / `needs-confirmation`).
- `render_review.py` cross-checks the manifest against the actual file diff and renders a single self-contained `review.html`: per-bullet before/after with word-level highlighting, rationale and risk badges on every change, a needs-confirmation checklist, JD keyword coverage, and the list of bullets kept verbatim.
- The report is interactive: each change has a **Keep / Edit / Drop** toggle. Drop the changes you dislike, or edit a suggestion in place to fine-tune its wording, and download a final `resume.tex` with drops reverted and edits applied — built entirely in the browser, no server. Or copy the decisions JSON back to the agent to apply, recompile, and re-review.
- The compiled result is visible in the page: when variant PDFs exist (and `pdftoppm` is installed), the report embeds original-vs-tailored page images side by side. The agent serves the report live by default (`render_review.py ... --serve`), which adds a **Recompile preview** button — drop changes, click, and the local server recompiles and refreshes the preview in seconds. No Overleaf round-trip.
- Any edit the manifest does not explain is flagged as an **unexplained change** (exit code 3). The agent must declare it honestly or revert it before presenting results. Silent synonym-shuffling is treated as a bug.

The skill never edits the source `resume.tex` in place; it works on versioned copies under `resume_variants/`.

## What "stretch" means here

Stretch is stronger *positioning* of the same true facts — reordering, selection, adopting the JD's exact terminology where the underlying work matches, surfacing buried adjacent evidence. It is governed by a quality bar:

- **No-churn rule**: a bullet is only touched when a specific JD requirement and evidence can be named; strong bullets stay byte-identical.
- **Beats-the-original rule**: a rewrite must add JD-relevant information or sharpen impact while keeping every original metric, tech name, and scope term — otherwise it is reverted.
- **Interview-defense rule**: every `adjacent` or `needs-confirmation` claim must ship with a one-sentence first-person defense the candidate could actually say in an interview. No defense line, no change.
- Fabrication (employers, titles, dates, metrics, tools, production scope, ownership) is never allowed in either variant.

## Repository layout

```text
.
├── latex-resume-tailoring/
│   ├── SKILL.md
│   ├── VERSION
│   ├── agents/openai.yaml
│   ├── references/
│   │   ├── changes_schema.md     # changes.json manifest contract
│   │   └── review_rubric.md      # ATS / recruiter / senior SDE / integrity rubric
│   └── scripts/
│       ├── create_resume_variant.py   # versioned working copies
│       ├── check_latex_resume.py      # compile + page-count check (JSON)
│       └── render_review.py           # HTML diff report + manifest validation
├── tests/
│   └── test_render_review.py     # parser/validator/revert regression suite
├── setup.sh
└── README.md
```

## Install

Current skill version: `0.6.0`

```bash
./setup.sh                  # installs for both Claude Code and Codex
./setup.sh --host claude    # ~/.claude/skills/latex-resume-tailoring
./setup.sh --host codex     # ~/.codex/skills/latex-resume-tailoring
./setup.sh --upgrade        # replace only if this checkout is newer
./setup.sh --force          # replace unconditionally
```

Restart the host after installing.

## Usage

```text
Use the latex-resume-tailoring skill to tailor my resume for this JD.
JD: https://example.com/jobs/123        (or pasted text)
resume: ./main.tex
Career vault: ./career-vault.md         (optional)
Constraints: one page, keep formatting
```

If you have no career vault, the skill proceeds with the resume itself as the fact boundary and says so in the final report — it will not block, and it will not invent anything beyond what the resume already states.

Asking for a "fake" or inflated resume produces the stretch variant instead: the strongest version that survives an interview follow-up question, with unverified claims routed to the confirmation checklist.

## Workflow (what the agent does)

1. Analyzes the JD into numbered requirements (`R1..`, `P1..`) and ATS keywords.
2. Builds a truth map: direct match / adjacent match / gap per requirement.
3. Creates versioned copies: `resume_variants/<YYYYMMDD>-<label>-vNN/resume.tex`.
4. Edits under the quality bar (no-churn, beats-the-original, anti-pattern list).
5. Writes `changes.json` per variant (see `references/changes_schema.md`).
6. Compiles both variants and checks the page limit (`check_latex_resume.py`).
7. Runs `render_review.py`; re-edits until there are zero unexplained changes, then serves the report live (`--serve`, background) and hands over the URL.
8. Reviews from four angles (ATS / recruiter / senior SDE / integrity) and recommends what to submit.
9. Returns paths (original, variants, PDFs, `review.html`), change counts, keyword gaps, and the recommendation.

## Scripts

```bash
# versioned working copy
python3 latex-resume-tailoring/scripts/create_resume_variant.py main.tex --label uber-swe1-strict

# compile + page check (exit 0 ok / 1 compile failed / 2 over page limit)
python3 latex-resume-tailoring/scripts/check_latex_resume.py <variant>/resume.tex --max-pages 1

# HTML review + manifest validation (exit 0 clean / 3 unexplained changes)
python3 latex-resume-tailoring/scripts/render_review.py \
  --original main.tex --variant <strict-dir> --variant <stretch-dir>
```

`review.html` is fully self-contained (no server, no network) — open it in any browser. The agent serves it live by default (`--serve`, adds recompile-on-drop); the static file is the fallback when a background server can't run.

## Template compatibility

The parser targets standard LaTeX: `\item` bullets, `\section`-style headings, `\\` line breaks. Templates that wrap bullets in custom macros (Jake's Resume `\resumeItem`, moderncv `\cvitem`/`\cventry`) degrade gracefully: each entry is still extracted as a comparable unit, so diffing, manifest validation, and keep/drop all keep working — the entry is just labeled as a line rather than a bullet. Prose wrapped across source lines is merged into one unit, so a single-sentence edit never shows up as multiple false diffs. CRLF files are normalized on read. Worst case for an exotic template is a cosmetic one (uglier extracted text), never a corrupted resume: editing, compiling, and exporting don't go through the parser.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Covers unit extraction across template styles (standard, Jake's Resume, moderncv), paragraph merging, CRLF handling, anti-churn validation (undeclared synonym swaps and metric tampering are flagged), keep/drop revert round-trips, edit application (including the duplicate-text safety skip), and keyword verification.

## Requirements

- Python 3.10+
- For PDF checks: `latexmk` or `pdflatex`, and `pdfinfo` (poppler-utils) recommended

```bash
# Ubuntu/Debian
sudo apt-get install texlive-latex-recommended texlive-latex-extra latexmk poppler-utils
# macOS
brew install --cask mactex
```

Without a LaTeX toolchain the skill still edits, validates, and renders the review; it reports that PDF validation was skipped.

## Integrity rules

The vault (or, absent one, the resume itself) is the source of truth for hard facts. The skill must not add employers, roles, dates, degrees, certifications, unverified technologies or metrics, or unsupported production/scale/security/leadership claims. JD requirements without evidence are reported as gaps — visibly, in the keyword table and final report — rather than papered over.
