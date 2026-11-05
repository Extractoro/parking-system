# /// script
# requires-python = ">=3.11"
# dependencies = ["python-docx>=1.1", "markdown-it-py>=3.0", "pyyaml>=6.0", "pymupdf>=1.24"]
# ///
"""Build a lab report (.docx) from docs/reports/<course>-lrN.md on the department template.

    uv run scripts/build_report.py docs/reports/web-lr1.md            # -> docs/reports/out/Звіт_Web_ЛР1.docx
    uv run scripts/build_report.py docs/reports/web-lr1.md --pdf      # + PDF and page PNGs to check by eye

    uv run scripts/build_report.py docs/reports/web-lr1.md --lint-only # only the style check

The template (docs/reports/template.docx) is opened, never recreated: margins, fonts and the title
page stay the department's. The source has fixed sections (see docs/plans/stage-R-report-pipeline.md):
front matter (course, lab, title, commit, date) and `## Мета роботи`, `## Хід роботи`,
`## Код програми`, `## Висновки`. Listings are not copied by hand: a marker
`<!-- code: path#L10-L40 -->` takes the lines from `git show <commit>:<path>`.

Before building, the source is checked against docs/reports/STYLE.md (section 7): no item codes,
no bold labels, no long or middle dashes, no internal paths or file names outside «Код програми»,
no mentions of AI. After building, the text of the .docx is checked for dashes once more.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs" / "reports" / "template.docx"
OUT_DIR = ROOT / "docs" / "reports" / "out"

DISCIPLINES = {
    "web": ("Web", "Обмін даними у Web-застосунках"),
    "dpc": ("DPC", "Розподілені та паралельні обчислення"),
}
SECTIONS = ["Мета роботи", "Хід роботи", "Код програми", "Висновки"]
ANCHORS = {"Мета роботи": "1.1", "Хід роботи": "1.2", "Код програми": "1.3", "Висновки": "Висновки"}

BODY_FONT = "Times New Roman"
BODY_SIZE = Pt(14)
CODE_FONT = "Consolas"
CODE_SIZE = Pt(9)
TABLE_SIZE = Pt(12)
MAX_IMAGE_WIDTH = Cm(16)
MAX_LISTING_LINES = 60
CODE_MARKER = re.compile(r"<!--\s*code:\s*(?P<path>[^#\s]+)#L(?P<a>\d+)-L(?P<b>\d+)\s*-->")


class ReportError(Exception):
    """The source cannot be built: a missing section, picture, code range or commit."""


class StyleError(ReportError):
    """The source or the built .docx breaks docs/reports/STYLE.md; `problems` lists the lines."""

    def __init__(self, where: str, problems: list[str]):
        super().__init__(f"{where}: {len(problems)} style problem(s)\n" + "\n".join(f"  {p}" for p in problems))
        self.problems = problems


# ---------------------------------------------------------------------------------------------
# style linter (docs/reports/STYLE.md, section 7)
# ---------------------------------------------------------------------------------------------
DASHES = re.compile("[—–]")  # — and –
STYLE_EVERYWHERE = [
    (re.compile(r"\*\*(Що зроблено|Де\.|Як перевірено|Очікуваний|Фактичний)"), "bold label"),
    (re.compile(r"(?<!\w)[ОДOD]\d\.\d+(?!\w)"), "item code of the guide"),
    (re.compile(r"\(Ядро\)"), "«(Ядро)»"),
    (DASHES, "long or middle dash (use a hyphen)"),
    # whole words, case as written: «ші» inside «гроші» is not «ШІ», «API» is not «AI»
    (re.compile(r"(?<!\w)(Claude|ШІ|AI)(?!\w)"), "mention of AI"),
    (re.compile(r"асистент|згенерован", re.I), "mention of AI"),
]
# internal files and paths: allowed only in «Код програми» (listing captions and code markers)
STYLE_OUTSIDE_CODE = [
    (re.compile(r"README|decisions\.md|CLAUDE\.md|docs/|results/|migrations/|(?<![\w.])web/|(?<![\w.])dpc/|scripts/"),
     "internal document or path"),
    (re.compile(r"test_|\.py(?!\w)|\.sql(?!\w)|\.yaml(?!\w)"), "test or file name"),
]


def lint_source(path: Path) -> list[str]:
    """Problems as 'line N: what: text'; an empty list means the source follows STYLE.md."""
    problems: list[str] = []
    section = ""
    in_front_matter = False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if n == 1 and line.strip() == "---":
            in_front_matter = True
            continue
        if in_front_matter:
            if line.strip() == "---":
                in_front_matter = False
            continue
        m = re.match(r"^## +(.+?)\s*$", line)
        if m:
            section = m.group(1)
        rules = STYLE_EVERYWHERE + ([] if section == "Код програми" else STYLE_OUTSIDE_CODE)
        for rx, what in rules:
            hit = rx.search(line)
            if hit:
                problems.append(f"line {n}: {what} «{hit.group(0)}»: {line.strip()[:100]}")
    return problems


def lint_docx(path: Path) -> list[str]:
    """Word or a converter may put dashes in by itself: check the text of the built document."""
    doc = Document(str(path))
    texts = [p.text for p in doc.paragraphs]
    texts += [p.text for t in doc.tables for row in t.rows for c in row.cells for p in c.paragraphs]
    return [f"paragraph: {t.strip()[:100]}" for t in texts if DASHES.search(t)]


# ---------------------------------------------------------------------------------------------
# source
# ---------------------------------------------------------------------------------------------
@dataclass
class Source:
    path: Path
    meta: dict
    sections: dict[str, str] = field(default_factory=dict)


def read_source(path: Path) -> Source:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise ReportError(f"{path}: no front matter (--- course/lab/title/commit/date ---)")
    meta = yaml.safe_load(m.group(1)) or {}
    for key in ("course", "lab", "title", "commit"):
        if not meta.get(key):
            raise ReportError(f"{path}: front matter has no '{key}'")
    if meta["course"] not in DISCIPLINES:
        raise ReportError(f"{path}: course must be one of {sorted(DISCIPLINES)}")

    parts = re.split(r"^## +(.+?)\s*$", m.group(2), flags=re.M)
    sections = {parts[i].strip(): parts[i + 1].strip() for i in range(1, len(parts), 2)}
    for name in SECTIONS:
        if not sections.get(name):
            raise ReportError(f"{path}: required section '## {name}' is missing or empty")
    return Source(path, meta, sections)


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise ReportError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def repo_url() -> str:
    """Web address of the repository from `git remote get-url origin` (https or ssh form)."""
    url = git("remote", "get-url", "origin").strip()
    m = re.match(r"^git@([^:]+):(.+)$", url)
    if m:
        url = f"https://{m.group(1)}/{m.group(2)}"
    return re.sub(r"\.git$", "", url).rstrip("/")


def tag_url(course: str, lab: int) -> str:
    return f"{repo_url()}/tree/{course}-lr{lab}"


def listing(commit: str, rel: str, a: int, b: int) -> list[str]:
    lines = git("show", f"{commit}:{rel}").splitlines()
    if not (1 <= a <= b <= len(lines)):
        raise ReportError(f"code marker {rel}#L{a}-L{b}: the file has {len(lines)} lines at {commit[:7]}")
    if b - a + 1 > MAX_LISTING_LINES:
        raise ReportError(f"code marker {rel}#L{a}-L{b}: more than {MAX_LISTING_LINES} lines")
    return lines[a - 1 : b]


# ---------------------------------------------------------------------------------------------
# docx helpers
# ---------------------------------------------------------------------------------------------
def style_run(run, *, bold=None, italic=None, mono=False, size=None):
    run.font.name = CODE_FONT if mono else BODY_FONT
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), CODE_FONT if mono else BODY_FONT)
    run.font.size = size or (Pt(12) if mono else BODY_SIZE)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic


def body_format(p, *, indent=True, align=WD_ALIGN_PARAGRAPH.JUSTIFY, after=Pt(6)):
    pf = p.paragraph_format
    pf.line_spacing = 1.5
    pf.space_before = Pt(0)
    pf.space_after = after
    pf.first_line_indent = Cm(1.25) if indent else Cm(0)
    p.alignment = align


def shade(p, fill="F2F2F2"):
    ppr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    ppr.append(shd)


def set_cell_shading(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def replace_paragraph_text(p, text):
    """Word splits text into many runs: keep the first run's formatting, drop the rest."""
    runs = p.runs
    runs[0].text = text
    for r in runs[1:]:
        r._element.getparent().remove(r._element)


