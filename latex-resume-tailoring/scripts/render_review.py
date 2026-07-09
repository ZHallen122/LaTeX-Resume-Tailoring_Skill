#!/usr/bin/env python3
"""Generate a self-contained HTML before/after review for tailored LaTeX resumes.

Reads the original resume plus one or more variant directories (each containing
resume.tex and changes.json), cross-validates the declared changes against the
actual textual diff, and writes a single HTML report with:

- side-by-side per-bullet before/after with word-level highlighting
- rationale, JD evidence, and risk tag for every declared change
- keyword coverage per variant, auto-verified against the variant text
- an "unexplained changes" section for any edit missing from the manifest

With --apply-decisions, first applies a Keep/Edit/Drop decisions JSON (exported
from the report) to its variant: dropped changes are reverted in resume.tex and
removed from changes.json, edited changes get their new wording in both files,
the variant is recompiled, and the report is re-rendered from the updated files.

Prints a JSON summary to stdout. Exit codes:
  0  report written, every change is explained by the manifest
  1  input error
  3  report written, but unexplained changes exist (fix the manifest or revert)
  4  decisions applied, but some were skipped as unsafe (see "applied" in the summary)
"""

from __future__ import annotations

import argparse
import base64
import difflib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# LaTeX parsing
# ---------------------------------------------------------------------------

SECTION_RE = re.compile(r"\\(?:sectionline|section\*?|subsection\*?|cvsection|resumeSection)\s*\{([^}]*)\}")
SKIP_LINE_RE = re.compile(
    r"^\s*(?:\\(?:vspace|hspace|smallskip|medskip|bigskip|newpage|pagebreak|hrule|par|small|normalsize|"
    r"begin|end|documentclass|usepackage|definecolor|hypersetup|pagestyle|setlength|setlist|"
    r"renewcommand|newcommand|geometry|titlespacing|titleformat|input|include|centering|raggedright)\b.*|[%{}\s\\\[\]]*)$"
)


