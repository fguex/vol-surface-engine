#!/usr/bin/env python3
"""Rend un markdown en PDF paginé (Chrome headless), avec table des matières et numéros de page.

    scripts/build_cours_pdf.py docs/cours_complet.md docs/cours_complet.pdf

Dépendances : `markdown` et `pymupdf` (pip), `Google Chrome` (macOS).
Rien n'est ajouté au projet Python : ce script sert à produire un document, pas à tourner en prod.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from pathlib import Path

import markdown

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page { size: A4; margin: 16mm 14mm 16mm 14mm; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 10pt;
       line-height: 1.42; color: #14181f; }
h1 { font-size: 22pt; margin: 0 0 6pt 0; color: #0b1f3a; border-bottom: 3px solid #0b1f3a;
     padding-bottom: 6pt; }
h1 + p em { color: #445; }
h2 { font-size: 14.5pt; margin: 18pt 0 6pt 0; color: #0b1f3a;
     border-bottom: 1px solid #c8d2e0; padding-bottom: 3pt; page-break-after: avoid; }
h3 { font-size: 11.8pt; margin: 13pt 0 4pt 0; color: #123a63; page-break-after: avoid; }
h4 { font-size: 10.6pt; margin: 10pt 0 3pt 0; color: #234; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
code { font-family: "SFMono-Regular", Menlo, Consolas, monospace; font-size: 8.7pt;
       background: #f2f4f8; padding: 0.5pt 2.5pt; border-radius: 2pt; }
pre { background: #f6f8fb; border: 1px solid #dde4ee; border-left: 3px solid #4a6fa5;
      padding: 6pt 8pt; margin: 7pt 0; font-size: 7.9pt; line-height: 1.25;
      white-space: pre-wrap; word-wrap: break-word; page-break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 7.9pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 8.6pt;
        page-break-inside: avoid; }
th { background: #0b1f3a; color: #fff; text-align: left; padding: 3.5pt 5pt; font-weight: 600; }
td { border-bottom: 1px solid #dde4ee; padding: 3.5pt 5pt; vertical-align: top; }
tr:nth-child(even) td { background: #f7f9fc; }
blockquote { margin: 6pt 0 6pt 14pt; padding: 5pt 10pt; background: #fbf7e8;
             border-left: 3px solid #c9a227; color: #3a3320; }
hr { border: none; border-top: 1px solid #c8d2e0; margin: 16pt 0; }
strong { color: #0b1f3a; }
ul, ol { margin: 4pt 0 6pt 0; padding-left: 18pt; }
li { margin: 2pt 0; }
.toc { margin: 10pt 0 18pt 0; padding: 10pt 14pt; background: #f7f9fc;
       border: 1px solid #dde4ee; }
.toc h2.toc-title { border: none; margin: 0 0 6pt 0; font-size: 13pt; }
.toc ul.toclist { list-style: none; padding-left: 2pt; font-size: 9.2pt; }
.toc ul.toclist li { margin: 2pt 0; color: #223; }
.toc ul.toclist li::before { content: '\\25B8  '; color: #4a6fa5; }
"""


def build_html(md_text: str) -> str:
    body = markdown.markdown(
        md_text,
        extensions=["extra", "toc", "sane_lists", "fenced_code", "codehilite"],
        extension_configs={
            "toc": {"permalink": False, "toc_depth": "2-2"},
            "codehilite": {"guess_lang": False, "noclasses": True},
        },
    )
    heads = re.findall(r"<h2[^>]*>(.*?)</h2>", body, flags=re.S)
    toc = "\n".join(f"<li>{h}</li>" for h in heads)
    toc_html = ('<section class="toc"><h2 class="toc-title">Table des matières</h2>'
                f'<ul class="toclist">{toc}</ul></section>')
    # La table des matières se place juste avant le premier titre de niveau 2.
    first = re.search(r'<h2 id="[^"]*">', body)
    if first:
        body = body[:first.start()] + toc_html + body[first.start():]
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            f"<title>Cours complet</title><style>{CSS}</style></head><body>{body}</body></html>")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    a = ap.parse_args()

    md_text = a.input.read_text(encoding="utf-8")
    # `e^{x}` -> `e^(x)` : plus lisible en police fixe, et sans markdown parasite.
    md_text = re.sub(r"\^\{([^{}]*)\}", r"^(\1)", md_text)

    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "cours.html"
        html_path.write_text(build_html(md_text), encoding="utf-8")
        cmd = [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
               f"--print-to-pdf={a.output}", f"file://{html_path}"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not a.output.exists():
            print(r.stderr[-800:])
            return 1

    import pymupdf  # dépendance de rendu uniquement

    doc = pymupdf.open(a.output)
    n = doc.page_count
    for i, page in enumerate(doc, start=1):
        if i == 1:
            continue
        w, h = page.rect.width, page.rect.height
        page.insert_text((w / 2 - 60, h - 18), f"{a.output.stem}  ·  {i} / {n}",
                         fontsize=7.5, fontname="helv", color=(0.42, 0.47, 0.55))
    doc.set_metadata({"title": a.output.stem, "author": "Felix Guez"})
    doc.saveIncr()
    print(f"{a.output} : {n} pages, {a.output.stat().st_size // 1024} Ko")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
