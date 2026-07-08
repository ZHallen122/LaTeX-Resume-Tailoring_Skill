# Changelog

All notable changes to the latex-resume-tailoring skill.

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