def read_text(path: Path) -> str:
    # Normalize line endings so multi-line units stay exact substrings of the
    # text we diff, embed, and patch — regardless of CRLF files from Windows.
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_comments(tex: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", tex)


def latex_to_plain(fragment: str) -> str:
    """Best-effort LaTeX -> plain text for matching and display."""
    text = fragment
    text = re.sub(r"\\href\s*\{[^}]*\}\s*\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:textbf|textit|emph|texttt|underline|textsc|mbox)\s*\{([^{}]*)\}", r"\1", text)
    # second pass for one level of nesting
    text = re.sub(r"\\(?:textbf|textit|emph|texttt|underline|textsc|mbox)\s*\{([^{}]*)\}", r"\1", text)
    text = text.replace("\\textbar{}", "|").replace("\\textbar", "|")
    text = text.replace("\\&", "&").replace("\\%", "%").replace("\\$", "$").replace("\\#", "#").replace("\\_", "_")
    text = text.replace("~", " ").replace("``", '"').replace("''", '"')
    text = re.sub(r"\\\\(\[[^\]]*\])?", " ", text)
    text = re.sub(r"\$[^$]*\$", "", text)
    text = re.sub(r"\\[a-zA-Z]+\s*(\[[^\]]*\])?", " ", text)
    text = text.replace("}{", "} {")  # keep adjacent macro args readable: {A}{B} -> A B
    text = text.replace("{", "").replace("}", "").replace("&", " | ")
    return re.sub(r"\s+", " ", text).strip()


def normalize(fragment: str) -> str:
    return latex_to_plain(fragment).lower()


class Unit:
    """One comparable content unit: a bullet or a meaningful text line."""

    def __init__(self, section: str, kind: str, raw: str, order: int):
        self.section = section
        self.kind = kind  # "bullet" | "line"
        self.raw = raw.strip()
        self.plain = latex_to_plain(raw)
        self.norm = normalize(raw)
        self.order = order

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Unit {self.section}/{self.kind}: {self.plain[:40]!r}>"


def extract_units(tex: str) -> list[Unit]:
    body_match = re.search(r"\\begin\{document\}(.*)\\end\{document\}", tex, re.DOTALL)
    body = body_match.group(1) if body_match else tex
    body = strip_comments(body)

    units: list[Unit] = []
    section = "PREAMBLE"
    order = 0

    # Split into \item chunks and non-item lines, preserving order.
    token_re = re.compile(r"\\item\b", re.MULTILINE)
    pos = 0
    pieces: list[tuple[str, str]] = []  # (kind, text)
    lines_buffer: list[str] = []

    def flush_lines() -> None:
        nonlocal lines_buffer
        for line in "\n".join(lines_buffer).splitlines():
            pieces.append(("line", line))
        lines_buffer = []

    matches = list(token_re.finditer(body))
    for i, m in enumerate(matches):
        lines_buffer.append(body[pos : m.start()])
        flush_lines()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chunk = body[m.end() : end]
        stop = re.search(r"\\end\{itemize\}|\\end\{enumerate\}", chunk)
        if stop:
            bullet_text = chunk[: stop.start()]
            pieces.append(("bullet", bullet_text))
            lines_buffer.append(chunk[stop.start() :])
        else:
            pieces.append(("bullet", chunk))
        pos = end
    lines_buffer.append(body[pos:])
    flush_lines()

    # Consecutive prose lines merge into one paragraph unit so a sentence
    # wrapped across source lines is one comparable unit, not N false diffs.
    # A paragraph ends at: a hard line break (\\), a standalone macro call
    # (\educationEntry{..}{..} rows stay individual units), or any structural
    # boundary (blank/skip line, section command, bullet).
    para: list[str] = []

    def ends_with_hard_break(line: str) -> bool:
        return re.search(r"\\\\(\[[^\]]*\])?\s*$", line) is not None

    def is_standalone_macro(line: str) -> bool:
        s = line.strip()
        return s.startswith("\\") and s.endswith("}") and "{" in s and s.count("{") == s.count("}")

    def flush_para() -> None:
        nonlocal order
        if not para:
            return
        text = "\n".join(para)
        para.clear()
        if latex_to_plain(text):
            units.append(Unit(section, "line", text, order))
            order += 1

    for kind, text in pieces:
        if kind == "bullet":
            flush_para()
            if latex_to_plain(text):
                units.append(Unit(section, "bullet", text, order))
                order += 1
            continue
        sec = SECTION_RE.search(text)
        if sec:
            flush_para()
            section = latex_to_plain(sec.group(1)) or section
            continue
        if SKIP_LINE_RE.match(text) or not latex_to_plain(text):
            flush_para()
            continue
        para.append(text)
        if ends_with_hard_break(text) or is_standalone_macro(text):
            flush_para()
    flush_para()
    return units


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

RISK_LEVELS = ("verified", "adjacent", "needs-confirmation")


def sanitize_variant_name(name: str) -> str:
    """Variant names flow into HTML ids, inline JS handlers, and JS object keys.
    Restrict to a safe charset so no context can be escaped."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", str(name))[:60]


def load_manifest(path: Path) -> dict:
    data = json.loads(read_text(path))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: manifest must be a JSON object, got {type(data).__name__}")
    data.setdefault("meta", {})
    data.setdefault("jd_requirements", [])
    data.setdefault("changes", [])
    data.setdefault("keywords", [])
    if not isinstance(data["meta"], dict):
        raise ValueError(f'{path}: "meta" must be an object')
    if not isinstance(data["changes"], list) or not all(isinstance(c, dict) for c in data["changes"]):
        raise ValueError(f'{path}: "changes" must be a list of objects')
    if not isinstance(data["keywords"], list) or not all(isinstance(k, dict) for k in data["keywords"]):
        raise ValueError(f'{path}: "keywords" must be a list of objects')
    for idx, change in enumerate(data["changes"]):
        change.setdefault("type", "rewrite")
        change.setdefault("before", "")
        change.setdefault("after", "")
        change.setdefault("jd", [])
        change.setdefault("evidence", "")
        change.setdefault("rationale", "")
        change.setdefault("defense", "")
        change.setdefault("risk", "verified")
        change.setdefault("section", "")
        change["_id"] = idx
    return data


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def find_unit(units: list[Unit], text: str, used: set[int]) -> int | None:
    """Locate the unit best matching `text` (normalized), excluding used indices."""
    target = normalize(text)
    if not target:
        return None
    best, best_ratio = None, 0.0
    for i, unit in enumerate(units):
        if i in used:
            continue
        if unit.norm == target:
            return i
        ratio = similarity(unit.norm, target)
        if ratio > best_ratio:
            best, best_ratio = i, ratio
    return best if best_ratio >= 0.85 else None


# ---------------------------------------------------------------------------
# Pairing original vs variant units
# ---------------------------------------------------------------------------


def pair_variant(original: list[Unit], variant: list[Unit], manifest: dict) -> dict:
    """Return pairing info: explained changes, unchanged pairs, unexplained diffs."""
    used_orig: set[int] = set()
    used_var: set[int] = set()
    explained: list[dict] = []
    problems: list[str] = []

    for change in manifest["changes"]:
        entry = {"change": change, "orig_idx": None, "var_idx": None}
        ctype = change["type"]
        if ctype in {"rewrite", "reorder", "style", "remove"} and change["before"]:
            idx = find_unit(original, change["before"], used_orig)
            if idx is None:
                problems.append(
                    f'changes[{change["_id"]}] ({ctype}): "before" text not found in the original resume.'
                )
            else:
                used_orig.add(idx)
                entry["orig_idx"] = idx
        if ctype in {"rewrite", "reorder", "style", "add"} and change["after"]:
            idx = find_unit(variant, change["after"], used_var)
            if idx is None:
                problems.append(
                    f'changes[{change["_id"]}] ({ctype}): "after" text not found in the tailored resume.'
                )
            else:
                used_var.add(idx)
                entry["var_idx"] = idx
        if ctype == "remove" and change["before"]:
            leftover = find_unit(variant, change["before"], used_var)
            if leftover is not None:
                problems.append(
                    f'changes[{change["_id"]}] (remove): text still present in the tailored resume.'
                )
        explained.append(entry)

    # Pair remaining identical units (possibly reordered).
    unchanged: list[tuple[int, int]] = []
    var_by_norm: dict[str, list[int]] = {}
    for j, unit in enumerate(variant):
        if j not in used_var:
            var_by_norm.setdefault(unit.norm, []).append(j)
    for i, unit in enumerate(original):
        if i in used_orig:
            continue
        bucket = var_by_norm.get(unit.norm)
        if bucket:
            j = bucket.pop(0)
            used_orig.add(i)
            used_var.add(j)
            unchanged.append((i, j))

    # Fuzzy-pair the rest: these are edits nobody declared.
    unexplained: list[dict] = []
    remaining_orig = [i for i in range(len(original)) if i not in used_orig]
    remaining_var = [j for j in range(len(variant)) if j not in used_var]
    for i in remaining_orig:
        best_j, best_ratio = None, 0.0
        for j in remaining_var:
            ratio = similarity(original[i].norm, variant[j].norm)
            if ratio > best_ratio:
                best_j, best_ratio = j, ratio
        if best_j is not None and best_ratio >= 0.5:
            unexplained.append({"kind": "rewrite", "orig_idx": i, "var_idx": best_j})
            remaining_var.remove(best_j)
        else:
            unexplained.append({"kind": "remove", "orig_idx": i, "var_idx": None})
    for j in remaining_var:
        unexplained.append({"kind": "add", "orig_idx": None, "var_idx": j})

    return {
        "explained": explained,
        "unchanged": unchanged,
        "unexplained": unexplained,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# PDF preview rendering
# ---------------------------------------------------------------------------


def pdf_pages_to_data_uris(pdf_path: Path, dpi: int = 165, max_pages: int = 4) -> list[str]:
    """Render PDF pages to PNG data URIs via pdftoppm. Empty list when unavailable."""
    if not pdf_path.exists() or not shutil.which("pdftoppm"):
        return []
    with tempfile.TemporaryDirectory() as td:
        prefix = Path(td) / "page"
        proc = subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), "-l", str(max_pages), str(pdf_path), str(prefix)],
            capture_output=True,
        )
        if proc.returncode != 0:
            return []
        uris = []
        for png in sorted(Path(td).glob("page-*.png")):
            uris.append("data:image/png;base64," + base64.b64encode(png.read_bytes()).decode("ascii"))
        return uris


def find_variant_pdf(variant_dir: Path) -> Path:
    return variant_dir / "build" / "resume.pdf"


# ---------------------------------------------------------------------------
# Keyword auto-verification
# ---------------------------------------------------------------------------


def keyword_found(term: str, haystack: str) -> bool:
    """Whole-token match against normalized (lowercase) text. Plain substring
    matching lets short terms ride inside unrelated words — "Go" in
    "algorithms", "Java" in "JavaScript", "R" in almost anything — and fake a
    verified ✓. A match must not butt against an alphanumeric character, and
    must not be the bare prefix of a symbol-suffixed token ("C" in "C++"/"C#"
    is not evidence of C), while symbol terms themselves ("C++", "C#", ".NET",
    "Node.js") still match exactly."""
    t = term.lower().strip()
    if not t:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9+#])", haystack) is not None


def verify_keywords(manifest: dict, variant_units: list[Unit]) -> list[dict]:
    haystack = " ".join(u.norm for u in variant_units)
    rows = []
    for kw in manifest["keywords"]:
        term = str(kw.get("term", "")).strip()
        status = kw.get("status", "covered")
        found = keyword_found(term, haystack)
        rows.append(
            {
                "term": term,
                "status": status,
                "where": kw.get("where", ""),
                "found": found,
                "mismatch": status == "covered" and not found,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def word_diff(before: str, after: str) -> tuple[str, str]:
    """Word-level diff of two plain strings -> (before_html, after_html)."""
    a = before.split()
    b = after.split()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    left: list[str] = []
    right: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            left.append(esc(" ".join(a[i1:i2])))
            right.append(esc(" ".join(b[j1:j2])))
        elif op == "delete":
            left.append(f'<del>{esc(" ".join(a[i1:i2]))}</del>')
        elif op == "insert":
            right.append(f'<ins>{esc(" ".join(b[j1:j2]))}</ins>')
        else:
            left.append(f'<del>{esc(" ".join(a[i1:i2]))}</del>')
            right.append(f'<ins>{esc(" ".join(b[j1:j2]))}</ins>')
    return " ".join(left), " ".join(right)


RISK_LABEL = {
    "verified": ("verified", "risk-verified"),
    "adjacent": ("adjacent evidence", "risk-adjacent"),
    "needs-confirmation": ("needs confirmation", "risk-confirm"),
}

TYPE_LABEL = {
    "rewrite": "rewritten",
    "add": "added",
    "remove": "removed",
    "reorder": "reordered",
    "style": "style/fit",
}


def render_change_card(change: dict, original: list[Unit], variant: list[Unit], entry: dict, vname: str) -> str:
    before_plain = latex_to_plain(change["before"]) if change["before"] else ""
    after_plain = latex_to_plain(change["after"]) if change["after"] else ""
    if entry.get("orig_idx") is not None:
        before_plain = original[entry["orig_idx"]].plain
    if entry.get("var_idx") is not None:
        after_plain = variant[entry["var_idx"]].plain
    left, right = word_diff(before_plain, after_plain)

    risk = change.get("risk", "verified")
    risk_label, risk_class = RISK_LABEL.get(risk, (risk, "risk-adjacent"))
    jd_chips = "".join(f'<span class="chip">{esc(str(r))}</span>' for r in change.get("jd", []))
    type_label = TYPE_LABEL.get(change["type"], change["type"])

    meta_rows = []
    if change.get("rationale"):
        meta_rows.append(f'<div class="meta"><span class="k">Why</span>{esc(change["rationale"])}</div>')
    if change.get("evidence"):
        meta_rows.append(f'<div class="meta"><span class="k">Evidence</span>{esc(change["evidence"])}</div>')
    if change.get("defense"):
        meta_rows.append(f'<div class="meta"><span class="k">Interview defense</span>{esc(change["defense"])}</div>')

    after_view = '<p class="orig-view">{content}</p><p class="edited-view hidden"></p>'
    if change["type"] == "add":
        body = f'<div class="col only"><div class="lbl">Added</div>{after_view.format(content=right or esc(after_plain))}</div>'
    elif change["type"] == "remove":
        body = f'<div class="col only"><div class="lbl">Removed</div><p><del>{esc(before_plain)}</del></p></div>'
    else:
        body = (
            f'<div class="col"><div class="lbl">Before</div><p>{left or esc(before_plain)}</p></div>'
            f'<div class="col"><div class="lbl">After</div>{after_view.format(content=right or esc(after_plain))}</div>'
        )

    cid = change["_id"]
    # "remove" changes have no "after" text to fine-tune; Keep/Drop covers them.
    edit_btn = (
        f"""<button class="t-edit" onclick="toggleEdit('{esc(vname)}',{cid})">Edit</button>"""
        if change["type"] != "remove"
        else ""
    )
    editbox = (
        f"""
      <div class="editbox hidden" id="edit-{esc(vname)}-{cid}">
        <div class="lbl">Fine-tune this change (raw LaTeX — this exact text lands in the final .tex)</div>
        <textarea id="edit-ta-{esc(vname)}-{cid}" rows="3" spellcheck="false"></textarea>
        <div class="edit-actions">
          <button class="primary" onclick="saveEdit('{esc(vname)}',{cid})">Save edit</button>
          <button onclick="resetEdit('{esc(vname)}',{cid})">Reset to suggestion</button>
          <button onclick="cancelEdit('{esc(vname)}',{cid})">Cancel</button>
        </div>
      </div>"""
        if change["type"] != "remove"
        else ""
    )
    return f"""
    <article class="card" id="card-{esc(vname)}-{cid}" data-risk="{esc(risk)}">
      <header>
        <span class="badge type">{esc(type_label)}</span>
        <span class="badge {risk_class}">{esc(risk_label)}</span>
        <span class="section-name">{esc(change.get("section", ""))}</span>
        {jd_chips}
        <span class="toggle" role="group" aria-label="keep, edit, or drop this change">
          <button class="t-keep active" onclick="setDecision('{esc(vname)}',{cid},true)">Keep</button>
          {edit_btn}
          <button class="t-drop" onclick="setDecision('{esc(vname)}',{cid},false)">Drop</button>
        </span>
      </header>
      <div class="cols">{body}</div>{editbox}
      {"".join(meta_rows)}
    </article>"""


def render_unexplained_card(item: dict, original: list[Unit], variant: list[Unit]) -> str:
    before = original[item["orig_idx"]].plain if item["orig_idx"] is not None else ""
    after = variant[item["var_idx"]].plain if item["var_idx"] is not None else ""
    section = (
        original[item["orig_idx"]].section
        if item["orig_idx"] is not None
        else variant[item["var_idx"]].section
        if item["var_idx"] is not None
        else ""
    )
    left, right = word_diff(before, after)
    if item["kind"] == "add":
        body = f'<div class="col only"><div class="lbl">Added (undeclared)</div><p>{esc(after)}</p></div>'
    elif item["kind"] == "remove":
        body = f'<div class="col only"><div class="lbl">Removed (undeclared)</div><p><del>{esc(before)}</del></p></div>'
    else:
        body = (
            f'<div class="col"><div class="lbl">Before</div><p>{left}</p></div>'
            f'<div class="col"><div class="lbl">After</div><p>{right}</p></div>'
        )
    return f"""
    <article class="card unexplained-card">
      <header>
        <span class="badge risk-unexplained">unexplained</span>
        <span class="section-name">{esc(section)}</span>
      </header>
      <div class="cols">{body}</div>
      <div class="meta"><span class="k">Action</span>Declare this edit in changes.json with rationale + evidence, or revert it.</div>
    </article>"""


def render_preview_section(name: str, original_pages: list[str], variant_pages: list[str]) -> str:
    if not original_pages and not variant_pages:
        return ""
    figures = []
    if original_pages:
        imgs = "".join(f'<img src="{u}" alt="original page">' for u in original_pages)
        figures.append(f'<figure><figcaption>Original</figcaption>{imgs}</figure>')
    if variant_pages:
        imgs = "".join(f'<img src="{u}" alt="{esc(name)} page">' for u in variant_pages)
        figures.append(
            f'<figure><figcaption id="preview-cap-{esc(name)}">{esc(name)} (as generated)</figcaption>'
            f'<div id="preview-pages-{esc(name)}">{imgs}</div></figure>'
        )
    return f"""
    <h3 class="sec">Compiled preview</h3>
    <p class="preview-zoom-hint">Click any page to enlarge (Esc to close).</p>
    <div class="previews{' single' if len(figures) == 1 else ''}">{"".join(figures)}</div>"""


def render_variant_panel(
    name: str,
    manifest: dict,
    original: list[Unit],
    variant: list[Unit],
    pairing: dict,
    keywords: list[dict],
    original_pages: list[str] | None = None,
    variant_pages: list[str] | None = None,
) -> str:
    changes_html = []
    by_section: dict[str, list[str]] = {}
    for entry in pairing["explained"]:
        card = render_change_card(entry["change"], original, variant, entry, name)
        by_section.setdefault(entry["change"].get("section", "Other"), []).append(card)
    for section, cards in by_section.items():
        changes_html.append(f'<h3 class="sec">{esc(section)}</h3>' + "".join(cards))

    unexplained_html = ""
    if pairing["unexplained"]:
        cards = "".join(render_unexplained_card(i, original, variant) for i in pairing["unexplained"])
        unexplained_html = f"""
        <h3 class="sec danger">⚠ Unexplained changes ({len(pairing["unexplained"])})</h3>
        <p class="hint">These edits exist in the file but are missing from changes.json. They are exactly the
        kind of silent rewording this report is designed to catch.</p>
        {cards}"""

    problems_html = ""
    if pairing["problems"]:
        items = "".join(f"<li>{esc(p)}</li>" for p in pairing["problems"])
        problems_html = f'<div class="problems"><strong>Manifest problems</strong><ul>{items}</ul></div>'

    kw_rows = []
    for row in keywords:
        status = row["status"]
        cls = {"covered": "kw-ok", "adjacent": "kw-adj", "gap": "kw-gap"}.get(status, "")
        verified = "✓" if row["found"] else ("✗ not found in text" if row["mismatch"] else "—")
        kw_rows.append(
            f'<tr><td>{esc(row["term"])}</td><td class="{cls}">{esc(status)}</td>'
            f"<td>{esc(row['where'])}</td><td>{esc(verified)}</td></tr>"
        )
    kw_table = (
        f"""<h3 class="sec">JD keyword coverage</h3>
        <div class="tbl-wrap"><table>
        <thead><tr><th>Keyword</th><th>Status</th><th>Where</th><th>Verified in text</th></tr></thead>
        <tbody>{"".join(kw_rows)}</tbody></table></div>"""
        if kw_rows
        else ""
    )

    confirm_items = [
        c for c in manifest["changes"] if c.get("risk") == "needs-confirmation" and c["type"] != "remove"
    ]
    confirm_html = ""
    if confirm_items:
        rows = "".join(
            f'<li><label><input type="checkbox"> {esc(latex_to_plain(c["after"]) or latex_to_plain(c["before"]))}'
            f'<span class="defense">{esc(c.get("defense", ""))}</span></label></li>'
            for c in confirm_items
        )
        confirm_html = f"""
        <h3 class="sec">Confirm before submitting</h3>
        <p class="hint">Each claim below is plausible but not verified. Tick it only if you can say the
        defense line in an interview; otherwise ask for it to be removed.</p>
        <ul class="confirm">{rows}</ul>"""

    unchanged_rows = "".join(
        f"<li><strong>{esc(original[i].section)}</strong> — {esc(original[i].plain)}</li>"
        for i, _ in pairing["unchanged"]
        if original[i].kind == "bullet"
    )
    unchanged_html = (
        f"""<details class="unchanged"><summary>Unchanged bullets ({len(pairing["unchanged"])} units kept verbatim)</summary>
        <ul>{unchanged_rows}</ul></details>"""
        if pairing["unchanged"]
        else ""
    )

    n_changes = len(manifest["changes"])
    n_confirm = len(confirm_items)
    n_unexplained = len(pairing["unexplained"])
    stat_unexp = (
        f'<div class="stat bad"><div class="n">{n_unexplained}</div><div class="l">unexplained</div></div>'
        if n_unexplained
        else '<div class="stat good"><div class="n">0</div><div class="l">unexplained</div></div>'
    )
    summary = f"""
    <div class="stats">
      <div class="stat"><div class="n">{n_changes}</div><div class="l">declared changes</div></div>
      <div class="stat"><div class="n">{len(pairing["unchanged"])}</div><div class="l">kept verbatim</div></div>
      <div class="stat"><div class="n">{n_confirm}</div><div class="l">need confirmation</div></div>
      {stat_unexp}
    </div>"""

    unexplained_warn = (
        '<span class="ab-warn">⚠ unexplained changes present — they are always kept as-is</span>'
        if pairing["unexplained"]
        else ""
    )
    filterbar = (
        f"""
    <div class="filterbar" id="filterbar-{esc(name)}" role="group" aria-label="filter changes">
      <span class="fb-label">Show</span>
      <button class="fb active" data-f="all" onclick="setFilter('{esc(name)}','all')">All</button>
      <button class="fb" data-f="kept" onclick="setFilter('{esc(name)}','kept')">Kept</button>
      <button class="fb" data-f="edited" onclick="setFilter('{esc(name)}','edited')">Edited</button>
      <button class="fb" data-f="dropped" onclick="setFilter('{esc(name)}','dropped')">Dropped</button>
      <button class="fb" data-f="confirm" onclick="setFilter('{esc(name)}','confirm')">Needs confirmation</button>
    </div>
    <div class="filter-empty hidden" id="filter-empty-{esc(name)}">No changes match this filter.</div>"""
        if n_changes
        else ""
    )

    actionbar = f"""
    <div class="actionbar">
      <span class="ab-count" id="ab-count-{esc(name)}"></span>
      {unexplained_warn}
      <span class="ab-buttons">
        <button class="recompile-btn hidden" id="recompile-{esc(name)}"
          onclick="recompilePreview('{esc(name)}')">Recompile preview</button>
        <button class="primary apply-btn hidden" id="apply-{esc(name)}"
          onclick="applyFinal('{esc(name)}')"
          title="Write your Keep/Edit/Drop decisions into this variant's resume.tex and changes.json, recompile, and download the final PDF">Apply &amp; download final PDF</button>
        <button class="primary" id="copy-{esc(name)}" onclick="copyDecisions('{esc(name)}')">Copy decisions JSON</button>
        <button onclick="downloadFinal('{esc(name)}')"
          title="Advanced: raw LaTeX with your decisions applied in-browser — you compile it yourself; the files on disk are not updated">Download edited .tex</button>
        <button class="subtle" onclick="resetDecisions('{esc(name)}')" title="Reset every Keep/Edit/Drop selection for this variant">Reset</button>
      </span>
    </div>
    <details class="howto">
      <summary>How this review works</summary>
      <ol>
        <li><strong>Keep</strong> is the default — every suggested change starts accepted.</li>
        <li><strong>Drop</strong> a change to revert that bullet to the original wording;
            <strong>Edit</strong> lets you fine-tune the suggested wording instead (the box takes raw LaTeX).</li>
        <li>Your selections and edits are saved in this browser automatically.</li>
        <li>When you're done — with the live server running, <strong>Apply &amp; download final PDF</strong> writes your
            decisions into this variant's <code>resume.tex</code> and <code>changes.json</code>, recompiles, and downloads
            the submission-ready PDF. Without the server, <strong>Copy decisions JSON</strong> and paste it back to the
            agent to apply, compile, and re-review for you — or <strong>Download edited .tex</strong> if you prefer to
            compile the LaTeX yourself (this does not update the files on disk).</li>
      </ol>
    </details>
    {filterbar}"""

    preview_html = render_preview_section(name, original_pages or [], variant_pages or [])

    return f"""
    <section class="panel" id="panel-{esc(name)}">
      {summary}
      {actionbar}
      {problems_html}
      {unexplained_html}
      {"".join(changes_html)}
      {confirm_html}
      {kw_table}
      {preview_html}
      {unchanged_html}
    </section>"""


CSS = """
:root { --bg:#f4f5f7; --fg:#191c21; --muted:#68707b; --line:#e2e5ea; --card:#ffffff;
  --accent:#2557d6; --accent-fg:#ffffff; --accent-soft:#e9efff;
  --add-bg:#d9f3e3; --add-fg:#136a3c; --del-bg:#fce1e1; --del-fg:#96271f;
  --warn:#96650a; --warn-bg:#fcf0cf; --danger:#c02b22; --danger-bg:#fce1e1;
  --chip:#ecefff; --chip-fg:#3d43a8;
  --shadow:0 1px 2px rgba(18,22,30,.05), 0 1px 4px rgba(18,22,30,.05);
  --shadow-lg:0 10px 32px rgba(18,22,30,.14); }
@media (prefers-color-scheme: dark) { :root { --bg:#0e1116; --fg:#e7e9ec; --muted:#98a1ac;
  --line:#2a313b; --card:#171c23; --accent:#6d9bff; --accent-fg:#0e1116; --accent-soft:#1c2739;
  --add-bg:#173425; --add-fg:#84dfa7; --del-bg:#3a1b1b; --del-fg:#f3a29b;
  --warn:#e2b64c; --warn-bg:#322a12; --danger:#f27d72; --danger-bg:#3a1b1b;
  --chip:#232a4c; --chip-fg:#b9c1fa;
  --shadow:0 1px 2px rgba(0,0,0,.35);
  --shadow-lg:0 12px 36px rgba(0,0,0,.5); } }
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin:0; font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  background:var(--bg); color:var(--fg); -webkit-font-smoothing:antialiased; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 28px 20px 96px; }
button { transition: background-color .12s ease, border-color .12s ease, color .12s ease,
  box-shadow .12s ease, transform .06s ease; }
button:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
button:active { transform:translateY(1px); }
.masthead { margin-bottom:18px; }
.masthead .eyebrow { font-size:11px; font-weight:700; letter-spacing:.12em; text-transform:uppercase;
  color:var(--accent); margin-bottom:6px; }
h1 { font-size: 26px; line-height:1.25; margin: 0 0 6px; letter-spacing:-.01em; }
.sub { color: var(--muted); font-size: 13px; }
.sub a { color:var(--accent); }
.meta-files { margin-top:8px; font-size:13px; color:var(--muted); }
.meta-files summary { cursor:pointer; user-select:none; width:max-content; padding:3px 8px; margin-left:-8px;
  border-radius:6px; }
.meta-files summary:hover { background:var(--accent-soft); color:var(--accent); }
.meta-files .files { margin-top:6px; display:grid; gap:4px; word-break:break-all; }
.howto { margin:14px 0 4px; font-size:13px; color:var(--muted); background:var(--card);
  border:1px solid var(--line); border-radius:12px; padding:10px 14px; box-shadow:var(--shadow); }
.howto summary { cursor:pointer; user-select:none; font-weight:600; color:var(--fg); }
.howto ol { margin:8px 0 2px; padding-left:20px; }
.howto li { margin:4px 0; }
.tabs { display:flex; gap:8px; margin: 18px 0; flex-wrap: wrap; }
.tabs button { font:600 14px/1 inherit; padding:10px 18px; border-radius:999px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer; box-shadow:var(--shadow); }
.tabs button:hover { border-color:var(--accent); color:var(--accent); }
.tabs button .tab-n { font-weight:700; font-size:11px; background:var(--accent-soft); color:var(--accent);
  border-radius:999px; padding:2px 7px; margin-left:6px; }
