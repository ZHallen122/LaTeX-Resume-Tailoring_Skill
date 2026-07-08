#!/usr/bin/env python3
"""Generate a self-contained HTML before/after review for tailored LaTeX resumes.

Reads the original resume plus one or more variant directories (each containing
resume.tex and changes.json), cross-validates the declared changes against the
actual textual diff, and writes a single HTML report with:

- side-by-side per-bullet before/after with word-level highlighting
- rationale, JD evidence, and risk tag for every declared change
- keyword coverage per variant, auto-verified against the variant text
- an "unexplained changes" section for any edit missing from the manifest

Prints a JSON summary to stdout. Exit codes:
  0  report written, every change is explained by the manifest
  1  input error
  3  report written, but unexplained changes exist (fix the manifest or revert)
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


def verify_keywords(manifest: dict, variant_units: list[Unit]) -> list[dict]:
    haystack = " ".join(u.norm for u in variant_units)
    rows = []
    for kw in manifest["keywords"]:
        term = str(kw.get("term", "")).strip()
        status = kw.get("status", "covered")
        found = bool(term) and term.lower() in haystack
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

    if change["type"] == "add":
        body = f'<div class="col only"><div class="lbl">Added</div><p>{right or esc(after_plain)}</p></div>'
    elif change["type"] == "remove":
        body = f'<div class="col only"><div class="lbl">Removed</div><p><del>{esc(before_plain)}</del></p></div>'
    else:
        body = (
            f'<div class="col"><div class="lbl">Before</div><p>{left or esc(before_plain)}</p></div>'
            f'<div class="col"><div class="lbl">After</div><p>{right or esc(after_plain)}</p></div>'
        )

    cid = change["_id"]
    return f"""
    <article class="card" id="card-{esc(vname)}-{cid}">
      <header>
        <span class="badge type">{esc(type_label)}</span>
        <span class="badge {risk_class}">{esc(risk_label)}</span>
        <span class="section-name">{esc(change.get("section", ""))}</span>
        {jd_chips}
        <span class="toggle" role="group" aria-label="keep or drop this change">
          <button class="t-keep active" onclick="setDecision('{esc(vname)}',{cid},true)">Keep</button>
          <button class="t-drop" onclick="setDecision('{esc(vname)}',{cid},false)">Drop</button>
        </span>
      </header>
      <div class="cols">{body}</div>
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
    actionbar = f"""
    <div class="actionbar">
      <span class="ab-count" id="ab-count-{esc(name)}"></span>
      {unexplained_warn}
      <span class="ab-buttons">
        <button class="recompile-btn hidden" id="recompile-{esc(name)}"
          onclick="recompilePreview('{esc(name)}')">Recompile preview</button>
        <button class="primary" onclick="downloadFinal('{esc(name)}')">Download final resume.tex</button>
        <button onclick="copyDecisions('{esc(name)}')">Copy decisions JSON</button>
      </span>
    </div>
    <p class="hint">Drop a change to revert that bullet to the original wording in the downloaded file. Selections are
    saved in this browser. After downloading, recompile to re-check the one-page fit:
    <code>python3 scripts/check_latex_resume.py resume.tex --max-pages 1</code> — or paste the decisions JSON back to
    the agent to apply, compile, and re-review for you.</p>"""

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
:root { --bg:#ffffff; --fg:#1a1d21; --muted:#6b7280; --line:#e5e7eb; --card:#f9fafb;
  --accent:#0052a6; --add-bg:#dcfce7; --add-fg:#14532d; --del-bg:#fee2e2; --del-fg:#7f1d1d;
  --warn:#b45309; --danger:#b91c1c; --chip:#eef2ff; --chip-fg:#3730a3; }
@media (prefers-color-scheme: dark) { :root { --bg:#111418; --fg:#e6e8ea; --muted:#9aa3ad;
  --line:#2a2f36; --card:#191e24; --accent:#6ea8dc; --add-bg:#14351f; --add-fg:#86efac;
  --del-bg:#3b1a1a; --del-fg:#fca5a5; --warn:#f59e0b; --danger:#f87171; --chip:#26294a; --chip-fg:#b4bcf8; } }
* { box-sizing: border-box; }
body { margin:0; font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; background:var(--bg); color:var(--fg); }
.wrap { max-width: 1080px; margin: 0 auto; padding: 24px 20px 80px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--muted); font-size: 13px; margin-bottom: 20px; word-break: break-all; }
.tabs { display:flex; gap:8px; margin: 18px 0; flex-wrap: wrap; }
.tabs button { font:600 14px/1 inherit; padding:9px 16px; border-radius:999px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer; }
.tabs button.active { background:var(--accent); border-color:var(--accent); color:#fff; }
.panel { display:none; } .panel.active { display:block; }
.stats { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 16px; min-width:110px; }
.stat .n { font-size:22px; font-weight:700; } .stat .l { font-size:12px; color:var(--muted); }
.stat.bad .n { color:var(--danger); } .stat.good .n { color:var(--add-fg); }
.sec { margin:26px 0 10px; font-size:15px; text-transform:uppercase; letter-spacing:.04em; color:var(--accent); }
.sec.danger { color:var(--danger); }
.card { border:1px solid var(--line); border-radius:12px; background:var(--card); padding:12px 14px; margin-bottom:12px; }
.card header { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px; }
.badge { font-size:11px; font-weight:700; padding:3px 9px; border-radius:999px; text-transform:uppercase; letter-spacing:.03em; }
.badge.type { background:var(--chip); color:var(--chip-fg); }
.risk-verified { background:var(--add-bg); color:var(--add-fg); }
.risk-adjacent { background:#fef3c7; color:#92400e; }
.risk-confirm { background:#fde68a; color:#78350f; }
.risk-unexplained { background:var(--del-bg); color:var(--del-fg); }
.section-name { font-size:12px; color:var(--muted); }
.chip { font-size:11px; background:var(--chip); color:var(--chip-fg); border-radius:999px; padding:2px 8px; }
.cols { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.cols .only { grid-column: 1 / -1; }
@media (max-width: 760px) { .cols { grid-template-columns:1fr; } }
.col .lbl { font-size:11px; font-weight:700; color:var(--muted); text-transform:uppercase; margin-bottom:2px; }
.col p { margin:0; }
del { background:var(--del-bg); color:var(--del-fg); text-decoration:line-through; border-radius:3px; padding:0 2px; }
ins { background:var(--add-bg); color:var(--add-fg); text-decoration:none; border-radius:3px; padding:0 2px; }
.meta { font-size:13px; margin-top:8px; color:var(--fg); }
.meta .k { display:inline-block; font-size:11px; font-weight:700; color:var(--muted); text-transform:uppercase;
  margin-right:8px; min-width:60px; }
.hint { font-size:13px; color:var(--muted); margin:4px 0 12px; }
.problems { border:1px solid var(--warn); border-radius:10px; padding:10px 14px; margin-bottom:14px; font-size:13px; }
.tbl-wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-size:12px; text-transform:uppercase; }
.kw-ok { color:var(--add-fg); font-weight:600; } .kw-adj { color:var(--warn); font-weight:600; }
.kw-gap { color:var(--danger); font-weight:600; }
.confirm { list-style:none; padding:0; } .confirm li { margin-bottom:10px; }
.confirm label { display:block; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 12px; cursor:pointer; }
.confirm .defense { display:block; font-size:12px; color:var(--muted); margin-top:4px; }
.unchanged { margin-top:24px; }
.unchanged summary { cursor:pointer; color:var(--muted); font-size:14px; }
.unchanged ul { font-size:13px; color:var(--muted); }
.unexplained-card { border-color: var(--danger); }
.toggle { margin-left:auto; display:inline-flex; border:1px solid var(--line); border-radius:999px; overflow:hidden; }
.toggle button { font:600 11px/1 inherit; padding:5px 12px; border:0; background:transparent; color:var(--muted); cursor:pointer; }
.toggle .t-keep.active { background:var(--add-bg); color:var(--add-fg); }
.toggle .t-drop.active { background:var(--del-bg); color:var(--del-fg); }
.card.dropped { opacity:.5; border-style:dashed; }
.card.dropped .cols, .card.dropped .meta { text-decoration:none; }
.actionbar { position:sticky; top:0; z-index:10; display:flex; align-items:center; gap:12px; flex-wrap:wrap;
  background:var(--bg); border:1px solid var(--line); border-radius:12px; padding:10px 14px; margin-bottom:6px;
  box-shadow:0 2px 8px rgba(0,0,0,.06); }
.ab-count { font-weight:700; font-size:14px; }
.ab-warn { font-size:12px; color:var(--danger); }
.ab-buttons { margin-left:auto; display:flex; gap:8px; }
.actionbar button { font:600 13px/1 inherit; padding:9px 14px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer; }
.actionbar button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
code { background:var(--card); border:1px solid var(--line); border-radius:5px; padding:1px 5px; font-size:12px; }
.hidden { display:none !important; }
.previews { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.previews.single { grid-template-columns:minmax(0, 640px); justify-content:center; }
@media (max-width: 900px) { .previews { grid-template-columns:1fr; } }
.previews figure { margin:0; }
.previews figcaption { font-size:12px; font-weight:700; color:var(--muted); text-transform:uppercase; margin-bottom:6px; }
.previews img { width:100%; display:block; border:1px solid var(--line); border-radius:8px; background:#fff;
  box-shadow:0 1px 6px rgba(0,0,0,.08); margin-bottom:10px; cursor:zoom-in; }
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
const decisions = {};  // vname -> { changeId -> true(keep)/false(drop) }
const storeKey = v => 'resume-review:' + location.pathname + ':' + v;

function loadDecisions(v) {
  decisions[v] = {};
  DATA.variants[v].changes.forEach(c => { decisions[v][c.id] = true; });
  try {
    const saved = JSON.parse(localStorage.getItem(storeKey(v)) || '{}');
    Object.keys(saved).forEach(k => { if (k in decisions[v]) decisions[v][k] = saved[k]; });
  } catch (e) {}
}

function setDecision(v, id, keep) {
  decisions[v][id] = keep;
  try { localStorage.setItem(storeKey(v), JSON.stringify(decisions[v])); } catch (e) {}
  refresh(v);
}

function refresh(v) {
  const d = decisions[v];
  DATA.variants[v].changes.forEach(c => {
    const card = document.getElementById('card-' + v + '-' + c.id);
    if (!card) return;
    const keep = d[c.id] !== false;
    card.classList.toggle('dropped', !keep);
    card.querySelector('.t-keep').classList.toggle('active', keep);
    card.querySelector('.t-drop').classList.toggle('active', !keep);
  });
  const total = DATA.variants[v].changes.length;
  const kept = Object.values(d).filter(x => x !== false).length;
  const el = document.getElementById('ab-count-' + v);
  if (el) el.textContent = 'Keeping ' + kept + ' of ' + total + ' changes';
}

function escRe(s) { return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'); }

function buildFinal(v) {
  const vd = DATA.variants[v];
  let text = vd.variant_text;
  const warnings = [];
  vd.changes.forEach(c => {
    if (decisions[v][c.id] !== false) return;  // kept -> leave as-is
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
  const pick = keepFlag => DATA.variants[v].changes
    .filter(c => (d[c.id] !== false) === keepFlag)
    .map(c => ({ id: c.id, section: c.section, summary: c.plain.slice(0, 90) }));
  return JSON.stringify({ variant: v, source_report: location.pathname,
    kept: pick(true), dropped: pick(false) }, null, 2);
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

// Live recompile: only possible when the report is served by render_review.py --serve.
if (location.protocol === 'http:' || location.protocol === 'https:') {
  document.querySelectorAll('.recompile-btn').forEach(b => b.classList.remove('hidden'));
}

async function recompilePreview(v) {
  const btn = document.getElementById('recompile-' + v);
  const dropped = DATA.variants[v].changes
    .filter(c => decisions[v][c.id] === false).map(c => c.id);
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = 'Compiling…';
  try {
    const res = await fetch('/recompile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variant: v, dropped })
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
        tabs.append(f'<button id="tab-{esc(name)}" onclick="showTab(\'{esc(name)}\')">{esc(name)}</button>')
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
    jd_line = f'<div>JD: <a href="{esc(jd_url)}">{esc(jd_url)}</a></div>' if jd_url else ""

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
  <h1>Resume tailoring review{(" — " + esc(jd_title)) if jd_title else ""}</h1>
  <div class="sub">
    <div>Original (unchanged): <code>{esc(str(original_path))}</code></div>
    {variant_paths}
    {jd_line}
    <div>Generated: {esc(generated)}</div>
  </div>
  <div class="tabs">{"".join(tabs)}</div>
  {"".join(panels)}
</div>
<script type="application/json" id="report-data">{data_json}</script>
<script>{JS}
showTab('{esc(variants[0]["name"])}');
Object.keys(DATA.variants).forEach(v => refresh(v));</script>
</body>
</html>"""
    out_path.write_text(page, encoding="utf-8")


# ---------------------------------------------------------------------------
# Live preview server (--serve)
# ---------------------------------------------------------------------------


def build_final_text(variant_text: str, changes: list[dict], dropped_ids: set[int]) -> tuple[str, list[str]]:
    """Python twin of the in-browser buildFinal(): revert dropped changes."""
    text = variant_text
    warnings: list[str] = []
    for c in changes:
        if c["id"] not in dropped_ids:
            continue
        ctype, var_raw, orig_raw = c["type"], c["var_raw"], c["orig_raw"]
        if ctype == "add":
            pattern = re.compile(r"\\item\s*" + re.escape(var_raw) + r"[ \t]*\n?")
            if c["kind"] == "bullet" and pattern.search(text):
                text = pattern.sub("", text, count=1)
            elif var_raw in text:
                text = text.replace(var_raw + "\n", "", 1) if var_raw + "\n" in text else text.replace(var_raw, "", 1)
            else:
                warnings.append(f"Could not remove added text for change #{c['id']}")
        elif ctype == "remove":
            prev_raw = c.get("prev_raw", "")
            occurrences = text.count(prev_raw) if prev_raw else 0
            if occurrences == 1:
                insert = ("\n  \\item " if c["kind"] == "bullet" else "\n") + orig_raw
                text = text.replace(prev_raw, prev_raw + insert, 1)
            elif occurrences > 1:
                warnings.append(
                    f"Change #{c['id']}: anchor text appears {occurrences} times — restore skipped to avoid inserting at the wrong spot; ask the agent to apply it."
                )
            else:
                warnings.append(f"Could not restore removed text for change #{c['id']}")
        else:
            occurrences = text.count(var_raw) if var_raw else 0
            if occurrences == 1:
                text = text.replace(var_raw, orig_raw, 1)
            elif occurrences > 1:
                warnings.append(
                    f"Change #{c['id']}: text appears {occurrences} times — revert skipped to avoid rewriting the wrong bullet; ask the agent to apply it."
                )
            else:
                warnings.append(f"Could not revert change #{c['id']}")
    return text, warnings


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


def serve_report(report_path: Path, variants: list[dict], report_data: dict, port: int, max_pages: int) -> None:
    import http.server

    variant_dirs = {v["name"]: v["dir"] for v in variants}

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
                self._send(200, report_path.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/recompile":
                self._send(404, b"not found", "text/plain")
                return
            # Loopback-only hardening: a hostile web page can fire a cross-origin
            # POST whose side effect (a LaTeX compile) still runs even though the
            # response is unreadable. Reject anything not clearly from this report.
            origin = self.headers.get("Origin", "")
            host = self.headers.get("Host", "")
            if not host.startswith(("127.0.0.1", "localhost")):
                self._send(403, b'{"ok": false, "error": "bad host"}', "application/json")
                return
            if origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
                self._send(403, b'{"ok": false, "error": "cross-origin request rejected"}', "application/json")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1_000_000:
                    self._send(413, b'{"ok": false, "error": "invalid request size"}', "application/json")
                    return
                req = json.loads(self.rfile.read(length))
                name = req["variant"]
                dropped = set(int(i) for i in req.get("dropped", []))
                vdata = report_data["variants"][name]
                text, warnings = build_final_text(vdata["variant_text"], vdata["changes"], dropped)
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
    print(json.dumps(summary, indent=2), flush=True)

    if args.serve is not None:
        report_data = build_report_data(original_units, variants)
        serve_report(out_path, variants, report_data, args.serve, args.max_pages)
        return 0
    return 0 if summary["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