# ---------------------------------------------------------------------------------------------
# writer: markdown tokens -> docx elements inserted after an anchor paragraph
# ---------------------------------------------------------------------------------------------
class Writer:
    def __init__(self, doc, src: Source):
        self.doc = doc
        self.src = src
        self.cursor = None
        self.figures = 0
        self.listings = 0
        # typographer off: no automatic "smart" dashes or quotes (STYLE.md, section 7)
        self.md = MarkdownIt("commonmark", {"typographer": False}).enable("table")

    # -- placing -------------------------------------------------------------------------------
    def _place(self, element):
        self.cursor.addnext(element)
        self.cursor = element

    def paragraph(self):
        p = self.doc.add_paragraph()
        self._place(p._p)
        return p

    # -- inline -------------------------------------------------------------------------------
    def inline(self, p, children, *, size=None, base_bold=False):
        bold, italic = base_bold, False
        for t in children:
            if t.type == "text":
                style_run(p.add_run(t.content), bold=bold, italic=italic, size=size)
            elif t.type == "code_inline":
                style_run(p.add_run(t.content), bold=bold, mono=True, size=size or Pt(12))
            elif t.type in ("softbreak", "hardbreak"):
                style_run(p.add_run(" "), size=size)
            elif t.type == "strong_open":
                bold = True
            elif t.type == "strong_close":
                bold = base_bold
            elif t.type == "em_open":
                italic = True
            elif t.type == "em_close":
                italic = False
            elif t.type in ("link_open", "link_close", "html_inline"):
                continue
            elif t.type == "image":
                raise ReportError("an image must stand alone in its paragraph")

    # -- blocks -------------------------------------------------------------------------------
    def heading(self, text, level):
        p = self.paragraph()
        try:
            p.style = self.doc.styles["Heading 3"]  # keeps the outline level of the template
        except KeyError:
            pass
        pf = p.paragraph_format
        pf.space_before = Pt(12)
        pf.space_after = Pt(6)
        pf.first_line_indent = Cm(1.25)
        pf.keep_with_next = True
        r = p.add_run(text)
        style_run(r, bold=True, italic=level > 3)
        r.font.color.rgb = RGBColor(0, 0, 0)
        return p

    def text_paragraph(self, children):
        imgs = [c for c in children if c.type == "image"]
        if imgs:
            if len(children) != 1:
                raise ReportError("an image must stand alone in its paragraph")
            self.figure(imgs[0].attrGet("src"), imgs[0].content or "")
            return
        p = self.paragraph()
        body_format(p)
        self.inline(p, children)

    def figure(self, src, caption):
        path = (self.src.path.parent / src).resolve()
        if not path.is_file():
            raise ReportError(f"picture not found: {src} (looked at {path})")
        self.figures += 1
        p = self.paragraph()
        body_format(p, indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, after=Pt(0))
        p.paragraph_format.keep_with_next = True
        run = p.add_run()
        pic = run.add_picture(str(path))
        if pic.width > MAX_IMAGE_WIDTH:
            ratio = MAX_IMAGE_WIDTH / pic.width
            pic.width = MAX_IMAGE_WIDTH
            pic.height = int(pic.height * ratio)
        cap = self.paragraph()
        body_format(cap, indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, after=Pt(10))
        style_run(cap.add_run(f"Рисунок {self.figures} - {caption}"), size=Pt(12))

    def bullet(self, children, marker):
        p = self.paragraph()
        body_format(p, indent=False, after=Pt(2))
        pf = p.paragraph_format
        pf.left_indent = Cm(1.25 + 0.75)
        pf.first_line_indent = Cm(-0.75)
        style_run(p.add_run(f"{marker}\t"))
        tabs = pf.tab_stops
        tabs.add_tab_stop(Cm(1.25 + 0.75))
        self.inline(p, children)

    def code(self, lines, *, keep=True):
        for i, line in enumerate(lines or [""]):
            p = self.paragraph()
            pf = p.paragraph_format
            pf.line_spacing = 1.0
            pf.space_before = Pt(4) if i == 0 else Pt(0)
            pf.space_after = Pt(6) if i == len(lines) - 1 else Pt(0)
            pf.first_line_indent = Cm(0)
            pf.left_indent = Cm(0.25)
            pf.keep_with_next = keep and i < len(lines) - 1
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            shade(p)
            style_run(p.add_run(line.rstrip("\n").replace("\t", "    ") or " "), mono=True, size=CODE_SIZE)

    def table(self, rows):
        ncols = max(len(r) for r in rows)
        t = self.doc.add_table(rows=len(rows), cols=ncols)
        try:
            t.style = self.doc.styles["Table Grid"]
        except KeyError:
            pass
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, row in enumerate(rows):
            for j in range(ncols):
                cell = t.cell(i, j)
                p = cell.paragraphs[0]
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.0
                children = row[j] if j < len(row) else []
                self.inline(p, children, size=TABLE_SIZE, base_bold=(i == 0))
                if i == 0:
                    set_cell_shading(cell, "F2F2F2")
        self._place(t._tbl)
        spacer = self.paragraph()  # Word needs a paragraph between a table and what follows
        body_format(spacer, indent=False, after=Pt(4))

    def listing_caption(self, text):
        p = self.paragraph()
        body_format(p, indent=False, align=WD_ALIGN_PARAGRAPH.LEFT, after=Pt(2))
        p.paragraph_format.keep_with_next = True
        style_run(p.add_run(text), size=Pt(12))  # «Лістинг N - Опис (file)», regular weight (STYLE.md 6)

    # -- one section ----------------------------------------------------------------------------
    def render(self, markdown, *, code_section=False):
        tokens = self.md.parse(markdown)
        i = 0
        pending_caption = None
        while i < len(tokens):
            t = tokens[i]
            if t.type == "heading_open":
                text = tokens[i + 1].content
                if code_section:
                    pending_caption = text
                else:
                    self.heading(text, int(t.tag[1]))
                i += 3
            elif t.type == "paragraph_open":
                self.text_paragraph(tokens[i + 1].children)
                i += 3
            elif t.type in ("bullet_list_open", "ordered_list_open"):
                ordered = t.type == "ordered_list_open"
                n = int(t.attrGet("start") or 1)
                depth = 0
                i += 1
                while i < len(tokens) and not (tokens[i].type in ("bullet_list_close", "ordered_list_close") and depth == 0):
                    tk = tokens[i]
                    if tk.type in ("bullet_list_open", "ordered_list_open"):
                        depth += 1
                    elif tk.type in ("bullet_list_close", "ordered_list_close"):
                        depth -= 1
                    elif tk.type == "inline":
                        self.bullet(tk.children, f"{n}." if ordered else "•")
                        n += 1
                    i += 1
                i += 1
            elif t.type == "fence" or t.type == "code_block":
                self.code(t.content.rstrip("\n").split("\n"))
                i += 1
            elif t.type == "table_open":
                rows, row = [], None
                i += 1
                while tokens[i].type != "table_close":
                    tk = tokens[i]
                    if tk.type == "tr_open":
                        row = []
                    elif tk.type == "inline":
                        row.append(tk.children)
                    elif tk.type == "tr_close":
                        rows.append(row)
                    i += 1
                self.table(rows)
                i += 1
            elif t.type == "html_block":
                m = CODE_MARKER.search(t.content)
                if m:
                    rel, a, b = m.group("path"), int(m.group("a")), int(m.group("b"))
                    lines = listing(self.src.meta["commit"], rel, a, b)
                    self.listings += 1
                    title = pending_caption or f"Лістинг {self.listings}"
                    self.listing_caption(f"{title} ({Path(rel).name})")
                    self.code(lines, keep=len(lines) <= 30)
                    pending_caption = None
                i += 1
            elif t.type == "hr":
                i += 1
            else:
                i += 1
        if code_section:
            # STYLE.md 6a: a link to the tag of the lab, not a hash (the tag may be set after the build)
            p = self.paragraph()
            body_format(p)
            style_run(p.add_run("Повний код роботи: "))
            style_run(p.add_run(tag_url(self.src.meta["course"], int(self.src.meta["lab"]))), size=Pt(12))


