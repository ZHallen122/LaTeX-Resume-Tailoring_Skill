# Tooling pitfalls

Four failure modes that show up on almost every run. Design around them from the start instead of rediscovering them per job.

## 1. Unit boundaries decide manifest structure

`render_review.py` extracts a "unit" per `\item`, and each unit absorbs everything after its bullet text up to the next `\item`. That trailing scaffolding includes `\end{resumelist}}`, blank lines, the next `\sectiontitle{...}`, and the **next entry header**.

Consequences you must design for, not debug:

- An **experience/project entry header** lives inside the *previous* bullet's unit. Reformatting a header is therefore declared on the card of the bullet that precedes it — not as its own entry.
- The **TECHNICAL SKILLS block** lives inside the last project bullet's unit. The skills rewrite is declared on that bullet's card.
- The **AWARDS block** lives inside the last skills-adjacent unit, same rule.
- A bullet whose text is byte-identical still shows as changed when a neighbour was added or removed, because its absorbed scaffolding moved. Declare it as `type: reorder` with identical `before`/`after` anchors and say in the rationale that only scaffolding shifted.

`pair_variant` matches greedily in manifest order and marks each unit used, so **two entries that resolve to the same unit will fail**: the second reports `"before"/"after" text not found`. When that happens, merge the two into one card whose rationale covers both edits, rather than hunting for a different anchor.

## 2. Never build LaTeX-bearing scripts through a bash heredoc

`\textbf` passed through `python - <<'PYEOF'` has repeatedly arrived as a literal tab plus `extbf`, silently corrupting the script and producing anchors that match nothing.

- Write any script containing LaTeX or backslashes with the **Write tool**.
- If a patch must run inside bash, construct backslashes as `chr(92)` and assert `chr(9) not in text` before writing.
- Prefer **backslash-free anchors** everywhere (`"56 Pundit policies"`, not `r"\textbf{56} Pundit"`). Every anchor in a manifest builder should be plain prose.

## 3. Keyword-table terms must be literal page strings

The validator substring-matches each `covered` term against the `.tex`. These all fail even when the underlying evidence is on the page:

| Fails | Use instead |
|---|---|
| `data processing` | `data pipelines` |
| `React / TypeScript`, `AWS / GCP / Azure` (slash-joined) | one term per row |
| `M.S.`, `North America`, `0-2 years experience` | `Master of Science`, `US Citizen`, or mark `adjacent` |
| `Monitoring & Alerting` | the file holds `Monitoring \& Alerting` — match the source, or use `Monitoring` |
| `developer tools`, `scalable`, `open source` | `developer tool`, `scalability`, `open-source` |

Put the JD's own phrasing in the `where` field. `adjacent` and `gap` rows are not string-checked, so use them honestly instead of bending a term to pass.

## 4. Patch builder scripts by line index, not by loose substring

A two-space-indented anchor once matched a continuation line inside a long `SKILLS_RATIONALE` string and the splice destroyed that definition and the one after it. When editing a generator script:

1. Locate the block by its assignment line (`next(i for i, l in enumerate(lines) if l.startswith("ROVE_B2 = ("))`).
2. Find the target line within a bounded window of that index.
3. Replace by index.
4. Verify with `python -c "import ast; ast.parse(open(path).read())"` before running.