.tabs button.active { background:var(--accent); border-color:var(--accent); color:var(--accent-fg); }
.tabs button.active .tab-n { background:rgba(255,255,255,.22); color:var(--accent-fg); }
.panel { display:none; } .panel.active { display:block; animation: fadein .18s ease; }
@keyframes fadein { from { opacity:0; transform:translateY(3px); } to { opacity:1; transform:none; } }
.stats { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 18px;
  min-width:120px; box-shadow:var(--shadow); }
.stat .n { font-size:24px; font-weight:750; letter-spacing:-.02em; } .stat .l { font-size:12px; color:var(--muted); }
.stat.bad { border-color:var(--danger); } .stat.bad .n { color:var(--danger); }
.stat.good .n { color:var(--add-fg); }
.sec { margin:30px 0 12px; font-size:13px; font-weight:700; text-transform:uppercase; letter-spacing:.08em;
  color:var(--muted); display:flex; align-items:center; gap:10px; }
.sec::after { content:""; flex:1; height:1px; background:var(--line); }
.sec.danger { color:var(--danger); }
.card { border:1px solid var(--line); border-left:3px solid var(--line); border-radius:12px; background:var(--card);
  padding:14px 16px; margin-bottom:12px; box-shadow:var(--shadow); transition:border-color .12s ease, opacity .15s ease; }