# ---------------------------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------------------------
def find(doc, startswith):
    for p in doc.paragraphs:
        if p.text.strip().startswith(startswith):
            return p
    raise ReportError(f"template: no paragraph starting with '{startswith}'")


def build(src_path: Path, out_dir: Path = OUT_DIR) -> Path:
    problems = lint_source(src_path)
    if problems:
        raise StyleError(str(src_path), problems)
    src = read_source(src_path)
    commit = str(src.meta["commit"])
    git("cat-file", "-e", f"{commit}^{{commit}}")  # the commit must exist
    code, discipline = DISCIPLINES[src.meta["course"]]
    lab = int(src.meta["lab"])

    doc = Document(str(TEMPLATE))

    # title page and topic; the typo of the template "робіти" is fixed here
    replace_paragraph_text(find(doc, "з лабораторної"), f"з лабораторної роботи № {lab}")
    replace_paragraph_text(find(doc, "з дисципліни"), f"з дисципліни «{discipline}»")
    replace_paragraph_text(find(doc, "ТЕМА"), f"ТЕМА {lab}: {src.meta['title']}.")

    anchors = {name: find(doc, ANCHORS[name]) for name in SECTIONS}
    # drop the empty placeholder paragraphs between the section headings of the template
    for name in SECTIONS:
        nxt = anchors[name]._p.getnext()
        while nxt is not None and nxt.tag == qn("w:p"):
            text = "".join(nxt.itertext()).strip()
            if text or nxt in [a._p for a in anchors.values()]:
                break
            following = nxt.getnext()
            nxt.getparent().remove(nxt)
            nxt = following

    w = Writer(doc, src)
    for name in SECTIONS:
        w.cursor = anchors[name]._p
        w.render(src.sections[name], code_section=(name == "Код програми"))
        if name != "Висновки":
            # a little air before the next section heading of the template
            w.cursor.addnext(OxmlElement("w:p"))

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"Звіт_{code}_ЛР{lab}.docx"
    try:
        doc.save(str(out))
    except PermissionError:
        raise ReportError(f"{out} is open in another program (Word?): close it and build again") from None
    problems = lint_docx(out)
    if problems:
        raise StyleError(str(out), problems)
    return out


