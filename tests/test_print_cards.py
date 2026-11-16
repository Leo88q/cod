"""Карманная карточка 40×50 и лист A4 с метками реза.

Проверяем то, что важно для печати: QR не меньше нормы читаемости, тихая зона
не залита, размеры в миллиметрах, метки реза на месте.
"""
import re

import pytest

from app import print_cards as pc


def test_qr_matrix_matches_segno():
    """Путь QR восстановим до матрицы — иначе напечатается мусор."""
    import segno

    url = "https://scena.link/c/C001"
    path, modules = pc.qr_path(url)
    qr = segno.make(url, error="m")
    size, border = len(qr.matrix), 2
    grid = [[False] * modules for _ in range(modules)]
    for m in re.finditer(r"M(\d+) (\d+)h(\d+)", path):
        x0, y, run = (int(g) for g in m.groups())
        for x in range(x0, x0 + run):
            grid[y][x] = True
    assert all(grid[r + border][c + border] == bool(qr.matrix[r][c])
               for r in range(size) for c in range(size))


def test_quiet_zone_is_empty():
    """Без пустой рамки сканер не увидит QR на печати — это классическая ошибка."""
    path, modules = pc.qr_path("https://scena.link/c/C001")
    grid = [[False] * modules for _ in range(modules)]
    for m in re.finditer(r"M(\d+) (\d+)h(\d+)", path):
        x0, y, run = (int(g) for g in m.groups())
        for x in range(x0, x0 + run):
            grid[y][x] = True
    inner = modules - 4
    assert not any(grid[i][j] for i in range(modules) for j in range(modules)
                   if i < 2 or j < 2 or i >= inner + 2 or j >= inner + 2)


def test_qr_module_size_is_printable():
    _, modules = pc.qr_path("https://scena.link/c/C001")
    assert pc.QR_MM / modules >= 0.4      # модуль не меньше 0.4 мм


def test_card_size_is_40_by_50_mm():
    svg = pc.card_svg("https://scena.link/c/C001", "C001", shop="Студия")
    assert 'width="40.0"' in svg and 'height="50.0"' in svg
    assert "напиши здесь" in svg           # место под рукописную строку
    assert "http" not in svg               # ни одной внешней ссылки


def test_sheet_a4_has_twenty_cards_and_cut_marks():
    codes = [f"C{i:03d}" for i in range(20)]
    svg = pc.sheet_svg([f"https://scena.link/c/{c}" for c in codes], codes, shop="Студия")
    assert svg.startswith("<svg")
    assert 'width="210.0mm"' in svg and 'height="297.0mm"' in svg
    assert svg.count('class="card"') == 20
    # метки реза: 2 шт на каждый из 4 углов каждой карточки.
    # Считаем именно штрихи реза, а не пунктир «напиши здесь» (у него тот же стиль).
    cut_marks = re.findall(r'<path d="M[-\d.]+ [-\d.]+[hv]-?[\d.]+" stroke="#111" stroke-width="0.2"/>', svg)
    assert len(cut_marks) == 20 * 4 * 2


def test_sheet_handles_partial_last_row():
    svg = pc.sheet_svg(["https://scena.link/c/A", "https://scena.link/c/B"], ["A", "B"])
    assert svg.count('class="card"') == 2


def test_empty_sheet_rejected():
    with pytest.raises(ValueError):
        pc.sheet_svg([], [])


def test_caption_and_shop_are_escaped():
    svg = pc.card_svg("https://scena.link/c/X", "X", caption="<script>", shop="A & B")
    assert "<script>" not in svg and "&lt;script&gt;" in svg
    assert "A &amp; B" in svg
