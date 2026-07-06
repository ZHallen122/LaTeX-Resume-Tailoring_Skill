#!/usr/bin/env python3
"""Compile a LaTeX resume and report PDF page count as JSON."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run_command(command: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode, proc.stdout


def count_pages_with_pdfinfo(pdf_path: Path) -> int | None:
    if not shutil.which("pdfinfo"):
        return None
    code, output = run_command(["pdfinfo", str(pdf_path)], pdf_path.parent)
    if code != 0:
        return None
    match = re.search(r"^Pages:\s+(\d+)\s*$", output, re.MULTILINE)
    return int(match.group(1)) if match else None


def count_pages_with_pypdf(pdf_path: Path) -> int | None:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            return None

    try:
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return None


def count_pages_roughly(pdf_path: Path) -> int | None:
    try:
        data = pdf_path.read_bytes()
    except OSError:
        return None
    count = len(re.findall(rb"/Type\s*/Page\b", data))
    return count or None


def count_pdf_pages(pdf_path: Path) -> int | None:
    return (
        count_pages_with_pdfinfo(pdf_path)
        or count_pages_with_pypdf(pdf_path)
        or count_pages_roughly(pdf_path)
    )


def compile_latex(tex_path: Path, out_dir: Path, engine: str) -> tuple[bool, str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    source_name = tex_path.name
    pdf_path = out_dir / f"{tex_path.stem}.pdf"

    if engine == "auto" and shutil.which("latexmk"):
        command = [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-outdir={out_dir}",
            source_name,
        ]
    else:
        selected = "pdflatex" if engine == "auto" else engine
        if not shutil.which(selected):
            return False, f"LaTeX engine not found: {selected}", pdf_path
        command = [
            selected,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-output-directory",
            str(out_dir),
            source_name,
        ]

    code, output = run_command(command, tex_path.parent)
    if code != 0:
        return False, output, pdf_path

    if command[0] in {"pdflatex", "xelatex", "lualatex"}:
        second_code, second_output = run_command(command, tex_path.parent)
        output = output + "\n" + second_output
        if second_code != 0:
            return False, output, pdf_path

    return pdf_path.exists(), output, pdf_path


def extract_warnings(log: str) -> list[str]:
    warnings: list[str] = []
    for line in log.splitlines():
        if "Overfull \\hbox" in line or "Underfull \\hbox" in line:
            warnings.append(line.strip())
        elif "LaTeX Warning:" in line:
            warnings.append(line.strip())
    return warnings[:25]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile a LaTeX resume and report page-count status as JSON."
    )
    parser.add_argument("resume_tex", type=Path, help="Path to resume.tex")
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument(
        "--engine",
        choices=["auto", "pdflatex", "xelatex", "lualatex"],
        default="auto",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Build directory. Defaults to <resume-dir>/build.",
    )
    args = parser.parse_args()

    tex_path = args.resume_tex.resolve()
    if not tex_path.exists():
        print(json.dumps({"ok": False, "error": f"File not found: {tex_path}"}, indent=2))
        return 1

    out_dir = (args.out_dir.resolve() if args.out_dir else tex_path.parent / "build")
    compiled, log, pdf_path = compile_latex(tex_path, out_dir, args.engine)
    pages = count_pdf_pages(pdf_path) if compiled else None
    within_limit = pages is not None and pages <= args.max_pages

    result = {
        "ok": compiled and within_limit,
        "compiled": compiled,
        "resume_tex": str(tex_path),
        "pdf": str(pdf_path) if compiled else None,
        "pages": pages,
        "max_pages": args.max_pages,
        "within_limit": within_limit,
        "warnings": extract_warnings(log),
    }
    if not compiled:
        result["error"] = "LaTeX compilation failed or no PDF was produced."
        result["log_tail"] = "\n".join(log.splitlines()[-40:])

    print(json.dumps(result, indent=2))
    if not compiled:
        return 1
    return 0 if within_limit else 2


if __name__ == "__main__":
    sys.exit(main())