def to_pdf(docx_path: Path) -> Path:
    """Word (Windows) or LibreOffice converts the report; pdftoppm makes page PNGs to look at."""
    pdf = docx_path.with_suffix(".pdf")
    soffice = subprocess.run(["where" if sys.platform == "win32" else "which", "soffice"], capture_output=True)
    if soffice.returncode == 0:
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(pdf.parent), str(docx_path)], check=True)
    elif sys.platform == "win32":
        ps = (
            "$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
            f"$d = $w.Documents.Open('{docx_path}', $false, $true); "
            f"$d.SaveAs([ref] '{pdf}', [ref] 17); $d.Close($false); $w.Quit()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    else:
        raise ReportError("no converter to PDF: install LibreOffice")
    return pdf


def pages_to_png(pdf: Path, png_dir: Path, dpi: int = 60) -> list[Path]:
    """pdftoppm when installed, else PyMuPDF (pip package, no system install needed)."""
    prefix = png_dir / pdf.stem
    for old in png_dir.glob(pdf.stem + "*.png"):
        old.unlink()
    if subprocess.run(["where" if sys.platform == "win32" else "which", "pdftoppm"], capture_output=True).returncode == 0:
        subprocess.run(["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(prefix)], check=True)
    else:
        import pymupdf

        with pymupdf.open(str(pdf)) as d:
            for n, page in enumerate(d, start=1):
                page.get_pixmap(dpi=dpi).save(str(png_dir / f"{pdf.stem}-{n:02d}.png"))
    return sorted(png_dir.glob(pdf.stem + "*.png"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("--pdf", action="store_true", help="also convert to PDF and print page PNGs")
    ap.add_argument("--lint-only", action="store_true", help="only check the source against STYLE.md")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR, help="where to write the .docx (default: docs/reports/out)")
    ap.add_argument("--png-dir", type=Path, default=None, help="where to put page PNGs (default: next to the PDF)")
    args = ap.parse_args()
    try:
        if args.lint_only:
            problems = lint_source(args.source.resolve())
            if problems:
                raise StyleError(str(args.source), problems)
            print("style: ok")
            return 0
        out = build(args.source.resolve(), out_dir=args.out_dir.resolve())
    except ReportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(out)
    if args.pdf:
        pdf = to_pdf(out)
        print(pdf)
        png_dir = args.png_dir or pdf.parent / "pages"
        png_dir.mkdir(parents=True, exist_ok=True)
        for png in pages_to_png(pdf, png_dir):
            print(png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
