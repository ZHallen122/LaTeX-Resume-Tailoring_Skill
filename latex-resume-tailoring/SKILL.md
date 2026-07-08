---
name: latex-resume-tailoring
description: LaTeX-first resume tailoring for a target job description using an existing resume.tex. Use when the agent needs to analyze a JD, produce strict and stretch resume variants with a machine-checked change manifest, patch existing LaTeX without changing its template, compile and check one-page fit, and generate an HTML before/after review report. Works in Claude Code and Codex.
---

# LaTeX Resume Tailoring

## Overview

Tailor an existing LaTeX resume to a target role while preserving the user's template, tone, and factual boundaries. The workflow is **manifest-driven**: every edit must be declared in a `changes.json` next to the edited file, citing the JD requirement it serves, the evidence behind it, and a risk level. `scripts/render_review.py` cross-checks the manifest against the real diff and renders an HTML before/after report; any undeclared edit is flagged and must be declared or reverted. Silent rewording is a bug, not a style.

Produce two variants by default:

- **Strict** — only claims directly supported by the vault/resume. The safe submission.
- **Stretch** — stronger *positioning* of the same true facts: reordering, selection, JD terminology, surfacing buried evidence. Not a license to reword everything.

Never edit the user's original resume file. Always create versioned copies first.

## Required Inputs

- Target job description (text or URL — fetch it if given a URL).
- Existing resume `.tex` file.
- Career vault / master resume when available. **If the user has no vault, do not block:** treat the resume itself as the fact boundary, say so in the final report, and keep every claim within what the resume already states. Ask (once, via a structured question tool if available) whether they have extra verified material; proceed either way.
- Optional constraints: page limit (default: keep the current page count), protected sections, emphasis preferences.

If the user asks for a "fake" or inflated resume, do not fabricate. Produce the stretch variant and explain it is the strongest defensible version.

## Workflow

### 1. Analyze the JD into numbered requirements

Extract requirements and give each a stable id (`R1..Rn` required, `P1..Pn` preferred), plus likely ATS keywords and the role's scan priorities. These ids are what every change will cite, so keep them concrete ("R5: distributed systems", not "R5: strong engineer").

### 2. Build the truth map

For each requirement: **direct match** (evidence exists verbatim), **adjacent match** (real work that maps onto it with honest rewording), **gap** (no evidence — report it, never fill it). Note the exact resume/vault line that backs each match; you will paste it into `evidence` fields later.

### 3. Create versioned working copies

```bash
python3 scripts/create_resume_variant.py path/to/resume.tex --label <company-role>-strict
python3 scripts/create_resume_variant.py path/to/resume.tex --label <company-role>-stretch
```

This creates `resume_variants/<YYYYMMDD>-<label>-vNN/resume.tex` next to the source. Edit only these copies. Use `--copy-assets` when the template needs sibling files.

### 4. Edit under the bullet quality bar

Preserve the template: section order, macros, packages, spacing, escaping style (`\&`, `\%`, `\textbar{}`, ...), bullet syntax, tone, density.

**The no-churn rule:** touch a unit (bullet, skills line, header) only when you can name (a) the JD requirement it serves and (b) the evidence behind the new wording. A bullet that is already strong and JD-relevant stays **byte-identical**. Typical good tailoring touches 30–60% of bullets; touching 100% is a red flag that you are churning, not tailoring.

**Every rewritten bullet must beat the original**, meaning it adds JD-relevant information (a requirement now evidenced, a keyword now natural, a scope now explicit) or sharpens impact — while keeping every verified metric, technology, and scope term from the original. If your rewrite is merely different rather than better, revert it.

Bullet shape: action verb + what was built (specific tech) + how (method/architecture) + outcome (metric/scope) — but only components the evidence supports.

**Anti-patterns (all count as churn — revert on sight):**
- Synonym shuffling: "Built" → "Developed", "improving" → "enhancing" with no information gained.
- Dropping or weakening a metric, tech name, or scope that was in the original.
- Verb inflation ("led", "owned", "architected") beyond the evidenced responsibility level.
- Rewording bullets unrelated to any JD requirement.
- Keyword stuffing: JD terms bolted onto bullets whose work doesn't evidence them.

**Strict variant:** direct matches only; every change `risk: verified`.

