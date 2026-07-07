---
name: latex-resume-tailoring
description: LaTeX-first resume tailoring for a target job description using an existing resume.tex and a verified career vault or master resume. Use when Codex needs to analyze a JD, select truthful experience, patch an existing LaTeX resume without changing its template or style, compile/check one-page fit, and produce ATS, recruiter, senior engineer, and integrity reviews.
---

# LaTeX Resume Tailoring

## Overview

Tailor an existing LaTeX resume to a target role while preserving the user's original structure, formatting, tone, and factual boundaries. Treat the career vault/master resume as the only source of truth for experience, metrics, projects, technologies, dates, and responsibilities.

Never edit the user's original resume file. Always create a versioned copy first, then edit only that copy.

## Required Inputs

Collect or locate these before editing:

- Target job description.
- Existing `resume.tex`.
- Verified career vault, master resume, or equivalent source of real experience.
- Optional constraints: page limit, preferred projects, target roles to emphasize, sections that must not change.

If any required input is missing, ask for it before editing or proceed only with an explicit limitation in the final risk report.

## Missing Input Questions

Before analyzing or editing, check whether the user provided all required inputs. If not, ask concise questions and wait when the missing input would change the resume patch.

Prefer a structured user-question tool when the host exposes one, such as `AskUserQuestion`, `request_user_input`, or an equivalent UI question tool. Use that tool instead of burying the request inside a long prose response. If no structured question tool is available, ask a short plain-text question.

When multiple required inputs are missing, ask for them in one clear question with a short checklist. Do not ask more than three questions at once.

Ask for:

- Missing JD: "Please provide the target job description or a link/text for the role."
- Missing `resume.tex`: "Please provide the existing LaTeX resume file or paste the current `resume.tex` content."
- Missing career vault/master resume: "Please provide your verified career vault, master resume, or factual experience notes. I need this to avoid inventing experience, metrics, technologies, or responsibilities."
- Missing constraints when implied: "Should I enforce a one-page limit, and are there projects or roles you want emphasized or protected?"

Do not make factual resume edits without a career vault or equivalent verified source. If the user only wants a high-level review of the existing resume against a JD, proceed without editing and label the output as review-only with unsupported gaps.

## Workflow

### 1. Analyze the Job Description

Extract:

- Required skills, languages, frameworks, platforms, credentials, and years/seniority signals.
- Preferred skills and domain keywords.
- Role focus: backend, frontend, full stack, ML, data, infrastructure, security, product, leadership, etc.
- Recruiter scan priorities and likely ATS search terms.
- Evidence the resume should show, such as scale, ownership, reliability, customer impact, collaboration, or system design.

Keep the JD analysis separate from the resume edit so keyword choices are traceable.

### 2. Build a Truth Map

Map JD requirements to verified vault evidence before rewriting:

- Direct match: the vault explicitly supports the technology, responsibility, domain, metric, or project.
- Adjacent match: the vault supports a related skill, but wording must stay honest.
- Gap: no verified evidence exists. Do not invent content; report the gap.

Never add unverified employers, titles, dates, degrees, certifications, tools, metrics, leadership scope, production usage, user counts, revenue, latency, availability, or security/compliance claims.

### 3. Create a Versioned Working Copy

Before any resume edit, copy the source LaTeX file into a generated variant folder and treat that copy as the only editable resume for the task. Do not patch, format, compile in place, or otherwise mutate the original `resume.tex`, even if the user asks for a single tailored version.

Use the bundled helper when available:

```bash
python3 latex-resume-tailoring/scripts/create_resume_variant.py path/to/resume.tex --label company-role
```

The helper creates:

```text
<resume-dir>/resume_variants/<YYYYMMDD>-<label>-vNN>/resume.tex
```

Rules:

- Put every generated resume variant under `resume_variants/` next to the source resume unless the user explicitly chooses another output directory.
- Use a short slug from the target company, role, or user-provided label. If no label is available, use `target-role`.
- Increment `vNN` automatically when a folder for the same date and label already exists.
- Preserve any sibling files needed by the LaTeX template by copying the whole source directory only when compilation requires local assets; otherwise copy just the `.tex` file.
- Keep the original file path and the generated variant path in the final response.

### 4. Patch the Working Copy

Edit only the versioned working copy. Preserve:

- Section order, custom commands, packages, spacing conventions, bullet syntax, and typography.
- The user's existing tone and density.
- Macros and escaping style, including how the file represents `&`, `%`, `_`, `#`, and links.

Prefer content edits before format edits. Reorder or rewrite bullets only when doing so improves JD relevance, clarity, scanability, or truthful keyword coverage. Keep bullets specific: action, technical method, scope, and result when supported by the vault.

### 5. Compile and Check Fit

Run the helper after each meaningful patch:

```bash
python3 latex-resume-tailoring/scripts/check_latex_resume.py path/to/resume_variants/YYYYMMDD-label-vNN/resume.tex --max-pages 1
```

If the resume overflows, compress content in this order:

- Remove or shorten the least relevant bullets.
- Tighten wording and remove filler.
- Prefer stronger project/skill selection.
- Only then adjust LaTeX spacing, and keep changes minimal and consistent with the original file.

If LaTeX is unavailable, still patch the file and report that compile/page validation could not be completed.

### 6. Review From Four Angles

Read `references/review_rubric.md` when producing the final review or when judging borderline changes.

Always include:

- ATS reviewer: keyword coverage, role alignment, parsing friendliness.
- HR recruiter: quick-scan match, title/seniority fit, clarity of impact.
- Senior SDE interviewer: technical credibility, system/project depth, ownership, likely interview signal.
- Quality and integrity review: hallucination risk, keyword stuffing risk, format drift risk, unsupported claims, remaining JD gaps.

### 7. Final Response

Return or summarize:

- Original `resume.tex` path, clearly labeled as unchanged.
- Updated variant `resume.tex` path.
- Compiled PDF path when compilation succeeds.
- Change summary grouped by section.
- ATS, HR recruiter, and senior SDE review result.
- Risk report covering hallucination risk, keyword stuffing risk, format drift risk, and remaining JD gaps.
- Compile/page-check result, including page count and any LaTeX warnings that matter.

## Editing Standards

- Keep changes narrowly tied to JD requirements and verified vault evidence.
- Use natural keywords inside truthful bullets; do not create keyword lists that feel disconnected from the work.
- Do not edit the source resume, replace the user's template, regenerate the resume from scratch, or normalize style across the file unless asked.
- Avoid vague inflation such as "optimized systems" or "owned architecture" unless the vault supports what was optimized, owned, and measured.
- Preserve one-page constraints unless the user explicitly allows more pages.
