"""Regression tests for render_review.py — parser, validator, and revert logic.

Run from the repo root:
    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "latex-resume-tailoring" / "scripts"))

from render_review import (  # noqa: E402
    Unit,
    apply_decisions,
    build_final_text,
    build_report_data,
    extract_units,
    latex_to_plain,
    load_manifest,
    pair_variant,
    parse_decisions,
    read_text,
    sanitize_variant_name,
    verify_keywords,
)


def units_of(src: str) -> list[Unit]:
    return extract_units(src)


def wrap_doc(body: str) -> str:
    return "\\begin{document}\n" + body + "\n\\end{document}"


def manifest(changes: list[dict], keywords: list[dict] | None = None) -> dict:
    for i, c in enumerate(changes):
        c.setdefault("type", "rewrite")
        c.setdefault("before", "")
        c.setdefault("after", "")
        c.setdefault("jd", [])
        c.setdefault("evidence", "")
        c.setdefault("rationale", "")
        c.setdefault("defense", "")
        c.setdefault("risk", "verified")
        c.setdefault("section", "")
        c["_id"] = i
    return {"meta": {}, "jd_requirements": [], "changes": changes, "keywords": keywords or []}


# ---------------------------------------------------------------------------
# Template styles: the user's template, Jake's Resume, moderncv
# ---------------------------------------------------------------------------

USER_STYLE = wrap_doc(r"""
\sectionline{TECHNICAL SKILLS}
\textbf{Programming Languages:} Python, Java, C/C++\\
\textbf{Cloud \& Tools:} AWS, Docker, Git\\
\textbf{Technologies:} Linux, React, Node.js
\vspace{4pt}

