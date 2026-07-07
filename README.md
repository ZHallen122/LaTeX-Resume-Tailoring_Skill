# LaTeX Resume Tailoring Skill

LaTeX Resume Tailoring is a Codex skill for tailoring an existing `resume.tex` to a target job description. It preserves the user's resume structure and style, uses a verified career vault as the source of truth, and produces review output from ATS, recruiter, senior engineering, and integrity perspectives.

The skill never edits the user's source `resume.tex` in place. It first creates a versioned working copy under `resume_variants/`, then patches and compiles that copy so users can generate and compare multiple tailored versions.

The project also includes a small static review cockpit for reading LaTeX resume content in a more human-friendly way before or after tailoring.

## What It Does

- Analyzes a target job description for required skills, preferred skills, seniority signals, domain keywords, and likely recruiter/ATS priorities.
- Matches the JD against a verified career vault or master resume.
- Creates a versioned copy of the existing `resume.tex`, then patches that copy instead of generating a new template or mutating the original.
- Avoids invented experience, metrics, tools, responsibilities, dates, or ownership claims.
- Compiles the resume to PDF and checks page count when a local LaTeX environment is available.
- Reports ATS, HR recruiter, Senior SDE, and integrity review results.
- Flags hallucination risk, keyword stuffing risk, format drift risk, and remaining JD gaps.

## Repository Layout

```text
.
├── latex-resume-tailoring/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   ├── references/review_rubric.md
│   └── scripts/
│       ├── check_latex_resume.py
│       └── create_resume_variant.py
├── resume-review-ui/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── setup_codex.sh
└── README.md
```

## Install For Codex

Run the Codex-only setup script from the repository root:

```bash
./setup_codex.sh
```

This installs the skill to:

```bash
${CODEX_HOME:-$HOME/.codex}/skills/latex-resume-tailoring
```

If the skill is already installed and you want to replace it:

```bash
./setup_codex.sh --force
```

Restart Codex after installation if it was already running.

## Requirements

For resume analysis and LaTeX editing:

- Codex
- The target JD
- The user's existing `resume.tex`
- A verified career vault, master resume, or factual experience notes

For PDF compilation and one-page checks:

- `latexmk` recommended
- `pdflatex` supported as fallback
- `xelatex` and `lualatex` supported when selected explicitly
- `pdfinfo` recommended for page counting

The skill can still edit and review `resume.tex` without a local LaTeX installation. It will report that PDF compile/page validation could not be completed.

Common install options:

```bash
# macOS
brew install --cask mactex

# Ubuntu/Debian
sudo apt-get install texlive-latex-recommended texlive-latex-extra latexmk poppler-utils
```

`MacTeX` is large. `BasicTeX` plus `latexmk` can be enough for many resume templates, but some templates need extra packages.

## Basic Usage

After installing, invoke the skill in Codex:

```text
Use $latex-resume-tailoring to tailor my resume.tex for this job description using only my career vault. Keep it one page.
```

Provide or point Codex to:

```text
JD: <paste the target job description>
resume.tex: ./resume.tex
Career vault: ./career-vault.md
Constraints: one page, emphasize backend/platform work, preserve current formatting
```

The skill will ask for missing required inputs. In particular, it must ask for a career vault or equivalent verified source before making factual resume edits.

## Expected Workflow

1. Codex checks whether JD, `resume.tex`, and career vault are available.
2. Codex asks concise follow-up questions for missing materials.
3. Codex analyzes the JD and identifies role priorities.
4. Codex builds a truth map from JD requirements to verified career evidence.
5. Codex creates a versioned working copy under `resume_variants/<YYYYMMDD>-<label>-vNN>/resume.tex`.
6. Codex patches the copied LaTeX resume without changing the template.
7. Codex compiles the PDF and checks the page limit when LaTeX tools are installed.
8. Codex compresses or trims content if the resume exceeds the page limit.
9. Codex returns the unchanged source path, updated variant path, PDF path when available, change summary, reviews, and risk report.

If the user only wants a high-level review and has not provided a career vault, the skill should proceed as review-only and label unsupported gaps clearly.

## Versioned Resume Copies

Create an editable resume variant before tailoring:

```bash
python3 latex-resume-tailoring/scripts/create_resume_variant.py path/to/resume.tex --label company-role
```

The helper creates:

```text
path/to/resume_variants/YYYYMMDD-company-role-v01/resume.tex
```

Run it again with the same label on the same day and it creates `v02`, `v03`, and so on. Use `--copy-assets` when the LaTeX template depends on sibling images, style files, fonts, or other local assets.

## Compile And Page Check Script

The helper script compiles a LaTeX resume and reports status as JSON:

```bash
python3 latex-resume-tailoring/scripts/check_latex_resume.py path/to/resume.tex --max-pages 1
```

Options:

```bash
python3 latex-resume-tailoring/scripts/check_latex_resume.py path/to/resume.tex \
  --max-pages 1 \
  --engine auto \
  --out-dir path/to/build
```

Supported engines:

- `auto`: use `latexmk` if available, otherwise `pdflatex`
- `pdflatex`
- `xelatex`
- `lualatex`

Exit codes:

- `0`: compiled and within page limit
- `1`: compile failed or no PDF was produced
- `2`: compiled but exceeded the page limit

## Review Cockpit

The optional static frontend helps users review LaTeX resume content without reading raw LaTeX:

```text
resume-review-ui/index.html
```

Open that file in a browser. No dev server or build step is required.

The cockpit supports:

- JD input
- Career vault input
- `resume.tex` input
- Human-readable resume preview
- Keyword coverage view
- ATS review
- HR recruiter review
- Senior SDE review
- Integrity review
- Patch idea view
- Copyable review report

The current UI is heuristic and runs fully in the browser. It does not call an LLM or modify files.

## Integrity Rules

The skill treats the career vault as the source of truth. It must not add:

- New employers, roles, titles, or dates
- New degrees, certifications, or credentials
- Unverified technologies
- Unverified metrics
- Unsupported production, scale, security, compliance, leadership, or ownership claims

When the JD asks for something the vault does not support, the skill reports a remaining gap instead of fabricating evidence.

## Troubleshooting

If Codex does not find the skill:

1. Confirm it was installed under `${CODEX_HOME:-$HOME/.codex}/skills/latex-resume-tailoring`.
2. Restart Codex.
3. Invoke it explicitly with `$latex-resume-tailoring`.

If PDF compilation fails:

1. Run the check script manually.
2. Confirm `latexmk` or `pdflatex` is installed.
3. Check whether the resume template needs additional LaTeX packages.
4. Try a specific engine if the template requires it:

```bash
python3 latex-resume-tailoring/scripts/check_latex_resume.py resume.tex --engine xelatex
```

If the resume exceeds one page:

1. Shorten the least relevant bullets.
2. Remove weaker projects or older details.
3. Tighten wording.
4. Adjust spacing only after content edits are not enough.

## Current Limitations

- The setup script installs Codex only.
- The review cockpit is a local static prototype and does not persist data.
- The compile helper depends on local LaTeX tools for PDF generation.
- The skill can enforce truthfulness only when the user provides a reliable career vault.
