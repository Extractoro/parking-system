"""Tests of scripts/build_report.py on a small made-up source (stage R).

    uv run --no-project --with pytest --with python-docx --with markdown-it-py --with pyyaml pytest scripts/tests -q
"""

import subprocess
import sys
from pathlib import Path

import pytest
from docx import Document

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_report  # noqa: E402

HEAD = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
IMG = ROOT / "docs" / "reports" / "img" / "web-lr1-409.jpg"


def source(tmp_path: Path, *, body: str | None = None, code: str | None = None, drop: str | None = None) -> Path:
    sections = {
        "Мета роботи": "Перевірити збирач на маленькому джерелі.",
        "Хід роботи": body
        or (
            "### О1.4 Головна операція (Ядро)\n\n**Що зроблено.** Абзац із `кодом` і *курсивом*.\n\n"
            "- перший пункт\n- другий пункт\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "```sh\necho hello\n```\n\n"
            f"![Знімок клієнта]({IMG.as_posix()})\n"
        ),
        "Код програми": code or "### Лістинг 1 — міграція\n\n<!-- code: migrations/002_c09_booking.sql#L8-L14 -->\n",
        "Висновки": "Один абзац із числом 17.",
    }
    if drop:
        sections.pop(drop)
    text = f"---\ncourse: web\nlab: 9\ntitle: \"Тестова робота\"\ncommit: {HEAD}\ndate: 2026-11-04\n---\n\n"
    text += "\n\n".join(f"## {k}\n\n{v}" for k, v in sections.items())
    p = tmp_path / "web-lr9.md"
    p.write_text(text, encoding="utf-8")
    return p


def all_text(doc) -> str:
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        parts += [c.text for row in t.rows for c in row.cells]
    return "\n".join(parts)


def test_builds_title_sections_table_figure_listing(tmp_path):
    out = build_report.build(source(tmp_path), out_dir=tmp_path / "out")
    assert out.name == "Звіт_Web_ЛР9.docx"
    doc = Document(str(out))
    text = all_text(doc)
    assert "з лабораторної роботи № 9" in text and "робіти" not in text  # the template typo is fixed
    assert "з дисципліни «Обмін даними у Web-застосунках»" in text
    assert "ТЕМА 9: Тестова робота." in text
    assert "Перевірити збирач" in text and "Один абзац із числом 17." in text
    assert "О1.4 Головна операція (Ядро)" in text
    assert "Рисунок 1 — Знімок клієнта" in text
    assert "Лістинг 1 — міграція. Файл: migrations/002_c09_booking.sql, рядки 8–14" in text
    assert "CREATE TABLE level_hour (" in text  # the listing came from git, not from the source
    assert len(doc.tables) == 1 and doc.tables[0].cell(1, 1).text == "2"
    assert len(doc.inline_shapes) == 1
    # order of the template sections is kept: 1.1 < 1.2 < 1.3 < Висновки
    idx = [next(i for i, p in enumerate(doc.paragraphs) if p.text.strip().startswith(k)) for k in ("1.1", "1.2", "1.3", "Висновки")]
    assert idx == sorted(idx)


@pytest.mark.parametrize("missing", ["Мета роботи", "Хід роботи", "Код програми", "Висновки"])
def test_missing_section_is_an_error(tmp_path, missing):
    with pytest.raises(build_report.ReportError, match=missing):
        build_report.build(source(tmp_path, drop=missing), out_dir=tmp_path / "out")


def test_missing_picture_is_an_error(tmp_path):
    with pytest.raises(build_report.ReportError, match="picture not found"):
        build_report.build(source(tmp_path, body="![x](img/nope.png)\n"), out_dir=tmp_path / "out")


def test_code_marker_out_of_range_is_an_error(tmp_path):
    code = "<!-- code: migrations/002_c09_booking.sql#L1-L9999 -->\n"
    with pytest.raises(build_report.ReportError, match="lines"):
        build_report.build(source(tmp_path, code=code), out_dir=tmp_path / "out")


def test_code_marker_unknown_file_is_an_error(tmp_path):
    with pytest.raises(build_report.ReportError, match="git show"):
        build_report.build(source(tmp_path, code="<!-- code: no/such/file.py#L1-L2 -->\n"), out_dir=tmp_path / "out")


def test_unknown_commit_is_an_error(tmp_path):
    p = source(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace(HEAD, "0" * 40), encoding="utf-8")
    with pytest.raises(build_report.ReportError):
        build_report.build(p, out_dir=tmp_path / "out")
