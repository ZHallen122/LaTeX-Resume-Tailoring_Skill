# Changelog

All notable changes to the latex-resume-tailoring skill.

## [0.7.0] - 2026-07-09

### Added
- `render_review.py --apply-decisions decisions.json`: deterministic, scripted application of the report's Keep/Edit/Drop selections. Dropped changes are reverted in `resume.tex` and removed from `changes.json`; edited changes get their `new_latex` in both the file and the manifest entry's `after`; the variant is recompiled and the report re-rendered — all in one command. Replaces the previous flow where the agent hand-edited `resume.tex` and the manifest after receiving a decisions JSON (the only step in the loop without a machine guarantee). Unsafe applications (ambiguous or missing text) are skipped, reported under `applied.skipped`, and signalled with new exit code 4; their manifest entries stay untouched so re-validation still passes.
- `build_final_text()` now also returns the set of change ids whose drop/edit was skipped, so callers can sync the manifest precisely.
- Tests for the apply flow: file + manifest sync, index-shift safety when dropping and editing in the same run, unsafe-skip reporting, decisions-JSON parsing (exported shape, empty-edit rejection), and post-apply revalidation.

### Fixed
- Keyword auto-verification used plain substring matching, so short JD terms were falsely verified ✓ inside unrelated words — "Go" by "algorithms"/"Django", "Java" by "JavaScript", "R"/"C" by almost anything. Now whole-token matching: a term must not butt against alphanumerics, and a bare "C" is not evidenced by "C++"/"C#", while symbol terms ("C++", "C#", ".NET", "Node.js") still match exactly.
- `VERSION` file was left at 0.5.0 by the 0.6.0 release, which broke `setup.sh --upgrade` version comparison; now 0.7.0.

## [0.6.0] - 2026-07-08

### Added
- **Edit** toggle on every change card (alongside Keep/Drop): fine-tune the suggested wording in place via a raw-LaTeX textarea, with reset-to-suggestion and empty-text guard. Edits flow through the browser-built final `resume.tex`, the live Recompile preview, and the decisions JSON (`edited` entries carry the `new_latex` text for the agent to apply server-side). Edits persist in the browser alongside Keep/Drop selections; `remove`-type changes have no "after" text and keep the plain Keep/Drop toggle.

### Changed
- `build_final_text()` (server twin of the in-browser builder) accepts an `edits` map; ambiguous edits (target text appearing more than once) are skipped with a warning instead of silently rewriting the wrong bullet, matching the existing drop-revert behavior.

## [0.5.0] - 2026-07-08

### Added
- Machine-checked change manifest (`changes.json` per variant): every edit must declare the JD requirement it serves, its evidence, risk level, and an interview-defense line (`references/changes_schema.md`).
- `scripts/render_review.py`: self-contained HTML review report with per-bullet before/after word-level diff, rationale and risk badges, needs-confirmation checklist, and auto-verified JD keyword coverage.
- Anti-churn validation: undeclared edits (silent synonym swaps, metric tampering) are flagged as unexplained changes with exit code 3 and must be declared or reverted.
- Interactive Keep/Drop toggles on every change with browser-side final `resume.tex` export and a decisions-JSON handoff back to the agent.
- Compiled PDF previews embedded in the report (original vs tailored, click to enlarge), plus `--serve` mode: a loopback HTTP server with a Recompile-preview button that reverts dropped changes, recompiles, and refreshes the preview live.
- Regression test suite (25 tests) covering parser template styles (standard, Jake's Resume, moderncv), paragraph merging, CRLF handling, anti-churn validation, revert round-trips, and manifest hardening.
- Claude Code support via unified `setup.sh` (installs to `~/.claude/skills` and/or `~/.codex/skills`).

### Changed
- SKILL.md rewritten around the manifest-driven workflow with an explicit bullet quality bar: no-churn rule, beats-the-original rule, stretch-as-positioning with a defense line required for every adjacent claim.
- Parser merges prose wrapped across source lines into single comparable units; adjacent macro arguments render readably.
- Missing career vault no longer blocks tailoring: the resume itself becomes the fact boundary, stated in the final report.

### Fixed
- Revert logic refuses (with a warning) instead of silently rewriting the wrong bullet when identical text appears more than once.
- Variant names are sanitized before flowing into HTML/JS contexts; malformed manifests produce clean errors instead of tracebacks.
- `/recompile` endpoint validates Host/Origin and caps request size; installer refuses to delete a target directory that is not an installed copy of this skill.

### Removed
- Paste-based `resume-review-ui` cockpit and the codex-only `setup_codex.sh` installer, replaced by the generated report and unified installer.

## [0.1.1] - earlier
- Initial Codex skill: strict/stretch variants, versioned copies, compile and page-fit checks, review rubric, static review cockpit.
