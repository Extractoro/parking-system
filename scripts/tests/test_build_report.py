"""Tests of scripts/build_report.py on a small made-up source (stage R, docs/reports/STYLE.md).

    uv run --no-project --with pytest --with python-docx --with markdown-it-py --with pyyaml pytest scripts/tests -q
"""

import shutil
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

GOOD_BODY = (
    "### 1.2.1 Бронювання місця\n\nАбзац із `кодом` і *курсивом*, гроші й API без зауважень.\n\n"
    "- перший пункт\n- другий пункт\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n"
    "```sh\necho hello\n```\n\n"
    "![Знімок клієнта](img/shot.jpg)\n"
)


def source(tmp_path: Path, *, body: str | None = None, code: str | None = None, drop: str | None = None) -> Path:
    (tmp_path / "img").mkdir(exist_ok=True)
    shutil.copy(IMG, tmp_path / "img" / "shot.jpg")
    sections = {
        "Мета роботи": "Перевірити збирач на маленькому джерелі.",
        "Хід роботи": GOOD_BODY if body is None else body,
        "Код програми": code or "### Лістинг 1 - Міграція\n\n<!-- code: migrations/002_c09_booking.sql#L8-L14 -->\n",
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


# ---------------------------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------------------------
def test_builds_title_sections_table_figure_listing(tmp_path):
    out = build_report.build(source(tmp_path), out_dir=tmp_path / "out")
    assert out.name == "Звіт_Web_ЛР9.docx"
    doc = Document(str(out))
    text = all_text(doc)
    assert "з лабораторної роботи № 9" in text and "робіти" not in text  # the template typo is fixed
    assert "з дисципліни «Обмін даними у Web-застосунках»" in text
    assert "ТЕМА 9: Тестова робота." in text
    assert "Перевірити збирач" in text and "Один абзац із числом 17." in text
    assert "1.2.1 Бронювання місця" in text
    assert "Рисунок 1 - Знімок клієнта" in text
    assert "Лістинг 1 - Міграція (002_c09_booking.sql)" in text  # file name only, no path
    assert "migrations/" not in text
    assert "CREATE TABLE level_hour (" in text  # the listing came from git, not from the source
    assert "Повний код роботи: https://github.com/" in text and text.count("/tree/web-lr9") == 1  # tag, no hash
    assert HEAD[:7] not in text
    assert "—" not in text and "–" not in text
    assert len(doc.tables) == 1 and doc.tables[0].cell(1, 1).text == "2"
    assert len(doc.inline_shapes) == 1
    caption = next(p for p in doc.paragraphs if p.text.startswith("Лістинг 1"))
    assert not any(r.bold for r in caption.runs)  # listing captions are in regular weight
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


# ---------------------------------------------------------------------------------------------
# style linter (STYLE.md, section 7)
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line, what",
    [
        ("**Що зроблено.** Текст.", "bold label"),
        ("**Як перевірено.** Текст.", "bold label"),
        ("**Фактичний результат.** Текст.", "bold label"),
        ("### О1.4 Головна операція", "item code"),
        ("Пункт D2.5 виконано.", "item code"),
        ("### Бронювання (Ядро)", "Ядро"),
        ("Бронювання — головна операція.", "dash"),
        ("Від 1–24 годин.", "dash"),
        ("Модель описано в README.", "internal document"),
        ("Рішення записано в docs/decisions.md.", "internal document"),
        ("Код у web/server/app/bookings.py.", "internal document"),
        ("Перевіряє test_5.", "test or file name"),
        ("Міграція 002_c09_booking.sql.", "test or file name"),
        ("Звіт згенеровано автоматично.", "mention of AI"),
        ("Код писав AI.", "mention of AI"),
    ],
)
def test_linter_finds(tmp_path, line, what):
    p = source(tmp_path, body=GOOD_BODY + "\n" + line + "\n")
    problems = build_report.lint_source(p)
    assert any(what in pr for pr in problems), problems
    with pytest.raises(build_report.StyleError):
        build_report.build(p, out_dir=tmp_path / "out")


def test_linter_quiet_on_words_that_only_look_alike(tmp_path):
    body = GOOD_BODY + "\nГроші, API, Web-клієнт, години 1-24 і 10:00 - 12:00 без зауважень.\n"
    assert build_report.lint_source(source(tmp_path, body=body)) == []


def test_paths_are_allowed_only_in_the_code_section(tmp_path):
    p = source(tmp_path)
    assert build_report.lint_source(p) == []  # the marker in «Код програми» names migrations/...
    p2 = source(tmp_path, body=GOOD_BODY + "\n<!-- code: migrations/002_c09_booking.sql#L8-L9 -->\n")
    assert any("internal document" in pr for pr in build_report.lint_source(p2))


def test_docx_check_finds_dashes_put_in_by_word(tmp_path):
    out = build_report.build(source(tmp_path), out_dir=tmp_path / "out")
    assert build_report.lint_docx(out) == []
    doc = Document(str(out))
    doc.add_paragraph("Рисунок 9 — підставлено")
    doc.save(str(out))
    assert build_report.lint_docx(out)


@pytest.mark.parametrize(
    ("remote", "web"),
    [("https://github.com/o/parking-system.git", "https://github.com/o/parking-system"),
     ("git@github.com:o/parking-system.git", "https://github.com/o/parking-system")],
)
def test_tag_url_from_remote(monkeypatch, remote, web):
    monkeypatch.setattr(build_report, "git", lambda *a: remote + "\n")
    assert build_report.tag_url("dpc", 3) == f"{web}/tree/dpc-lr3"