**Stretch variant** starts from strict and pushes *positioning*, not fiction:
- Reorder bullets/sections so JD-matching evidence is read first.
- Adopt the JD's exact terminology where the underlying fact matches (built a daemon with RPC channels and task persistence → "distributed" is fair; a class project → "production" is not).
- Surface adjacent or buried evidence into prominent bullets, tagged `risk: adjacent`.
- A claim that is plausible but unverified may appear **only** with `risk: needs-confirmation` and only if removing it later won't break the resume.
- Every `adjacent` / `needs-confirmation` change must include a `defense`: one first-person sentence the user could say in an interview to back it. **If you cannot write that sentence, the change is not allowed.** This is the stretch quality bar — impressive means "survives a follow-up question", not "sounds bigger".

Stretch may never invent employers, titles, dates, degrees, certifications, tools, metrics, users, revenue, production deployment, compliance posture, or leadership scope, and may never convert exposure into ownership or coursework into professional experience.

### 5. Write the change manifest

For each variant, write `changes.json` next to its `resume.tex` following `references/changes_schema.md`. One entry per edited unit: `section`, `type`, `before`/`after` (copied from the actual files), `jd` ids, `evidence`, `risk`, `rationale`, `defense`. Also record `jd_requirements` and the `keywords` coverage table (report gaps honestly).

### 6. Compile and check fit

```bash
python3 scripts/check_latex_resume.py <variant>/resume.tex --max-pages 1
```

On overflow, compress in order: drop least-relevant bullets → tighten wording → only then minimal spacing tweaks (declare them as `type: style`). If LaTeX is unavailable, say so and continue.

### 7. Render and validate the review report

```bash
python3 scripts/render_review.py --original path/to/resume.tex \
  --variant <strict-dir> --variant <stretch-dir>
```

Compile the original resume once too (`check_latex_resume.py path/to/resume.tex`) so the report can embed a side-by-side compiled preview: when `<dir>/build/resume.pdf` exists and `pdftoppm` is available, each variant panel shows the original and tailored PDFs as images — the user reviews the visual result in the page, no Overleaf needed.

The script writes `resume_variants/review.html` and prints a JSON summary. **Exit code 3 means unexplained changes exist** — edits in the file that no manifest entry declares. Fix each one by either adding an honest manifest entry or reverting the edit, then re-run until the summary says `"ok": true`. Do not present results to the user while unexplained changes remain.

Once the summary says `"ok": true`, **start the live review server — this is the default way to present the result**, not an option. Run the same command with `--serve` in the background (it blocks); with no port argument it auto-picks a free port and prints the URL on stderr — read it from the process output and give it to the user:

```bash
python3 scripts/render_review.py --original ... --variant ... --variant ... --serve
# stderr → Live preview server: http://127.0.0.1:<port>/  (Ctrl+C to stop)
```

Served over HTTP, every panel gains a **Recompile preview** button: the user drops or edits changes and clicks it, and the server reverts dropped edits, applies fine-tuned wording, recompiles, and swaps in the fresh preview with a page-count status — no download round-trip. Fall back to handing over the `review.html` path alone only when a background server genuinely can't run (no background-process support, sandboxed environment) — the file is fully self-contained, so everything except live recompile still works.

The report shows per-bullet before/after with word-level highlights, rationale, risk badges, the needs-confirmation checklist, keyword coverage, and what was kept verbatim.

The report is also interactive: every change card has a **Keep / Edit / Drop** toggle. The user can drop any change they dislike, or click Edit to fine-tune the suggested wording in place (the box takes raw LaTeX), then click "Download final resume.tex" to get a file with dropped changes reverted and edits applied (built entirely in the browser), or "Copy decisions JSON" to hand their selections back to you. **When the user pastes a decisions JSON**, apply it server-side: revert each `dropped` change in the variant's `resume.tex` and delete its `changes.json` entry; for each `edited` entry, replace the change's "after" text with `new_latex` in `resume.tex` and update the manifest entry's `after` field to match (keep its rationale/evidence, adjusting them only if the edit changed the claim). Then recompile and re-run `render_review.py` so the report matches the final file.

### 8. Review from four angles

Read `references/review_rubric.md`. Cover ATS, recruiter, senior-engineer, and integrity perspectives for both variants, then recommend: strict, stretch after confirming flagged claims, or strict plus selected stretch edits.

### 9. Final response

Report: original path (unchanged), both variant paths, PDF paths and page counts, the live review URL (plus the `review.html` path as fallback), per-variant change counts (declared / kept verbatim / needs-confirmation), keyword gaps, the four-angle review, and the submission recommendation. If no vault was provided, state that the resume itself was the fact boundary.