.card:hover { border-color:color-mix(in srgb, var(--accent) 45%, var(--line)); }
.card header { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
.badge { font-size:11px; font-weight:700; padding:3px 9px; border-radius:999px; text-transform:uppercase; letter-spacing:.03em; }
.badge.type { background:var(--chip); color:var(--chip-fg); }
.risk-verified { background:var(--add-bg); color:var(--add-fg); }
.risk-adjacent { background:var(--warn-bg); color:var(--warn); }
.risk-confirm { background:var(--warn-bg); color:var(--warn); box-shadow:inset 0 0 0 1px var(--warn); }
.risk-unexplained { background:var(--del-bg); color:var(--del-fg); }
.section-name { font-size:12px; color:var(--muted); }
.chip { font-size:11px; background:var(--chip); color:var(--chip-fg); border-radius:999px; padding:2px 8px; }
.cols { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.cols .only { grid-column: 1 / -1; }
@media (max-width: 760px) { .cols { grid-template-columns:1fr; } }
.col { background:var(--bg); border-radius:8px; padding:8px 10px; }
.col .lbl { font-size:10px; font-weight:700; color:var(--muted); text-transform:uppercase;
  letter-spacing:.06em; margin-bottom:3px; }
.col p { margin:0; }
del { background:var(--del-bg); color:var(--del-fg); text-decoration:line-through; border-radius:3px; padding:0 2px; }
ins { background:var(--add-bg); color:var(--add-fg); text-decoration:none; border-radius:3px; padding:0 2px; }
.meta { font-size:13px; margin-top:9px; color:var(--fg); }
.meta .k { display:inline-block; font-size:11px; font-weight:700; color:var(--muted); text-transform:uppercase;
  margin-right:8px; min-width:60px; }
.hint { font-size:13px; color:var(--muted); margin:4px 0 12px; }
.problems { border:1px solid var(--warn); background:var(--warn-bg); border-radius:10px; padding:10px 14px;
  margin-bottom:14px; font-size:13px; }
.tbl-wrap { overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px;
  box-shadow:var(--shadow); }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { text-align:left; padding:9px 14px; border-bottom:1px solid var(--line); }
tr:last-child td { border-bottom:0; }
tbody tr:hover { background:var(--bg); }
th { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
.kw-ok { color:var(--add-fg); font-weight:600; } .kw-adj { color:var(--warn); font-weight:600; }
.kw-gap { color:var(--danger); font-weight:600; }
.confirm { list-style:none; padding:0; } .confirm li { margin-bottom:10px; }
.confirm label { display:block; background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; cursor:pointer; box-shadow:var(--shadow); }
.confirm label:hover { border-color:var(--accent); }
.confirm .defense { display:block; font-size:12px; color:var(--muted); margin-top:4px; }
.unchanged { margin-top:24px; }
.unchanged summary { cursor:pointer; color:var(--muted); font-size:14px; }
.unchanged ul { font-size:13px; color:var(--muted); }
.unexplained-card { border-color: var(--danger); border-left-color: var(--danger); }
.toggle { margin-left:auto; display:inline-flex; border:1px solid var(--line); border-radius:999px;
  overflow:hidden; background:var(--bg); }
.toggle button { font:600 11px/1 inherit; padding:6px 13px; border:0; background:transparent;
  color:var(--muted); cursor:pointer; }
.toggle button:hover { color:var(--fg); }
.toggle .t-keep.active { background:var(--add-bg); color:var(--add-fg); }
.toggle .t-edit.active { background:var(--warn-bg); color:var(--warn); }
.toggle .t-drop.active { background:var(--del-bg); color:var(--del-fg); }
.card.kept { border-left-color: var(--add-fg); }
.card.dropped { opacity:.55; border-style:dashed; border-left-color:var(--danger); }
.card.dropped .cols, .card.dropped .meta { text-decoration:none; }
.card.edited { border-left-color:var(--warn); }
.card.filtered-out { display:none; }
.filter-empty { font-size:13px; color:var(--muted); background:var(--card); border:1px dashed var(--line);
  border-radius:10px; padding:12px 14px; margin-bottom:12px; }
.edited-view { margin:0; font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  background:var(--add-bg); color:var(--add-fg); border-radius:6px; padding:6px 8px;
  white-space:pre-wrap; word-break:break-word; }
.editbox { margin-top:10px; }
.editbox .lbl { font-size:11px; font-weight:700; color:var(--muted); text-transform:uppercase; margin-bottom:4px; }
.editbox textarea { width:100%; min-height:64px; font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  color:var(--fg); background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:8px 10px;
  resize:vertical; }
.editbox textarea:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
.edit-actions { display:flex; gap:8px; margin-top:8px; flex-wrap:wrap; }
.edit-actions button { font:600 12px/1 inherit; padding:7px 12px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer; }
.edit-actions button:hover { border-color:var(--accent); color:var(--accent); }
.edit-actions button.primary { background:var(--accent); border-color:var(--accent); color:var(--accent-fg); }
.edit-actions button.primary:hover { color:var(--accent-fg); filter:brightness(1.08); }
.actionbar { position:sticky; top:10px; z-index:10; display:flex; align-items:center; gap:12px; flex-wrap:wrap;
  background:var(--card); border:1px solid var(--line); border-radius:14px; padding:10px 14px; margin-bottom:8px;
  box-shadow:var(--shadow-lg); }
.ab-count { font-weight:700; font-size:14px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.ab-pill { font-size:11px; font-weight:700; border-radius:999px; padding:3px 9px; }
.ab-pill.keep { background:var(--add-bg); color:var(--add-fg); }
.ab-pill.edit { background:var(--warn-bg); color:var(--warn); }
.ab-pill.drop { background:var(--del-bg); color:var(--del-fg); }
.ab-warn { font-size:12px; color:var(--danger); }
.ab-buttons { margin-left:auto; display:flex; gap:8px; flex-wrap:wrap; }
.actionbar button { font:600 13px/1 inherit; padding:9px 14px; border-radius:9px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer; }
.actionbar button:hover { border-color:var(--accent); color:var(--accent); }
.actionbar button.primary { background:var(--accent); border-color:var(--accent); color:var(--accent-fg); }
.actionbar button.primary:hover { color:var(--accent-fg); filter:brightness(1.08); }
.actionbar button.subtle { border-color:transparent; color:var(--muted); }
.filterbar { display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin:12px 0 4px; font-size:12px; }
.filterbar .fb-label { color:var(--muted); font-weight:600; margin-right:2px; }
.filterbar .fb { font:600 12px/1 inherit; padding:6px 12px; border-radius:999px; border:1px solid var(--line);
  background:var(--card); color:var(--muted); cursor:pointer; }
.filterbar .fb:hover { color:var(--accent); border-color:var(--accent); }
.filterbar .fb.active { background:var(--accent); border-color:var(--accent); color:var(--accent-fg); }
code { background:var(--card); border:1px solid var(--line); border-radius:5px; padding:1px 5px; font-size:12px; }
.hidden { display:none !important; }
.previews { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.previews.single { grid-template-columns:minmax(0, 640px); justify-content:center; }
@media (max-width: 900px) { .previews { grid-template-columns:1fr; } }
.previews figure { margin:0; }
.previews figcaption { font-size:12px; font-weight:700; color:var(--muted); text-transform:uppercase; margin-bottom:6px; }
.previews img { width:100%; display:block; border:1px solid var(--line); border-radius:10px; background:#fff;
  box-shadow:var(--shadow); margin-bottom:10px; cursor:zoom-in; transition:box-shadow .15s ease; }
.previews img:hover { box-shadow:var(--shadow-lg); }
.preview-status { font-size:12px; margin-left:4px; }
.preview-status.bad { color:var(--danger); font-weight:700; }
.preview-zoom-hint { font-size:12px; color:var(--muted); margin:2px 0 10px; }
.lightbox { position:fixed; inset:0; z-index:100; background:rgba(10,12,14,.88); display:none; overflow:auto;
  padding:20px; }
.lightbox.open { display:block; }
.lightbox .lb-bar { position:sticky; top:0; z-index:101; display:flex; align-items:center; gap:12px;
  color:#fff; font-size:14px; margin-bottom:12px; }
.lightbox .lb-bar .lb-close { margin-left:auto; font:700 14px/1 inherit; padding:8px 16px; border-radius:999px;
  border:1px solid rgba(255,255,255,.4); background:transparent; color:#fff; cursor:pointer; }
.lightbox img { display:block; margin:0 auto 16px; width:min(1400px, 97vw); border-radius:6px;
  background:#fff; cursor:zoom-out; }
"""

JS = """
function showTab(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}

const DATA = JSON.parse(document.getElementById('report-data').textContent);
// vname -> { changeId -> true(keep) / false(drop) / "..."(keep with edited raw LaTeX) }
const decisions = {};
const storeKey = v => 'resume-review:' + location.pathname + ':' + v;

function loadDecisions(v) {
  decisions[v] = {};
  DATA.variants[v].changes.forEach(c => { decisions[v][c.id] = true; });
  try {
    const saved = JSON.parse(localStorage.getItem(storeKey(v)) || '{}');
    Object.keys(saved).forEach(k => {
      if (k in decisions[v] && (typeof saved[k] === 'boolean' || typeof saved[k] === 'string')) {
        decisions[v][k] = saved[k];
      }
    });
  } catch (e) {}
}

function persist(v) {
  try { localStorage.setItem(storeKey(v), JSON.stringify(decisions[v])); } catch (e) {}
}

function changeById(v, id) { return DATA.variants[v].changes.find(c => c.id === id); }

function setDecision(v, id, keep) {
  decisions[v][id] = keep;  // choosing Keep or Drop discards any saved edit
  persist(v);
  const box = document.getElementById('edit-' + v + '-' + id);
  if (box) box.classList.add('hidden');
  refresh(v);
}

function toggleEdit(v, id) {
  const box = document.getElementById('edit-' + v + '-' + id);
  if (!box) return;
  if (!box.classList.contains('hidden')) { box.classList.add('hidden'); return; }
  const dec = decisions[v][id];
  const ta = document.getElementById('edit-ta-' + v + '-' + id);
  ta.value = typeof dec === 'string' ? dec : changeById(v, id).var_raw;
  box.classList.remove('hidden');
  ta.focus();
}

function resetEdit(v, id) {
  document.getElementById('edit-ta-' + v + '-' + id).value = changeById(v, id).var_raw;
}

function cancelEdit(v, id) {
  document.getElementById('edit-' + v + '-' + id).classList.add('hidden');
}

function saveEdit(v, id) {
  const ta = document.getElementById('edit-ta-' + v + '-' + id);
  const text = ta.value.replace(/\\s+$/, '');
  if (!text.trim()) {
    alert('Edited text is empty — use Drop to revert to the original wording instead.');
    return;
  }
  // Saving text identical to the suggestion is just a Keep.
  decisions[v][id] = (text === changeById(v, id).var_raw) ? true : text;
  persist(v);
  cancelEdit(v, id);
  refresh(v);
}

const filters = {};  // vname -> 'all' | 'kept' | 'edited' | 'dropped' | 'confirm'

function setFilter(v, f) {
  filters[v] = f;
  const bar = document.getElementById('filterbar-' + v);
  if (bar) bar.querySelectorAll('.fb').forEach(b => b.classList.toggle('active', b.dataset.f === f));
  refresh(v);
}

function resetDecisions(v) {
  if (!confirm('Reset every Keep/Edit/Drop selection for this variant back to Keep?')) return;
  try { localStorage.removeItem(storeKey(v)); } catch (e) {}
  loadDecisions(v);
  document.querySelectorAll('#panel-' + CSS.escape(v) + ' .editbox').forEach(b => b.classList.add('hidden'));
  refresh(v);
}

function refresh(v) {
  const d = decisions[v];
  const f = filters[v] || 'all';
  let visible = 0;
  DATA.variants[v].changes.forEach(c => {
    const card = document.getElementById('card-' + v + '-' + c.id);
    if (!card) return;
    const dec = d[c.id];
    const edited = typeof dec === 'string';
    const kept = dec !== false && !edited;
    card.classList.toggle('kept', kept);
    card.classList.toggle('dropped', dec === false);
    card.classList.toggle('edited', edited);
    card.querySelector('.t-keep').classList.toggle('active', kept);
    card.querySelector('.t-drop').classList.toggle('active', dec === false);
    const te = card.querySelector('.t-edit');
    if (te) te.classList.toggle('active', edited);
    const ov = card.querySelector('.orig-view'), ev = card.querySelector('.edited-view');
    if (ov && ev) {
      ov.classList.toggle('hidden', edited);
      ev.classList.toggle('hidden', !edited);
      if (edited) ev.textContent = dec;
    }
    const show = f === 'all'
      || (f === 'kept' && kept)
      || (f === 'edited' && edited)
      || (f === 'dropped' && dec === false)
      || (f === 'confirm' && c.risk === 'needs-confirmation');
    card.classList.toggle('filtered-out', !show);
    if (show) visible++;
  });
  const empty = document.getElementById('filter-empty-' + v);
  if (empty) empty.classList.toggle('hidden', visible > 0 || f === 'all');
  const vals = Object.values(d);
  const total = DATA.variants[v].changes.length;
  const edited = vals.filter(x => typeof x === 'string').length;
  const dropped = vals.filter(x => x === false).length;
  const kept = total - edited - dropped;
  const el = document.getElementById('ab-count-' + v);
  if (el) {
    el.innerHTML = 'Keeping ' + (total - dropped) + ' of ' + total +
      ' <span class="ab-pill keep">' + kept + ' keep</span>' +
      (edited ? ' <span class="ab-pill edit">' + edited + ' edited</span>' : '') +
      (dropped ? ' <span class="ab-pill drop">' + dropped + ' dropped</span>' : '');
  }
}

function escRe(s) { return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'); }

function buildFinal(v) {
  const vd = DATA.variants[v];
  let text = vd.variant_text;
  const warnings = [];
  vd.changes.forEach(c => {
    const dec = decisions[v][c.id];
    if (typeof dec === 'string') {  // edited -> swap the suggested text for the fine-tuned wording
      const occurrences = c.var_raw ? text.split(c.var_raw).length - 1 : 0;
      if (occurrences === 1) text = text.replace(c.var_raw, () => dec);
      else if (occurrences > 1) {
        warnings.push('Change #' + c.id + ': text appears ' + occurrences + ' times — edit skipped to avoid rewriting the wrong bullet. Paste the decisions JSON to the agent instead.');
      } else warnings.push('Could not apply the edit for change #' + c.id + ' — ask the agent to apply this.');
      return;
    }
    if (dec !== false) return;  // kept -> leave as-is
    if (c.type === 'add') {
      const re = new RegExp('\\\\\\\\item\\\\s*' + escRe(c.var_raw) + '[ \\\\t]*\\\\n?');
      if (c.kind === 'bullet' && re.test(text)) text = text.replace(re, '');
      else if (text.includes(c.var_raw)) text = text.replace(c.var_raw + '\\n', '').replace(c.var_raw, '');
      else warnings.push('Could not remove added text for change #' + c.id);
    } else if (c.type === 'remove') {
      const anchorCount = c.prev_raw ? text.split(c.prev_raw).length - 1 : 0;
      if (anchorCount === 1) {
        const insert = (c.kind === 'bullet' ? '\\n  \\\\item ' : '\\n') + c.orig_raw;
        text = text.replace(c.prev_raw, () => c.prev_raw + insert);
      } else if (anchorCount > 1) {
        warnings.push('Change #' + c.id + ': anchor text appears ' + anchorCount + ' times — restore skipped to avoid inserting at the wrong spot. Paste the decisions JSON to the agent instead.');
      } else warnings.push('Could not restore removed text for change #' + c.id + ' — ask the agent to apply this.');
    } else {
      const occurrences = c.var_raw ? text.split(c.var_raw).length - 1 : 0;
      if (occurrences === 1) text = text.replace(c.var_raw, () => c.orig_raw);
      else if (occurrences > 1) {
        warnings.push('Change #' + c.id + ': text appears ' + occurrences + ' times — revert skipped to avoid rewriting the wrong bullet. Paste the decisions JSON to the agent instead.');
      } else warnings.push('Could not revert change #' + c.id + ' — ask the agent to apply this.');
    }
  });
  return { text, warnings };
}

function downloadFinal(v) {
  const { text, warnings } = buildFinal(v);
  if (warnings.length) alert('Heads up:\\n' + warnings.join('\\n'));
  const blob = new Blob([text], { type: 'application/x-tex' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const kept = Object.values(decisions[v]).filter(x => x !== false).length;
  a.download = 'resume-' + v + '-final-' + kept + 'of' + DATA.variants[v].changes.length + '.tex';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function decisionsJson(v) {
  const d = decisions[v];
  const row = c => ({ id: c.id, section: c.section, summary: c.plain.slice(0, 90) });
  const kept = [], dropped = [], edited = [];
  DATA.variants[v].changes.forEach(c => {
    const dec = d[c.id];
    if (dec === false) dropped.push(row(c));
    else if (typeof dec === 'string') edited.push(Object.assign(row(c), { new_latex: dec }));
    else kept.push(row(c));
  });
  return JSON.stringify({ variant: v, source_report: location.pathname,
    kept, dropped, edited }, null, 2);
}

function copyDecisions(v) {
  const json = decisionsJson(v);
  const done = () => alert('Decisions JSON copied — paste it back to the agent to apply and recompile.');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(json).then(done, () => window.prompt('Copy the decisions JSON:', json));
  } else {
    window.prompt('Copy the decisions JSON:', json);
  }
}

// Lightbox: click any preview page to view it near-fullscreen. Event delegation so
// images swapped in by live recompile stay zoomable.
function ensureLightbox() {
  let lb = document.getElementById('lightbox');
  if (lb) return lb;
  lb = document.createElement('div');
  lb.id = 'lightbox';
  lb.className = 'lightbox';
  lb.innerHTML = '<div class="lb-bar"><span id="lb-caption"></span>' +
    '<button class="lb-close" onclick="closeLightbox()">Close ✕ (Esc)</button></div>' +
    '<img id="lb-img" alt="preview enlarged">';
  lb.addEventListener('click', e => { if (e.target.id !== 'lb-caption') closeLightbox(); });
  document.body.appendChild(lb);
  return lb;
}

function openLightbox(img) {
  const lb = ensureLightbox();
  document.getElementById('lb-img').src = img.src;
  const fig = img.closest('figure');
  const cap = fig ? fig.querySelector('figcaption') : null;
  document.getElementById('lb-caption').textContent = cap ? cap.textContent : '';
  lb.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeLightbox() {
  const lb = document.getElementById('lightbox');
  if (lb) lb.classList.remove('open');
  document.body.style.overflow = '';
}

document.addEventListener('click', e => {
  if (e.target.tagName === 'IMG' && e.target.closest('.previews')) openLightbox(e.target);
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });

// Live recompile + apply: only possible when the report is served by render_review.py --serve.
if (location.protocol === 'http:' || location.protocol === 'https:') {
  document.querySelectorAll('.recompile-btn, .apply-btn').forEach(b => b.classList.remove('hidden'));
  // With Apply available it is the one primary action; Copy decisions JSON steps back.
  document.querySelectorAll('[id^="copy-"]').forEach(b => b.classList.remove('primary'));
}

function collectDecisions(v) {
  const dropped = [], edits = {};
  DATA.variants[v].changes.forEach(c => {
    const dec = decisions[v][c.id];
    if (dec === false) dropped.push(c.id);
    else if (typeof dec === 'string') edits[c.id] = dec;
  });
  return { dropped, edits };
}

async function applyFinal(v) {
  const { dropped, edits } = collectDecisions(v);
  const summary = dropped.length + ' dropped, ' + Object.keys(edits).length + ' edited';
  if (!confirm('Apply your decisions (' + summary + ') to the files on disk?\\n\\n' +
      'This updates resume.tex and changes.json for "' + v + '", recompiles, and downloads the final PDF.\\n' +
      'Dropped changes are reverted permanently in that variant.')) return;
  const btn = document.getElementById('apply-' + v);
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = 'Applying…';
  try {
    const res = await fetch('/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Review-Token': window.__REVIEW_TOKEN__ || '' },
      body: JSON.stringify({ variant: v, dropped, edits })
    });
    const j = await res.json();
    if (!j.ok) { alert('Apply failed:\\n' + (j.error || 'unknown error')); return; }
    const changed = j.applied && (j.applied.dropped_applied || j.applied.edited_applied);
    if (j.pdf) {
      const bytes = Uint8Array.from(atob(j.pdf), ch => ch.charCodeAt(0));
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
      a.download = 'resume-' + v + '-final.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
    let msg = 'Decisions applied to disk (' + j.applied.dropped_applied + ' dropped, '
      + j.applied.edited_applied + ' edited).';
    if (j.pages != null) {
      msg += ' Compiled to ' + j.pages + ' page' + (j.pages === 1 ? '' : 's')
        + (j.within_limit === false ? ' — over the page limit!' : '.');
    }
    if (j.compile_error) msg += '\\n\\nCompile problem:\\n' + j.compile_error;
    if (!j.pdf) msg += '\\n\\nNo PDF was produced — check the LaTeX toolchain, or ask the agent to compile.';
    if (j.warnings && j.warnings.length) msg += '\\n\\nWarnings:\\n' + j.warnings.join('\\n');
    // Only wipe saved selections + reload when something was actually written to
    // disk; otherwise the user keeps their Keep/Edit/Drop work to try again.
    if (changed) {
      msg += '\\n\\nThe page will now reload to show the applied state.';
      alert(msg);
      try { localStorage.removeItem(storeKey(v)); } catch (e) {}
      location.reload();
    } else {
      msg += '\\n\\nNothing was applied, so your selections are unchanged.';
      alert(msg);
    }
  } catch (e) {
    alert('Apply request failed — is the --serve process still running?\\n' + e);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

async function recompilePreview(v) {
  const btn = document.getElementById('recompile-' + v);
  const { dropped, edits } = collectDecisions(v);
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = 'Compiling…';
  try {
    const res = await fetch('/recompile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Review-Token': window.__REVIEW_TOKEN__ || '' },
      body: JSON.stringify({ variant: v, dropped, edits })
    });
    const j = await res.json();
    if (!j.ok) { alert('Compile failed:\\n' + (j.error || 'unknown error')); return; }
    const pagesEl = document.getElementById('preview-pages-' + v);
    if (pagesEl) {
      pagesEl.innerHTML = j.pages.map(u => '<img src="' + u + '" alt="preview page">').join('');
    }
    const cap = document.getElementById('preview-cap-' + v);
    if (cap) {
      const kept = Object.values(decisions[v]).filter(x => x !== false).length;
      const total = DATA.variants[v].changes.length;
      let status = j.page_count + ' page' + (j.page_count === 1 ? '' : 's');
      const bad = j.within_limit === false;
      cap.innerHTML = v + ' — live preview, keeping ' + kept + ' of ' + total +
        ' <span class="preview-status' + (bad ? ' bad' : '') + '">' +
        (bad ? '⚠ ' + status + ' (over limit)' : '✓ ' + status) + '</span>';
    }
  } catch (e) {
    alert('Recompile request failed — is the --serve process still running?\\n' + e);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

Object.keys(DATA.variants).forEach(v => { loadDecisions(v); });
"""


def build_report_data(original: list[Unit], variants: list[dict]) -> dict:
    """Data blob embedded in the report so the browser can build a final .tex
    by reverting dropped changes from the variant text."""
    data = {"variants": {}}
    for v in variants:
        changes = []
        for entry in v["pairing"]["explained"]:
            change = entry["change"]
            oi, vi = entry.get("orig_idx"), entry.get("var_idx")
            orig_raw = original[oi].raw if oi is not None else change["before"].strip()
            var_raw = v["units"][vi].raw if vi is not None else change["after"].strip()
            kind = (v["units"][vi].kind if vi is not None else original[oi].kind if oi is not None else "bullet")
            prev_raw = original[oi - 1].raw if (change["type"] == "remove" and oi) else ""
            changes.append(
                {
                    "id": change["_id"],
                    "type": change["type"],
                    "section": change.get("section", ""),
                    "risk": change.get("risk", "verified"),
                    "kind": kind,
                    "orig_raw": orig_raw,
                    "var_raw": var_raw,
                    "prev_raw": prev_raw,
                    "plain": latex_to_plain(change["after"] or change["before"]),
                }
            )
        data["variants"][v["name"]] = {"variant_text": read_text(v["tex"]), "changes": changes}
    return data


def render_report(
    original_path: Path,
    original: list[Unit],
    variants: list[dict],
    out_path: Path,
    original_pages: list[str] | None = None,
) -> None:
    first = variants[0]["manifest"]["meta"]
    jd_title = first.get("jd_title", "") or first.get("label", "")
    jd_url = first.get("jd_url", "")
    generated = first.get("generated", "")

    tabs = []
    panels = []
    for v in variants:
        name = v["name"]
        n = len(v["manifest"]["changes"])
        tabs.append(
            f'<button id="tab-{esc(name)}" onclick="showTab(\'{esc(name)}\')">{esc(name)}'
            f'<span class="tab-n">{n} change{"s" if n != 1 else ""}</span></button>'
        )
        panels.append(
            render_variant_panel(
                name,
                v["manifest"],
                original,
                v["units"],
                v["pairing"],
                v["keywords"],
                original_pages=original_pages,
                variant_pages=v.get("preview_pages"),
            )
        )

    variant_paths = "".join(
        f'<div>{esc(v["name"])}: <code>{esc(str(v["tex"]))}</code></div>' for v in variants
    )
    jd_line = f' · JD: <a href="{esc(jd_url)}">{esc(jd_url)}</a>' if jd_url else ""
    generated_line = f"Generated {esc(generated)}" if generated else "Before/after review of every tailored change"

    data_json = json.dumps(build_report_data(original, variants)).replace("</", "<\\/")

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Resume tailoring review — {esc(jd_title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <div class="eyebrow">Resume tailoring review</div>
    <h1>{esc(jd_title) if jd_title else "Tailored resume changes"}</h1>
    <div class="sub">{generated_line}{jd_line}</div>
    <details class="meta-files">
      <summary>Source files</summary>
      <div class="files">
        <div>Original (unchanged): <code>{esc(str(original_path))}</code></div>
        {variant_paths}
      </div>
    </details>
  </header>
  <div class="tabs{" hidden" if len(variants) == 1 else ""}">{"".join(tabs)}</div>
  {"".join(panels)}
</div>
<script type="application/json" id="report-data">{data_json}</script>
<script>{JS}
showTab('{esc(variants[0]["name"])}');
Object.keys(DATA.variants).forEach(v => refresh(v));</script>
</body>
</html>"""
    # Atomic write: --serve rewrites this file on /apply while GETs may be
    # reading it concurrently; a rename never exposes a half-written page.
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    tmp_path.write_text(page, encoding="utf-8")
    tmp_path.replace(out_path)


# ---------------------------------------------------------------------------
# Live preview server (--serve)
# ---------------------------------------------------------------------------


def build_final_text(
    variant_text: str,
    changes: list[dict],
    dropped_ids: set[int],
    edits: dict[int, str] | None = None,
) -> tuple[str, list[str], set[int]]:
    """Python twin of the in-browser buildFinal(): revert dropped changes and
    swap in user-edited wording (`edits`: change id -> replacement raw LaTeX).
    Returns (text, warnings, failed_ids); failed_ids are the change ids whose
    drop/edit was skipped as unsafe (ambiguous or missing text)."""
    text = variant_text
    warnings: list[str] = []
    failed: set[int] = set()
    edits = edits or {}
    for c in changes:
        cid = c["id"]
        if cid not in dropped_ids and cid in edits:
            var_raw = c["var_raw"]
            occurrences = text.count(var_raw) if var_raw else 0
            if occurrences == 1:
                text = text.replace(var_raw, edits[cid], 1)
            elif occurrences > 1:
                failed.add(cid)
                warnings.append(
                    f"Change #{cid}: text appears {occurrences} times — edit skipped to avoid rewriting the wrong bullet; ask the agent to apply it."
                )
            else:
                failed.add(cid)
                warnings.append(f"Could not apply the edit for change #{cid}")
            continue
        if cid not in dropped_ids:
            continue
        ctype, var_raw, orig_raw = c["type"], c["var_raw"], c["orig_raw"]
        if ctype == "add":
            pattern = re.compile(r"\\item\s*" + re.escape(var_raw) + r"[ \t]*\n?")
            if c["kind"] == "bullet" and pattern.search(text):
                text = pattern.sub("", text, count=1)
            elif var_raw in text:
                text = text.replace(var_raw + "\n", "", 1) if var_raw + "\n" in text else text.replace(var_raw, "", 1)
            else:
                failed.add(cid)
                warnings.append(f"Could not remove added text for change #{cid}")
        elif ctype == "remove":
            prev_raw = c.get("prev_raw", "")
            occurrences = text.count(prev_raw) if prev_raw else 0
            if occurrences == 1:
                insert = ("\n  \\item " if c["kind"] == "bullet" else "\n") + orig_raw
                text = text.replace(prev_raw, prev_raw + insert, 1)
            elif occurrences > 1:
                failed.add(cid)
                warnings.append(
                    f"Change #{cid}: anchor text appears {occurrences} times — restore skipped to avoid inserting at the wrong spot; ask the agent to apply it."
                )
            else:
                failed.add(cid)
                warnings.append(f"Could not restore removed text for change #{cid}")
        else:
            occurrences = text.count(var_raw) if var_raw else 0
            if occurrences == 1:
                text = text.replace(var_raw, orig_raw, 1)
            elif occurrences > 1:
                failed.add(cid)
                warnings.append(
                    f"Change #{cid}: text appears {occurrences} times — revert skipped to avoid rewriting the wrong bullet; ask the agent to apply it."
                )
            else:
                failed.add(cid)
                warnings.append(f"Could not revert change #{cid}")
    return text, warnings, failed


def parse_decisions(path: Path) -> dict:
    """Parse a decisions JSON exported by the report's "Copy decisions JSON" button.
    Accepts entries as objects with an "id" (the exported shape) or as bare ids."""
    data = json.loads(read_text(path))
    if not isinstance(data, dict):
        raise ValueError("decisions file must be a JSON object")

    def ids_of(key: str) -> set[int]:
        out = set()
        for item in data.get(key) or []:
            out.add(int(item["id"] if isinstance(item, dict) else item))
        return out

    edits: dict[int, str] = {}
    for item in data.get("edited") or []:
        if not isinstance(item, dict) or "id" not in item or "new_latex" not in item:
            raise ValueError('every "edited" entry must be an object with "id" and "new_latex"')
        text = str(item["new_latex"]).rstrip()
        if not text.strip():
            raise ValueError(f'edited change #{item["id"]}: "new_latex" is empty — drop the change instead')
        edits[int(item["id"])] = text
    return {
        "variant": sanitize_variant_name(data.get("variant", "")),
        "dropped": ids_of("dropped"),
        "edits": edits,
    }


def apply_decisions(variant_dir: Path, vdata: dict, dropped: set[int], edits: dict[int, str]) -> dict:
    """Apply Keep/Edit/Drop decisions to <variant_dir>/resume.tex and changes.json.

    Dropped changes are reverted in the .tex and their manifest entries removed;
    edited changes get their new wording in the .tex and in the entry's "after".
    Unsafe applications (ambiguous or missing text) are skipped and reported —
    their manifest entries stay untouched so re-validation still passes."""
    changes = vdata["changes"]
    known_ids = {c["id"] for c in changes}
    unknown = sorted((dropped | set(edits)) - known_ids)
    if unknown:
        raise ValueError(f"decisions reference unknown change ids: {unknown}")
    # Drop wins over edit for the same id, matching the browser/server behavior.
    edits = {cid: t for cid, t in edits.items() if cid not in dropped}

    text, warnings, failed = build_final_text(vdata["variant_text"], changes, dropped, edits)
    applied_drops = sorted(dropped - failed)
    applied_edits = sorted(set(edits) - failed)

    if applied_drops or applied_edits:
        tex_path = variant_dir / "resume.tex"
        manifest_path = variant_dir / "changes.json"
        # This edit is destructive (drops are irreversible in the file). Keep a
        # one-deep backup of both files so a bad edit or a mid-write failure is
        # recoverable, and compute the new manifest before touching either file
        # so we never leave a half-written pair on disk.
        raw = json.loads(read_text(manifest_path))
        # Change ids are positions in the manifest's changes list (see load_manifest),
        # so update edits by index first, then delete drops in descending order.
        for cid in applied_edits:
            raw["changes"][cid]["after"] = edits[cid]
        for cid in sorted(applied_drops, reverse=True):
            del raw["changes"][cid]
        manifest_text = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
        shutil.copy2(tex_path, tex_path.with_suffix(tex_path.suffix + ".bak"))
        shutil.copy2(manifest_path, manifest_path.with_suffix(manifest_path.suffix + ".bak"))
        tex_path.write_text(text, encoding="utf-8")
        manifest_path.write_text(manifest_text, encoding="utf-8")

    return {
        "dropped_applied": len(applied_drops),
        "edited_applied": len(applied_edits),
        "skipped": sorted(failed),
        "warnings": warnings,
    }


def recompile_variant(variant_dir: Path, max_pages: int) -> dict:
    """Best-effort recompile of <variant_dir>/resume.tex into build/ after an apply,
    so the re-rendered report embeds a preview of the final file."""
    if not (shutil.which("latexmk") or shutil.which("pdflatex")):
        return {"compiled": False, "skipped": "no LaTeX toolchain (latexmk/pdflatex) found"}
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_latex_resume import compile_latex, count_pdf_pages  # noqa: E402

    compiled, log, pdf_path = compile_latex(variant_dir / "resume.tex", variant_dir / "build", "auto")
    if not compiled:
        return {"compiled": False, "error": "\n".join(log.splitlines()[-15:])}
    pages = count_pdf_pages(pdf_path)
    return {
        "compiled": True,
        "pdf": str(pdf_path),
        "pages": pages,
        "within_limit": pages is not None and pages <= max_pages,
    }


def compile_preview(variant_dir: Path, tex_text: str, max_pages: int) -> dict:
    """Compile tex_text in <variant_dir>/preview-build and rasterize the PDF."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_latex_resume import compile_latex, count_pdf_pages  # noqa: E402

    build_dir = variant_dir / "preview-build"
    build_dir.mkdir(parents=True, exist_ok=True)
    # Copy sibling assets (cls/sty/images) the template might need, but never build outputs.
    for item in variant_dir.iterdir():
        if item.is_file() and item.suffix.lower() not in {".tex", ".json", ".pdf", ".log", ".aux"}:
            shutil.copy2(item, build_dir / item.name)
    tex_path = build_dir / "resume.tex"
    tex_path.write_text(tex_text, encoding="utf-8")
    compiled, log, pdf_path = compile_latex(tex_path, build_dir, "auto")
    if not compiled:
        return {"ok": False, "error": "LaTeX compile failed:\n" + "\n".join(log.splitlines()[-15:])}
    pages = count_pdf_pages(pdf_path)
    uris = pdf_pages_to_data_uris(pdf_path)
    if not uris:
        return {"ok": False, "error": "Compiled, but could not rasterize the PDF (is pdftoppm installed?)"}
    return {
        "ok": True,
        "pages": uris,
        "page_count": pages,
        "within_limit": (pages is not None and pages <= max_pages),
    }


def serve_report(
    report_path: Path,
    variants: list[dict],
    report_data: dict,
    port: int,
    max_pages: int,
    apply_cb=None,
) -> None:
    import hmac
    import http.server
    import secrets
    import threading
    from urllib.parse import urlsplit

    variant_dirs = {v["name"]: v["dir"] for v in variants}
    # LaTeX compiles share build dirs; one at a time.
    compile_lock = threading.Lock()

    # Per-process secret. /recompile and especially /apply have side effects
    # (LaTeX compile; for /apply, writing the user's resume.tex + changes.json).
    # Host/Origin string checks alone are spoofable ("localhost.evil.com"), so
    # every mutating request must echo this token. A cross-origin page cannot
    # read the loopback GET response, so it never learns the token and its
    # forged POST is rejected. The token is injected into the page at GET time,
    # never written to the on-disk report.
    session_token = secrets.token_urlsafe(32)
    token_script = f'<script>window.__REVIEW_TOKEN__ = "{session_token}";</script>'.encode("utf-8")

    def host_is_loopback(host_header: str) -> bool:
        # Host is "hostname[:port]"; require an exact loopback hostname, not a
        # prefix ("127.0.0.1.evil.com" and "localhost.evil.com" must fail).
        hostname = urlsplit("//" + host_header).hostname or ""
        return hostname in {"127.0.0.1", "localhost", "::1"}

    def origin_is_loopback(origin_header: str) -> bool:
        if not origin_header:
            return True  # same-origin requests may omit Origin
        return (urlsplit(origin_header).hostname or "") in {"127.0.0.1", "localhost", "::1"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in {"/", "/review.html", "/index.html"}:
                html_bytes = report_path.read_bytes()
                # Inject the session token just before </body> so the page's JS
                # can echo it back on mutating POSTs.
                marker = b"</body>"
                idx = html_bytes.rfind(marker)
                if idx != -1:
                    html_bytes = html_bytes[:idx] + token_script + html_bytes[idx:]
                self._send(200, html_bytes, "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path not in {"/recompile", "/apply"}:
                self._send(404, b"not found", "text/plain")
                return
            # Loopback-only hardening: a hostile web page can fire a cross-origin
            # POST whose side effect (a LaTeX compile, or for /apply a file write)
            # still runs even though the response is unreadable. Reject anything
            # not clearly from this report.
            origin = self.headers.get("Origin", "")
            host = self.headers.get("Host", "")
            if not host_is_loopback(host):
                self._send(403, b'{"ok": false, "error": "bad host"}', "application/json")
                return
            if not origin_is_loopback(origin):
                self._send(403, b'{"ok": false, "error": "cross-origin request rejected"}', "application/json")
                return
            # The token is the real gate: only a page served by this process
            # (which can read the GET response) knows it.
            sent_token = self.headers.get("X-Review-Token", "")
            if not hmac.compare_digest(sent_token, session_token):
                self._send(403, b'{"ok": false, "error": "missing or invalid session token"}', "application/json")
                return
            # Require a JSON content type. A cross-origin "simple request" can only
            # set text/plain without triggering a (here unanswered) CORS preflight,
            # so this rejects the no-preflight forgery path outright.
            if not self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json":
                self._send(415, b'{"ok": false, "error": "expected application/json"}', "application/json")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1_000_000:
                    self._send(413, b'{"ok": false, "error": "invalid request size"}', "application/json")
                    return
                req = json.loads(self.rfile.read(length))
                name = req["variant"]
                dropped = set(int(i) for i in req.get("dropped", []))
                edits = {int(k): str(t) for k, t in dict(req.get("edits", {})).items()}
                if self.path == "/apply":
                    if apply_cb is None:
                        result = {"ok": False, "error": "apply is not available in this server"}
                    else:
                        with compile_lock:
                            result = apply_cb(name, dropped, edits)
                else:
                    vdata = report_data["variants"][name]
                    text, warnings, _failed = build_final_text(vdata["variant_text"], vdata["changes"], dropped, edits)
                    with compile_lock:
                        result = compile_preview(variant_dirs[name], text, max_pages)
                    if warnings:
                        result["warnings"] = warnings
            except Exception as exc:  # surface errors to the page instead of a broken response
                result = {"ok": False, "error": str(exc)}
            self._send(200, json.dumps(result).encode("utf-8"), "application/json")

    # port 0 → the OS assigns a free port; read the real one back after bind
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_address[1]
    print(f"Live preview server: http://127.0.0.1:{actual_port}/  (Ctrl+C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render an HTML before/after review from variant dirs with changes.json manifests."
    )
    parser.add_argument("--original", type=Path, required=True, help="Path to the untouched source resume .tex")
    parser.add_argument(
        "--variant",
        type=Path,
        action="append",
        required=True,
        help="Variant directory containing resume.tex and changes.json. Repeatable.",
    )
    parser.add_argument("--out", type=Path, default=None, help="Output HTML path (default: <first variant>/../review.html)")
    parser.add_argument(
        "--original-pdf",
        type=Path,
        default=None,
        help="Compiled PDF of the original resume for the side-by-side preview (default: auto-detect <orig-dir>/build/<stem>.pdf)",
    )
    parser.add_argument("--max-pages", type=int, default=1, help="Page limit used by the live preview status")
    parser.add_argument(
        "--apply-decisions",
        type=Path,
        default=None,
        metavar="DECISIONS_JSON",
        help="Apply a Keep/Edit/Drop decisions JSON (exported from the report) to its variant before "
        "rendering: revert dropped changes, swap in edited wording, sync changes.json, and recompile.",
    )
    parser.add_argument(
        "--serve",
        type=int,
        nargs="?",
        const=0,
        default=None,
        metavar="PORT",
        help="After writing the report, serve it on 127.0.0.1:PORT with live recompile-on-drop (omit PORT to auto-pick a free port; the URL is printed on startup)",
    )
    args = parser.parse_args()

    original_path = args.original.resolve()
    if not original_path.exists():
        print(json.dumps({"ok": False, "error": f"Original not found: {original_path}"}))
        return 1
    original_units = extract_units(read_text(original_path))

    variants = []
    for vdir in args.variant:
        vdir = vdir.resolve()
        tex = vdir / "resume.tex"
        manifest_path = vdir / "changes.json"
        if not tex.exists():
            print(json.dumps({"ok": False, "error": f"Missing {tex}"}))
            return 1
        if not manifest_path.exists():
            print(json.dumps({"ok": False, "error": f"Missing {manifest_path} — every variant needs a changes.json manifest"}))
            return 1
        try:
            manifest = load_manifest(manifest_path)
        except (json.JSONDecodeError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": f"Invalid manifest {manifest_path}: {exc}"}))
            return 1
        units = extract_units(read_text(tex))
        pairing = pair_variant(original_units, units, manifest)
        keywords = verify_keywords(manifest, units)
        name = sanitize_variant_name(manifest["meta"].get("variant") or vdir.name)
        variants.append(
            {
                "name": name,
                "dir": vdir,
                "tex": tex,
                "manifest": manifest,
                "units": units,
                "pairing": pairing,
                "keywords": keywords,
                "preview_pages": pdf_pages_to_data_uris(find_variant_pdf(vdir)),
            }
        )

    apply_summary = None
    if args.apply_decisions:
        try:
            decisions = parse_decisions(args.apply_decisions)
        except (OSError, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            print(json.dumps({"ok": False, "error": f"Invalid decisions file {args.apply_decisions}: {exc}"}))
            return 1
        target = next((v for v in variants if v["name"] == decisions["variant"]), None)
        if target is None:
            names = [v["name"] for v in variants]
            print(json.dumps({"ok": False, "error": f'Decisions variant "{decisions["variant"]}" not among loaded variants {names}'}))
            return 1
        vdata = build_report_data(original_units, [target])["variants"][target["name"]]
        try:
            apply_summary = apply_decisions(target["dir"], vdata, decisions["dropped"], decisions["edits"])
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
        apply_summary["variant"] = target["name"]
        apply_summary["compile"] = recompile_variant(target["dir"], args.max_pages)
        # Reload from disk so validation and the report reflect the applied state.
        manifest = load_manifest(target["dir"] / "changes.json")
        units = extract_units(read_text(target["tex"]))
        target["manifest"] = manifest
        target["units"] = units
        target["pairing"] = pair_variant(original_units, units, manifest)
        target["keywords"] = verify_keywords(manifest, units)
        target["preview_pages"] = pdf_pages_to_data_uris(find_variant_pdf(target["dir"]))

    original_pdf = (
        args.original_pdf.resolve()
        if args.original_pdf
        else original_path.parent / "build" / f"{original_path.stem}.pdf"
    )
    original_pages = pdf_pages_to_data_uris(original_pdf)

    out_path = args.out.resolve() if args.out else (args.variant[0].resolve().parent / "review.html")
    render_report(original_path, original_units, variants, out_path, original_pages=original_pages)

    summary = {
        "ok": all(not v["pairing"]["unexplained"] and not v["pairing"]["problems"] for v in variants),
        "report": str(out_path),
        "original_preview_embedded": bool(original_pages),
        "variants": [
            {
                "name": v["name"],
                "declared_changes": len(v["manifest"]["changes"]),
                "unchanged_units": len(v["pairing"]["unchanged"]),
                "unexplained_changes": len(v["pairing"]["unexplained"]),
                "manifest_problems": v["pairing"]["problems"],
                "keyword_mismatches": [k["term"] for k in v["keywords"] if k["mismatch"]],
                "preview_embedded": bool(v["preview_pages"]),
            }
            for v in variants
        ],
    }
    if apply_summary is not None:
        summary["applied"] = apply_summary
    print(json.dumps(summary, indent=2), flush=True)

    if args.serve is not None:
        report_data = build_report_data(original_units, variants)

        def serve_apply(name: str, dropped: set[int], edits: dict[int, str]) -> dict:
            """Server twin of --apply-decisions for the report's Apply button:
            write the decisions into the variant's files, recompile, refresh the
            in-memory state and the report on disk, and hand back the final PDF."""
            target = next((v for v in variants if v["name"] == name), None)
            if target is None:
                return {"ok": False, "error": f"unknown variant {name!r}"}
            vdata = build_report_data(original_units, [target])["variants"][name]
            summary = apply_decisions(target["dir"], vdata, dropped, edits)
            compile_info = recompile_variant(target["dir"], args.max_pages)
            # Reload from disk so the served report and later applies see the new state
            # (change ids are manifest positions, so they shift after a drop).
            target["manifest"] = load_manifest(target["dir"] / "changes.json")
            target["units"] = extract_units(read_text(target["tex"]))
            target["pairing"] = pair_variant(original_units, target["units"], target["manifest"])
            target["keywords"] = verify_keywords(target["manifest"], target["units"])
            target["preview_pages"] = pdf_pages_to_data_uris(find_variant_pdf(target["dir"]))
            render_report(original_path, original_units, variants, out_path, original_pages=original_pages)
            report_data["variants"] = build_report_data(original_units, variants)["variants"]
            pdf_b64 = None
            pdf_path = find_variant_pdf(target["dir"])
            if compile_info.get("compiled") and pdf_path.exists():
                pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
            return {
                "ok": True,
                "applied": summary,
                "pages": compile_info.get("pages"),
                "within_limit": compile_info.get("within_limit"),
                "compile_error": compile_info.get("error") or compile_info.get("skipped"),
                "warnings": summary.get("warnings", []),
                "pdf": pdf_b64,
            }

        serve_report(out_path, variants, report_data, args.serve, args.max_pages, apply_cb=serve_apply)
        return 0
    if not summary["ok"]:
        return 3
    if apply_summary is not None and apply_summary["skipped"]:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
