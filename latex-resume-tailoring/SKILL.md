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

**The information test — apply it before you edit, not after.** For each unit you intend to change, write one sentence naming what the reader now learns that they did not before. If the only sentence you can write is "it reads better", "it's more active", or "it matches the JD's tone", the edit is churn: keep the original byte-identical and say so in the report. A smoother sentence is not an improvement; specificity is what a reviewer actually reads, and every concrete noun dropped for concision is information the reader no longer has. This also rules out relabelling something into a vaguer category to chase a keyword the JD never asks for — the more specific name is almost always the better one.

**User rejections are durable rules, not one-off notes.** When the user cuts or replaces content, record (a) their reason in their own words and (b) the *class* of content that reason rules out, then treat it as binding on every later job. Re-serving rejected content in a trimmed or reworded form is the same violation as re-serving it whole. If a later JD makes the rejected content look valuable again, do not quietly reinstate it: say that they cut it, why, what the new JD makes it worth, and let them decide. Standing *preferences* carry forward the same way — if they asked for something to be restored "when it fits", try it on every later variant and report whether it fit.

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

### 4b. Fidelity check — re-read every rewrite against its source

Positioning may change; facts may not. Before writing the manifest, take each rewritten unit back to the line it came from (the original `.tex` bullet, or the vault entry) and check three axes. Report the result in the final response, including anything you reverted.

1. **Added anything the source does not state?** A phase ("through deployment" when nothing says they deployed), an action, a scale, or a difficulty claim ("reconciling" where the source said "translating"). Unevidenced additions come out — including ones that merely sound safe because the rest of the sentence is true.
2. **Dropped anything the source does state?** A metric, a technology, or a scope term. Trimming a scope list to make a line shorter is a loss, not an edit. Restore it.
3. **Changed the subject or the actor?** "Parent agents fan out subtasks" rewritten as "the layer fans out to worker processes" moves who is doing the work. That is a change of meaning, not of emphasis.

Two hard rules that fall out of this:

- **Never silently rewrite a sentence the user wrote themselves.** Reproduce it verbatim, or state in the manifest why it changed.
- A `defense` sentence is written in the user's first person, so it must be something they can actually say. If you had to invent circumstances to make it read well, the underlying edit is not allowed.

The same standard governs free-text application answers written alongside the resume: draft only from what the user has actually built, read, or told you. If a question needs an experience they have not reported, ask for it rather than supplying one, and label any connective scaffolding they must replace before sending.

### 5. Write the change manifest

For each variant, write `changes.json` next to its `resume.tex` following `references/changes_schema.md`. One entry per edited unit: `section`, `type`, `before`/`after` (copied from the actual files), `jd` ids, `evidence`, `risk`, `rationale`, `defense`. Also record `jd_requirements` and the `keywords` coverage table (report gaps honestly). When an edit was reverted during the fidelity check, keep a line about it in the surviving entry's rationale so the review page shows what was withdrawn and why.

**Read `references/tooling_pitfalls.md` before writing the first manifest.** Extracted units absorb trailing scaffolding, so entry headers and the skills block belong on the *preceding* bullet's card, and two entries resolving to one unit will fail; keyword terms must be literal page strings; and any script containing LaTeX must be written with a file-write tool rather than through a bash heredoc. These are structural, not incidental — designing the manifest around them costs minutes, discovering them per job costs far more.

Generating the manifest from a small builder script (anchors → full unit text) is more reliable than hand-writing JSON, because the `before`/`after` fields must match extracted units exactly.

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

Served over HTTP, every panel gains two server-backed buttons. **Recompile preview**: the user drops or edits changes and clicks it, and the server reverts dropped edits, applies fine-tuned wording, recompiles, and swaps in the fresh preview with a page-count status — no download round-trip. **Apply & download final PDF** (the primary action): the server applies the decisions to the variant's `resume.tex` and `changes.json` on disk (same logic as `--apply-decisions`), recompiles, re-renders the report, and the browser downloads the submission-ready PDF — the user finishes without touching a terminal. Fall back to handing over the `review.html` path alone only when a background server genuinely can't run (no background-process support, sandboxed environment) — the file is fully self-contained, so everything except live recompile still works.

The report shows per-bullet before/after with word-level highlights, rationale, risk badges, the needs-confirmation checklist, keyword coverage, and what was kept verbatim.

The report is also interactive: every change card has a **Keep / Edit / Drop** toggle. The user can drop any change they dislike, or click Edit to fine-tune the suggested wording in place (the box takes raw LaTeX). In serve mode they finish with "Apply & download final PDF" (above); in the static file, the primary action is "Copy decisions JSON" to hand their selections back to you ("Download edited .tex" also exists for users who want the raw LaTeX with their decisions applied in-browser — it does not update the files on disk). **When the user pastes a decisions JSON**, save it to a file and apply it with the script — never hand-edit the files:

```bash
python3 scripts/render_review.py --original path/to/resume.tex \
  --variant <strict-dir> --variant <stretch-dir> --apply-decisions decisions.json
```

Only the variant named in the decisions file is modified: each `dropped` change is reverted in its `resume.tex` and removed from `changes.json`; each `edited` change gets its `new_latex` in the file and in the manifest entry's `after`. The variant is then recompiled and the report re-rendered, so one command leaves files, manifest, PDF, and report consistent. **Exit code 4 means some decisions were skipped as unsafe** (ambiguous or missing text — listed under `applied.skipped` in the JSON summary): apply those few by hand, keep their manifest entries honest, and re-run without `--apply-decisions` until the summary is clean. After applying, re-read each edited entry's rationale/evidence and adjust them if the user's wording changed the claim.

### 8. Review from four angles

Read `references/review_rubric.md`. Cover ATS, recruiter, senior-engineer, and integrity perspectives for both variants, then recommend: strict, stretch after confirming flagged claims, or strict plus selected stretch edits.

### 9. Final response

Report: original path (unchanged), both variant paths, PDF paths and page counts, the live review URL (plus the `review.html` path as fallback), per-variant change counts (declared / kept verbatim / needs-confirmation), keyword gaps, the four-angle review, and the submission recommendation. If no vault was provided, state that the resume itself was the fact boundary.

Also say, without being asked:

- **What the fidelity check found** (step 4b) — anything added, dropped, or reattributed, and what you reverted. "Nothing drifted" is a valid and useful answer.
- **Why each project or entry earned its slot, and what it displaced.** Selection is the highest-leverage decision in a tailoring run and the user will ask if you don't volunteer it.
- **What the page lost.** Cutting a bullet or a technology list removes keywords; name the ones that no longer appear anywhere and say which kinds of role would want them back.
- **Any eligibility gate in the JD** — graduation window, degree level, work authorization, location, sponsorship — checked against the resume, flagged before the review rather than buried under it.
