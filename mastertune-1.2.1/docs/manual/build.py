#!/usr/bin/env python3
"""Builds the mastertune manual, one PDF per language, from the chapter files in docs/manual/<lang>/.

Usage: python3 docs/manual/build.py [en] [de] [--chrome PATH]
- Joins style.css and the chapters (CHAPTERS, in this order; a missing one is skipped with a note) into
  docs/manual/build/<lang>.html and prints it to docs/mastertune-manual-<lang>.pdf with headless Chromium: A4, page
  numbers from style.css, the headings as PDF bookmarks.
- Two passes: the table of contents in 00-cover.html has <span class="pg" data-for="ID"></span> placeholders. After
  the first pass, the page of every heading with that id comes from the PDF's bookmarks (PyMuPDF) and is filled in.
Needs headless Chromium and, for the page numbers, PyMuPDF (pip install pymupdf).
"""
import argparse
import html
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)
CHAPTERS = ["00-cover", "01-start", "02-master-tune", "03-load-cpu-monitor", "04-usb-audio", "05-sound",
            "06-arpeggiator", "07-workflow", "08-tools", "09-drone", "10-drone-volca", "11-reference"]
TITLES = {"en": "mastertune – Manual", "de": "mastertune – Handbuch"}


def assemble(lang):
    css = open(os.path.join(HERE, "style.css"), encoding="utf-8").read()
    parts = []
    for name in CHAPTERS:
        path = os.path.join(HERE, lang, name + ".html")
        if not os.path.exists(path):
            print(f"{lang}: {name}.html missing, skipped", file=sys.stderr)
            continue
        parts.append(f"<!-- {name} -->\n" + open(path, encoding="utf-8").read())
    return (f'<!doctype html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n<title>{TITLES[lang]}</title>\n'
            f"<style>\n{css}</style>\n</head>\n<body>\n" + "\n".join(parts) + "\n</body>\n</html>\n")


def print_pdf(chrome, src, out):
    subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    "--generate-pdf-document-outline", f"--print-to-pdf={out}", "file://" + src],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def heading_pages(pdf):
    import pymupdf
    doc = pymupdf.open(pdf)
    pages = {}
    for _, title, page in doc.get_toc():
        pages.setdefault(" ".join(title.split()), page)
    return pages, doc.page_count


def fill_toc(text, pages):
    titles = {}
    for m in re.finditer(r'<h([23])[^>]*\bid="([^"]+)"[^>]*>(.*?)</h\1>', text, re.S):
        titles[m.group(2)] = " ".join(html.unescape(re.sub(r"<[^>]+>", "", m.group(3))).split())
    missing = []

    def page_for(m):
        page = pages.get(titles.get(m.group(1), ""))
        if page is None:
            missing.append(m.group(1))
            return m.group(0)
        return f'<span class="pg" data-for="{m.group(1)}">{page}</span>'
    text = re.sub(r'<span class="pg" data-for="([^"]+)">\d*</span>', page_for, text)
    return text, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("langs", nargs="*", default=["en", "de"])
    ap.add_argument("--chrome", default="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    a = ap.parse_args()
    os.makedirs(os.path.join(HERE, "build"), exist_ok=True)
    for lang in a.langs:
        src = os.path.join(HERE, "build", lang + ".html")
        out = os.path.join(DOCS, f"mastertune-manual-{lang}.pdf")
        text = assemble(lang)
        open(src, "w", encoding="utf-8").write(text)
        print_pdf(a.chrome, src, out)
        try:
            pages, _ = heading_pages(out)
        except ImportError:
            print(f"{lang}: no PyMuPDF, the table of contents has no page numbers", file=sys.stderr)
            continue
        text, missing = fill_toc(text, pages)
        if missing:
            print(f"{lang}: no page for {', '.join(missing)}", file=sys.stderr)
        open(src, "w", encoding="utf-8").write(text)
        print_pdf(a.chrome, src, out)
        _, count = heading_pages(out)
        print(f"{lang}: {out} ({count} pages)")


if __name__ == "__main__":
    main()
