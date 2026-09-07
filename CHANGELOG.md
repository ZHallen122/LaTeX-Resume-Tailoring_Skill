# Changelog

All notable changes to the latex-resume-tailoring skill.

## [0.9.0] - 2026-09-07

### Added
- **Step 4b, the fidelity check.** After rewriting and before writing the manifest, every changed unit is read back against its source on three axes: did the rewrite *add* something the source does not state (a phase like "through deployment", a scale, a difficulty claim); did it *drop* something the source does state (a metric, a technology, a scope term); did it change the *subject or actor* of the sentence. All three were violated in real runs — a bullet gained a deployment phase nobody had evidence for, a scope list was trimmed to shorten a line, and a sentence the user had written himself was rewritten so that the actor moved from the agents to the layer they run on. The step also makes two rules explicit: never silently rewrite a sentence the user wrote, and a `defense` must be something they can actually say. The result of the check is now part of the final report.
- **`references/tooling_pitfalls.md`**, and a pointer to it from step 5. Four failure modes that recurred on nearly every run: extracted units absorb trailing scaffolding (so entry headers and the TECHNICAL SKILLS block must be declared on the *preceding* bullet's card, and two entries resolving to one unit fail under greedy matching); bash heredocs mangle backslashes and corrupt any script containing LaTeX; keyword-table terms are substring-matched and must be literal page strings; and patches to generator scripts need line-indexed anchors, because a loose substring once matched a continuation line and destroyed the definition it was inside.
- **Reporting requirements** in step 9: what the fidelity check found, why each project earned its slot and what it displaced, which keywords left the page entirely, and any eligibility gate in the JD (graduation window, degree level, work authorization, location, sponsorship) checked against the resume and flagged up front rather than buried.

### Changed
- **The no-churn rule now has a test that runs before the edit, not after.** For each unit you intend to change, write one sentence naming what the reader now learns that they did not before; if the only available sentence is "it reads better" or "it matches the JD's tone", keep the original byte-identical. Added the corollary that relabelling something into a vaguer category to chase a keyword the JD never asks for is churn — the more specific name is almost always the better one.
- **User rejections are now specified as durable rules.** Record the reason and the class of content it rules out, apply it to every later job, and never re-serve rejected content in a trimmed or reworded form; if a later JD makes it look valuable again, say so and let the user decide. Standing preferences carry forward the same way. This was written after the same bullet was rejected twice — once whole, once trimmed.
- Step 4b extends the no-fabrication rule to free-text application answers drafted alongside the resume: draft only from what the user has built, read, or told you, ask for missing experiences instead of supplying them, and label scaffolding they must replace.
- Step 5 now recommends generating the manifest from a small builder script rather than hand-writing JSON, since `before`/`after` must match extracted units exactly.

## [0.8.0] - 2026-07-09

### Added
- **Apply & download final PDF** button (serve mode): a new `/apply` endpoint on the live-review server runs the same logic as `--apply-decisions` — dropped changes are reverted in the variant's `resume.tex` and removed from `changes.json`, edits are written to both, the variant is recompiled, the report re-renders — and the browser downloads the final compiled PDF. This replaces the old flow where the primary button handed the user a browser-built `.tex` they had to compile themselves, which silently diverged from the files on disk. Loopback/origin hardening and a compile lock cover the new endpoint; after an apply the page reloads to show the applied state and the variant's saved selections are cleared (change ids shift when manifest entries are dropped).

### Changed
- Download flow de-confused: in serve mode "Apply & download final PDF" is the single primary action; without a server, "Copy decisions JSON" (hand back to the agent) is primary. The browser-built `.tex` download is demoted to "Download edited .tex" with an explicit "compile it yourself / files on disk are not updated" tooltip.

### Security
- Hardened the live-preview server's mutating endpoints (`/recompile`, `/apply`). The prior `Host`/`Origin` check used prefix matching, so `localhost.evil.com` / `127.0.0.1.evil.com` passed it — a malicious page could drive a no-preflight cross-origin POST to `/apply` and overwrite the user's `resume.tex` and `changes.json` on disk. Now: a per-process secret token is injected into the served page (never written to the on-disk report) and required on every mutating POST — a cross-origin page cannot read the loopback response, so it cannot learn the token; `Host`/`Origin` are matched by exact loopback hostname instead of prefix; and a non-`application/json` content type is rejected to close the CORS simple-request path.
- `apply_decisions()` now writes a one-deep `.bak` of both `resume.tex` and `changes.json` before the destructive apply, and computes the new manifest fully before touching either file, so a bad edit or a mid-write failure is recoverable. The report's Apply button no longer clears the browser's saved Keep/Edit/Drop selections when nothing was actually applied.
- Review report UI overhaul for readability and usability:
  - New masthead: the JD title is the page headline, file paths are tucked into a collapsible "Source files" block instead of dominating the header.
  - Filter bar per variant (All / Kept / Edited / Dropped / Needs confirmation) so long reviews can be worked through by decision state, with an empty-state message when a filter matches nothing.
  - Sticky action bar now shows a live decision breakdown as colored Keep/Edited/Dropped pills, plus a **Reset** button that clears all saved selections for the variant (with confirmation).
  - The long usage paragraph is now a collapsible "How this review works" step list.
  - Cards get a decision-colored left edge (green kept, amber edited, red dashed dropped), before/after columns sit on tinted panels, and risk badges use theme variables so "adjacent"/"needs confirmation" are legible in dark mode (previously hardcoded light-only colors).
  - Variant tabs show their change count and the tab row is hidden when there is only one variant.
  - General polish: refined light/dark palettes, card shadows, hover/focus-visible states, smooth panel transitions, and table row hover.

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