\sectionline{EXPERIENCE}
\begin{tabularx}{\linewidth}{@{}X r@{}}
\experienceHeader{Software Engineer Intern}{Seattle, WA}{May 2026}{Microsoft}{C\#, .NET}
\end{tabularx}
\begin{itemize}
  \item Built a modular execution pipeline to load policy-delivered skills
    and invoke approved CLI runtimes.
  \item Implemented policy-based configuration, improving efficiency by \textbf{40\%}.
\end{itemize}
""")


class TestUserTemplateStyle(unittest.TestCase):
    def setUp(self):
        self.units = units_of(USER_STYLE)

    def test_skills_lines_stay_separate_units(self):
        skills = [u for u in self.units if u.section == "TECHNICAL SKILLS"]
        self.assertEqual(len(skills), 3)
        self.assertTrue(skills[0].plain.startswith("Programming Languages:"))
        self.assertTrue(skills[2].plain.startswith("Technologies:"))

    def test_bullets_detected_including_multiline_item(self):
        bullets = [u for u in self.units if u.kind == "bullet"]
        self.assertEqual(len(bullets), 2)
        # \item text wrapped across two source lines is one bullet
        self.assertIn("invoke approved CLI runtimes", bullets[0].plain)

    def test_experience_header_macro_is_own_unit(self):
        headers = [u for u in self.units if "Software Engineer Intern" in u.plain]
        self.assertEqual(len(headers), 1)
        self.assertEqual(headers[0].kind, "line")

    def test_sections_tracked(self):
        self.assertEqual({u.section for u in self.units}, {"TECHNICAL SKILLS", "EXPERIENCE"})

    def test_adjacent_macro_args_readable(self):
        header = next(u for u in self.units if "Software Engineer Intern" in u.plain)
        # {A}{B} args must not be mushed together: "InternSeattle" is a regression
        self.assertIn("Intern Seattle, WA", header.plain)


class TestOtherTemplates(unittest.TestCase):
    def test_jakes_resume_custom_item_macros_become_units(self):
        src = wrap_doc(r"""
\section{Experience}
\resumeSubheading{Software Engineer Intern}{May 2024}{Uber}{Sunnyvale, CA}
\resumeItemListStart
  \resumeItem{Developed a REST API using Go and gRPC serving 1M requests/day}
  \resumeItem{Reduced p99 latency by 40\% through Redis caching}
\resumeItemListEnd
""")
        units = units_of(src)
        texts = [u.plain for u in units]
        self.assertTrue(any("REST API using Go" in t for t in texts))
        self.assertTrue(any("p99 latency" in t for t in texts))
        # each \resumeItem is its own unit, not merged with its neighbor
        self.assertFalse(any("REST API" in t and "p99" in t for t in texts))

    def test_moderncv_entries_become_units(self):
        src = wrap_doc(r"""
\section{Experience}
\cventry{2024}{Engineer}{Uber}{CA}{}{Built streaming pipelines with Kafka.}
\cvitem{Skills}{Python, Java, distributed systems}
""")
        units = units_of(src)
        self.assertEqual(len(units), 2)
        self.assertIn("2024 Engineer Uber", units[0].plain)


class TestParagraphMerging(unittest.TestCase):
    def test_wrapped_prose_is_one_unit(self):
        src = wrap_doc(r"""
\section{Summary}
Experienced engineer who builds distributed
systems and developer tools, with a focus
on reliability and performance.
""")
        units = units_of(src)
        self.assertEqual(len(units), 1)
        self.assertIn("distributed systems and developer tools", units[0].plain)

    def test_blank_line_splits_paragraphs(self):
        src = wrap_doc("\\section{S}\nFirst paragraph text here.\n\nSecond paragraph text here.")
        units = units_of(src)
        self.assertEqual(len(units), 2)

    def test_paragraph_raw_is_exact_file_substring(self):
        src = wrap_doc("\\section{S}\nline one of prose\nline two of prose")
        unit = units_of(src)[0]
        self.assertIn(unit.raw, src)


class TestCrlfHandling(unittest.TestCase):
    def test_read_text_normalizes_crlf(self):
        with tempfile.NamedTemporaryFile("w", suffix=".tex", delete=False, newline="") as f:
            f.write("\\begin{document}\r\nSome prose line\r\nwrapped over two lines.\r\n\\end{document}\r\n")
            path = Path(f.name)
        try:
            text = read_text(path)
            self.assertNotIn("\r", text)
            unit = extract_units(text)[0]
            self.assertIn(unit.raw, text)  # revert-by-substring stays possible
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# Anti-churn validation
# ---------------------------------------------------------------------------

ORIG = wrap_doc(r"""
\sectionline{EXPERIENCE}
\begin{itemize}
  \item Developed a C\#/.NET installer with WiX, saving \textbf{\$5,000} annually.
  \item Automated data pipelines in Python, improving efficiency by \textbf{85\%}.
\end{itemize}
""")


class TestManifestValidation(unittest.TestCase):
    def test_declared_rewrite_is_explained(self):
        variant = ORIG.replace("Developed a C\\#/.NET installer", "Developed and shipped a C\\#/.NET installer")
        m = manifest([
            {
                "type": "rewrite",
                "before": "\\item Developed a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually.",
                "after": "\\item Developed and shipped a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually.",
            }
        ])
        pairing = pair_variant(units_of(ORIG), units_of(variant), m)
        self.assertEqual(pairing["problems"], [])
        self.assertEqual(pairing["unexplained"], [])
        self.assertEqual(len(pairing["unchanged"]), 1)

    def test_undeclared_synonym_swap_is_flagged(self):
        variant = ORIG.replace("Developed a", "Engineered a")
        pairing = pair_variant(units_of(ORIG), units_of(variant), manifest([]))
        self.assertEqual(len(pairing["unexplained"]), 1)
        self.assertEqual(pairing["unexplained"][0]["kind"], "rewrite")

    def test_undeclared_metric_tamper_is_flagged(self):
        variant = ORIG.replace("85\\%", "95\\%")
        pairing = pair_variant(units_of(ORIG), units_of(variant), manifest([]))
        self.assertEqual(len(pairing["unexplained"]), 1)

    def test_manifest_before_not_in_original_is_a_problem(self):
        m = manifest([{"type": "rewrite", "before": "\\item Something never written.", "after": "\\item Whatever."}])
        pairing = pair_variant(units_of(ORIG), units_of(ORIG), m)
        self.assertTrue(any("not found in the original" in p for p in pairing["problems"]))


# ---------------------------------------------------------------------------
# Keep/Drop revert (server-side twin of the in-browser builder)
# ---------------------------------------------------------------------------


class TestBuildFinalText(unittest.TestCase):
    def test_dropped_rewrite_reverts_to_original_wording(self):
        variant_text = ORIG.replace("Developed a", "Engineered a")
        changes = [
            {
                "id": 0,
                "type": "rewrite",
                "kind": "bullet",
                "orig_raw": "Developed a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually.",
                "var_raw": "Engineered a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually.",
                "prev_raw": "",
            }
        ]
        text, warnings, _ = build_final_text(variant_text, changes, dropped_ids={0})
        self.assertEqual(warnings, [])
        self.assertEqual(text, ORIG)

    def test_kept_change_is_untouched(self):
        variant_text = ORIG.replace("Developed a", "Engineered a")
        changes = [{"id": 0, "type": "rewrite", "kind": "bullet", "orig_raw": "x", "var_raw": "y", "prev_raw": ""}]
        text, _, _ = build_final_text(variant_text, changes, dropped_ids=set())
        self.assertEqual(text, variant_text)

    def test_dropped_add_removes_the_bullet(self):
        added = "  \\item Wrote integration tests for the installer.\n"
        variant_text = ORIG.replace("\\end{itemize}", added + "\\end{itemize}")
        changes = [
            {
                "id": 0,
                "type": "add",
                "kind": "bullet",
                "orig_raw": "",
                "var_raw": "Wrote integration tests for the installer.",
                "prev_raw": "",
            }
        ]
        text, warnings, _ = build_final_text(variant_text, changes, dropped_ids={0})
        self.assertEqual(warnings, [])
        self.assertNotIn("integration tests", text)

    def test_duplicate_text_revert_is_skipped_with_warning(self):
        # Two jobs share the identical bullet; a first-match replace would
        # silently revert the WRONG one. The revert must refuse instead.
        dup = wrap_doc(
            "\\sectionline{A}\n\\begin{itemize}\n  \\item Led a team of five engineers.\n\\end{itemize}\n"
            "\\sectionline{B}\n\\begin{itemize}\n  \\item Led a team of five engineers.\n\\end{itemize}"
        )
        changes = [
            {
                "id": 0,
                "type": "rewrite",
                "kind": "bullet",
                "orig_raw": "Managed a team of five engineers.",
                "var_raw": "Led a team of five engineers.",
                "prev_raw": "",
            }
        ]
        text, warnings, failed = build_final_text(dup, changes, dropped_ids={0})
        self.assertEqual(text, dup)  # nothing silently rewritten
        self.assertEqual(len(warnings), 1)
        self.assertIn("appears 2 times", warnings[0])
        self.assertEqual(failed, {0})

    def test_latex_special_chars_survive_revert(self):
        # $ & \ in replacement text must be inserted literally
        variant_text = ORIG.replace("saving \\textbf{\\$5,000}", "saving over \\textbf{\\$5,000}")
        changes = [
            {
                "id": 0,
                "type": "rewrite",
                "kind": "bullet",
                "orig_raw": "Developed a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually.",
                "var_raw": "Developed a C\\#/.NET installer with WiX, saving over \\textbf{\\$5,000} annually.",
                "prev_raw": "",
            }
        ]
        text, warnings, _ = build_final_text(variant_text, changes, dropped_ids={0})
        self.assertEqual(warnings, [])
        self.assertIn("saving \\textbf{\\$5,000} annually", text)

    def test_edited_change_swaps_in_the_new_wording(self):
        variant_text = ORIG.replace("Developed a", "Engineered a")
        changes = [
            {
                "id": 0,
                "type": "rewrite",
                "kind": "bullet",
                "orig_raw": "Developed a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually.",
                "var_raw": "Engineered a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually.",
                "prev_raw": "",
            }
        ]
        edited = "Shipped a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually."
        text, warnings, _ = build_final_text(variant_text, changes, dropped_ids=set(), edits={0: edited})
        self.assertEqual(warnings, [])
        self.assertIn(edited, text)
        self.assertNotIn("Engineered a C\\#", text)

    def test_edited_add_replaces_the_added_bullet_text(self):
        added = "  \\item Wrote integration tests for the installer.\n"
        variant_text = ORIG.replace("\\end{itemize}", added + "\\end{itemize}")
        changes = [
            {
                "id": 0,
                "type": "add",
                "kind": "bullet",
                "orig_raw": "",
                "var_raw": "Wrote integration tests for the installer.",
                "prev_raw": "",
            }
        ]
        text, warnings, _ = build_final_text(
            variant_text, changes, dropped_ids=set(), edits={0: "Wrote end-to-end tests for the installer."}
        )
        self.assertEqual(warnings, [])
        self.assertIn("\\item Wrote end-to-end tests for the installer.", text)
        self.assertNotIn("integration tests", text)

    def test_edit_on_duplicate_text_is_skipped_with_warning(self):
        dup = wrap_doc(
            "\\sectionline{A}\n\\begin{itemize}\n  \\item Led a team of five engineers.\n\\end{itemize}\n"
            "\\sectionline{B}\n\\begin{itemize}\n  \\item Led a team of five engineers.\n\\end{itemize}"
        )
        changes = [
            {
                "id": 0,
                "type": "rewrite",
                "kind": "bullet",
                "orig_raw": "Managed a team of five engineers.",
                "var_raw": "Led a team of five engineers.",
                "prev_raw": "",
            }
        ]
        text, warnings, failed = build_final_text(dup, changes, dropped_ids=set(), edits={0: "Led a team of six engineers."})
        self.assertEqual(text, dup)  # nothing silently rewritten
        self.assertEqual(len(warnings), 1)
        self.assertIn("appears 2 times", warnings[0])
        self.assertEqual(failed, {0})

    def test_drop_wins_over_edit_for_the_same_change(self):
        # The UI can't produce both, but a hand-crafted request could; drop must win.
        variant_text = ORIG.replace("Developed a", "Engineered a")
        changes = [
            {
                "id": 0,
                "type": "rewrite",
                "kind": "bullet",
                "orig_raw": "Developed a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually.",
                "var_raw": "Engineered a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually.",
                "prev_raw": "",
            }
        ]
        text, warnings, _ = build_final_text(variant_text, changes, dropped_ids={0}, edits={0: "Whatever."})
        self.assertEqual(warnings, [])
        self.assertEqual(text, ORIG)


# ---------------------------------------------------------------------------
# Applying a decisions JSON (--apply-decisions)
# ---------------------------------------------------------------------------

INSTALLER_BEFORE = "Developed a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually."
INSTALLER_AFTER = "Engineered a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually."
PIPELINES_BEFORE = "Automated data pipelines in Python, improving efficiency by \\textbf{85\\%}."
PIPELINES_AFTER = "Orchestrated data pipelines in Python, improving efficiency by \\textbf{85\\%}."


class TestApplyDecisions(unittest.TestCase):
    def _setup_variant(self, var_src: str, changes: list[dict], orig_src: str = ORIG):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        vdir = Path(td.name)
        (vdir / "resume.tex").write_text(var_src, encoding="utf-8")
        raw = {"meta": {"variant": "strict"}, "jd_requirements": [], "changes": changes, "keywords": []}
        (vdir / "changes.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")
        m = load_manifest(vdir / "changes.json")
        original = extract_units(orig_src)
        vunits = extract_units(var_src)
        pairing = pair_variant(original, vunits, m)
        data = build_report_data(
            original,
            [{"name": "strict", "units": vunits, "tex": vdir / "resume.tex", "pairing": pairing, "manifest": m}],
        )
        return vdir, data["variants"]["strict"]

    def test_dropped_change_reverts_file_and_removes_manifest_entry(self):
        var_src = ORIG.replace("Developed a", "Engineered a")
        vdir, vdata = self._setup_variant(var_src, [{"before": INSTALLER_BEFORE, "after": INSTALLER_AFTER}])
        result = apply_decisions(vdir, vdata, dropped={0}, edits={})
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["dropped_applied"], 1)
        self.assertEqual((vdir / "resume.tex").read_text(encoding="utf-8"), ORIG)
        raw = json.loads((vdir / "changes.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["changes"], [])

    def test_edited_change_updates_file_and_manifest_after(self):
        var_src = ORIG.replace("Developed a", "Engineered a")
        vdir, vdata = self._setup_variant(var_src, [{"before": INSTALLER_BEFORE, "after": INSTALLER_AFTER}])
        edited = "Shipped a C\\#/.NET installer with WiX, saving \\textbf{\\$5,000} annually."
        result = apply_decisions(vdir, vdata, dropped=set(), edits={0: edited})
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["edited_applied"], 1)
        final = (vdir / "resume.tex").read_text(encoding="utf-8")
        self.assertIn(edited, final)
        self.assertNotIn("Engineered a C\\#", final)
        raw = json.loads((vdir / "changes.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["changes"][0]["after"], edited)

    def test_mixed_drop_and_edit_survive_index_shift_and_revalidate_clean(self):
        var_src = ORIG.replace("Developed a", "Engineered a").replace("Automated data", "Orchestrated data")
        changes = [
            {"before": INSTALLER_BEFORE, "after": INSTALLER_AFTER},
            {"before": PIPELINES_BEFORE, "after": PIPELINES_AFTER},
        ]
        vdir, vdata = self._setup_variant(var_src, changes)
        edited = "Orchestrated data pipelines in Python and Airflow, improving efficiency by \\textbf{85\\%}."
        result = apply_decisions(vdir, vdata, dropped={0}, edits={1: edited})
        self.assertEqual(result["skipped"], [])
        final = (vdir / "resume.tex").read_text(encoding="utf-8")
        self.assertIn(INSTALLER_BEFORE, final)  # drop reverted the installer bullet
        self.assertIn(edited, final)
        raw = json.loads((vdir / "changes.json").read_text(encoding="utf-8"))
        self.assertEqual(len(raw["changes"]), 1)
        self.assertEqual(raw["changes"][0]["after"], edited)  # edit landed despite the deleted entry before it
        # The applied state must pass validation with zero unexplained changes.
        m = load_manifest(vdir / "changes.json")
        pairing = pair_variant(extract_units(ORIG), extract_units(final), m)
        self.assertEqual(pairing["problems"], [])
        self.assertEqual(pairing["unexplained"], [])

    def test_ambiguous_drop_is_skipped_and_manifest_kept(self):
        orig = wrap_doc(
            "\\sectionline{A}\n\\begin{itemize}\n  \\item Managed a team of five engineers.\n\\end{itemize}\n"
            "\\sectionline{B}\n\\begin{itemize}\n  \\item Led a team of five engineers.\n\\end{itemize}"
        )
        var_src = orig.replace("Managed a team", "Led a team")
        changes = [{"before": "Managed a team of five engineers.", "after": "Led a team of five engineers."}]
        vdir, vdata = self._setup_variant(var_src, changes, orig_src=orig)
        result = apply_decisions(vdir, vdata, dropped={0}, edits={})
        self.assertEqual(result["skipped"], [0])
        self.assertEqual(result["dropped_applied"], 0)
        self.assertTrue(result["warnings"])
        self.assertEqual((vdir / "resume.tex").read_text(encoding="utf-8"), var_src)  # untouched
        raw = json.loads((vdir / "changes.json").read_text(encoding="utf-8"))
        self.assertEqual(len(raw["changes"]), 1)  # entry kept so validation still explains the edit

    def test_unknown_change_id_raises(self):
        var_src = ORIG.replace("Developed a", "Engineered a")
        vdir, vdata = self._setup_variant(var_src, [{"before": INSTALLER_BEFORE, "after": INSTALLER_AFTER}])
        with self.assertRaises(ValueError):
            apply_decisions(vdir, vdata, dropped={7}, edits={})

    def _write_json(self, payload) -> Path:
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(payload, f)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return Path(f.name)

    def test_parse_decisions_accepts_the_exported_shape(self):
        path = self._write_json(
            {
                "variant": "strict",
                "source_report": "/x/review.html",
                "kept": [{"id": 2, "section": "S", "summary": "..."}],
                "dropped": [{"id": 0, "section": "S", "summary": "..."}],
                "edited": [{"id": 1, "section": "S", "summary": "...", "new_latex": "New wording."}],
            }
        )
        d = parse_decisions(path)
        self.assertEqual(d["variant"], "strict")
        self.assertEqual(d["dropped"], {0})
        self.assertEqual(d["edits"], {1: "New wording."})

    def test_parse_decisions_rejects_empty_new_latex(self):
        path = self._write_json({"variant": "strict", "edited": [{"id": 0, "new_latex": "   "}]})
        with self.assertRaises(ValueError):
            parse_decisions(path)


# ---------------------------------------------------------------------------
# Keyword auto-verification
# ---------------------------------------------------------------------------


class TestKeywordVerification(unittest.TestCase):
    def test_claimed_covered_but_absent_is_a_mismatch(self):
        units = units_of(ORIG)
        m = manifest([], keywords=[
            {"term": "Python", "status": "covered", "where": "pipelines bullet"},
            {"term": "Kubernetes", "status": "covered", "where": "nowhere really"},
            {"term": "distributed systems", "status": "gap", "where": ""},
        ])
        rows = verify_keywords(m, units)
        by_term = {r["term"]: r for r in rows}
        self.assertFalse(by_term["Python"]["mismatch"])
        self.assertTrue(by_term["Kubernetes"]["mismatch"])
        self.assertFalse(by_term["distributed systems"]["mismatch"])  # honest gap, not a lie

    def test_short_terms_do_not_match_inside_other_words(self):
        src = wrap_doc(
            "\\section{S}\n\\begin{itemize}\n"
            "  \\item Designed graph algorithms for a Django app in JavaScript.\n"
            "\\end{itemize}"
        )
        units = units_of(src)
        m = manifest([], keywords=[
            {"term": "Go", "status": "covered", "where": "claimed"},        # "alGOrithms", "djanGO"
            {"term": "R", "status": "covered", "where": "claimed"},          # letters everywhere
            {"term": "Java", "status": "covered", "where": "claimed"},       # "JAVAscript"
            {"term": "JavaScript", "status": "covered", "where": "bullet"},  # genuinely present
        ])
        by_term = {r["term"]: r for r in verify_keywords(m, units)}
        self.assertTrue(by_term["Go"]["mismatch"])
        self.assertTrue(by_term["R"]["mismatch"])
        self.assertTrue(by_term["Java"]["mismatch"])
        self.assertFalse(by_term["JavaScript"]["mismatch"])

    def test_symbol_suffixed_terms_match_exactly(self):
        src = wrap_doc(
            "\\section{S}\n\\begin{itemize}\n"
            "  \\item Built C\\#/.NET and C++ services with Node.js and Go.\n"
            "\\end{itemize}"
        )
        units = units_of(src)
        m = manifest([], keywords=[
            {"term": "C#", "status": "covered", "where": "bullet"},
            {"term": ".NET", "status": "covered", "where": "bullet"},
            {"term": "C++", "status": "covered", "where": "bullet"},
            {"term": "Node.js", "status": "covered", "where": "bullet"},
            {"term": "Go", "status": "covered", "where": "bullet"},
            {"term": "C", "status": "covered", "where": "claimed"},  # C# and C++ are not evidence of C
        ])
        by_term = {r["term"]: r for r in verify_keywords(m, units)}
        for present in ("C#", ".NET", "C++", "Node.js", "Go"):
            self.assertFalse(by_term[present]["mismatch"], present)
        self.assertTrue(by_term["C"]["mismatch"])


class TestManifestHardening(unittest.TestCase):
    def _write_manifest(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def test_non_dict_manifest_raises_value_error(self):
        path = self._write_manifest("[]")
        try:
            with self.assertRaises(ValueError):
                load_manifest(path)
        finally:
            path.unlink()

    def test_wrong_typed_fields_raise_value_error(self):
        for bad in ('{"changes": "x"}', '{"meta": "x"}', '{"keywords": ["x"]}'):
            path = self._write_manifest(bad)
            try:
                with self.assertRaises(ValueError):
                    load_manifest(path)
            finally:
                path.unlink()

    def test_variant_name_sanitized_for_html_and_js_contexts(self):
        import re as _re

        for hostile in ("x');alert(1)//", 'a"><img src=x>', "</script><script>evil()</script>"):
            cleaned = sanitize_variant_name(hostile)
            self.assertTrue(_re.fullmatch(r"[A-Za-z0-9._-]+", cleaned), cleaned)
        self.assertEqual(sanitize_variant_name("strict"), "strict")
        self.assertEqual(sanitize_variant_name("uber-swe1.v2"), "uber-swe1.v2")


class TestLatexToPlain(unittest.TestCase):
    def test_common_commands(self):
        self.assertEqual(
            latex_to_plain(r"Saved \textbf{\$5,000} via \href{https://x.com}{automation} \textbar{} CI/CD"),
            "Saved $5,000 via automation | CI/CD",
        )


if __name__ == "__main__":
    unittest.main()
