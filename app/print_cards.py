"""Печатная карманная карточка 40×50 мм и лист A4 для типографии.

Зачем отдельный модуль: A6-макет уже есть (`/api/cards/{code}/preview`), но он
для вкладыша в букет. Карманная карточка — другой предмет: её кладут в конверт,
в книгу, оставляют на столе. Её печатают не в типографии, а на офисном принтере
и режут ножницами, поэтому нужны:

* лист **A4 с метками реза** — иначе 20 карточек не выровнять;
* QR **не меньше 20 мм** — на 40 мм карточке это предел читаемости телефоном;
* **место под рукописную строку** — то, что нельзя напечатать;
* никаких внешних шрифтов и картинок: печать офлайн, шрифт системный.

Всё возвращается чистым SVG: его можно отдать в браузер и напечатать без
библиотек, и его удобно проверять тестами (размеры в миллиметрах).
"""
from __future__ import annotations

from typing import Iterable, List, Tuple

CARD_W_MM, CARD_H_MM = 40.0, 50.0
GAP_MM = 5.0
A4_W_MM, A4_H_MM = 210.0, 297.0
QR_MM = 22.0                      # читается телефоном, но влезает на 40 мм
WRITE_LINE_MM = 8.0               # под рукописную строку
PER_ROW = 4
PER_COL = 5


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))


def qr_path(text: str) -> Tuple[str, int]:
    """SVG-путь QR и число модулей. Печатается офлайн, без segno-зависимостей в SVG."""
    import segno

    qr = segno.make(text, error="m")
    # qr.matrix не содержит «тихой зоны»: без неё сканер не увидит QR на печати,
    # поэтому добавляем 2 модуля поля вручную и считаем модули вместе с ним
    border = 2
    inner = qr.matrix
    size = len(inner)
    modules = size + border * 2
    parts: List[str] = []
    for r in range(size):
        row = inner[r]
        col = 0
        while col < size:
            if not row[col]:
                col += 1
                continue
            run = 0
            while col + run < size and row[col + run]:
                run += 1
            parts.append(f"M{col + border} {r + border}h{run}")
            col += run
    return "".join(parts), modules


def card_svg(url: str, code: str, caption: str = "Открой меня",
             shop: str = "", width: float = CARD_W_MM,
             height: float = CARD_H_MM) -> str:
    """Одна карточка. Размеры в мм, поэтому в типографии не «поплывёт»."""
    path, modules = qr_path(url)
    scale = QR_MM / modules
    qr_x = (width - QR_MM) / 2
    qr_y = 6.0
    stroke = 0.9 if modules <= 41 else 0.7      # слишком плотный QR не сольётся
    return f"""<g class="card">
  <rect x="0" y="0" width="{width}" height="{height}" rx="2" fill="#fff" stroke="#111" stroke-width="0.3"/>
  <text x="{width/2}" y="4.5" text-anchor="middle" font-size="2.6" font-family="sans-serif"
        fill="#111" letter-spacing="0.2">{_esc(caption)}</text>
  <g transform="translate({qr_x:.3f} {qr_y:.3f}) scale({scale:.5f})">
    <path d="{path}" stroke="#111" stroke-width="{stroke}"/>
  </g>
  <line x1="5" y1="{qr_y + QR_MM + 3:.1f}" x2="{width - 5}" y2="{qr_y + QR_MM + 3:.1f}"
        stroke="#bbb" stroke-width="0.2" stroke-dasharray="0.8 0.8"/>
  <text x="{width/2}" y="{qr_y + QR_MM + 5.5:.1f}" text-anchor="middle" font-size="2.2"
        font-family="sans-serif" fill="#666">напиши здесь</text>
  <text x="{width/2}" y="{height - 3}" text-anchor="middle" font-size="1.8"
        font-family="sans-serif" fill="#999">{_esc(shop)} · {_esc(code)}</text>
</g>"""


def sheet_svg(urls: Iterable[str], codes: Iterable[str], shop: str = "",
              per_row: int = PER_ROW, per_col: int = PER_COL) -> str:
    """Лист A4 с карточками и метками реза. 20 карточек по умолчанию.

    Метки реза рисуются короткими штрихами по краям листа: ножницами режут по
    ним, поэтому карточки получаются одного размера без линейки.
    """
    pairs = list(zip(list(urls), list(codes)))[:per_row * per_col]
    if not pairs:
        raise ValueError("нужен хотя бы один код для листа")
    used_rows = (len(pairs) + per_row - 1) // per_row
    used_cols = min(per_row, len(pairs))
    grid_w = used_cols * CARD_W_MM + (used_cols - 1) * GAP_MM
    grid_h = used_rows * CARD_H_MM + (used_rows - 1) * GAP_MM
    safe = 5.0  # непечатаемая зона бытовых принтеров
    off_x = max(safe, (A4_W_MM - grid_w) / 2)
    off_y = max(safe, (A4_H_MM - grid_h) / 2)

    cards: List[str] = []
    marks: List[str] = []
    for index, (url, code) in enumerate(pairs):
        row, col = divmod(index, per_row)
        x = off_x + col * (CARD_W_MM + GAP_MM)
        y = off_y + row * (CARD_H_MM + GAP_MM)
        cards.append(f'<g transform="translate({x:.2f} {y:.2f})">'
                     + card_svg(url, code, shop=shop) + "</g>")
        # метки реза: по 3 мм снаружи с каждого угла
        for dx, dy, sx, sy in ((0, 0, -1, -1), (CARD_W_MM, 0, 1, -1),
                               (0, CARD_H_MM, -1, 1), (CARD_W_MM, CARD_H_MM, 1, 1)):
            mx, my = x + dx, y + dy
            marks.append(f'<path d="M{mx - 2.5 * sx} {my}h{2.5 * sx}" stroke="#111" stroke-width="0.2"/>')
            marks.append(f'<path d="M{mx} {my - 2.5 * sy}v{2.5 * sy}" stroke="#111" stroke-width="0.2"/>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{A4_W_MM}mm" height="{A4_H_MM}mm"
     viewBox="0 0 {A4_W_MM} {A4_H_MM}">
  <rect width="100%" height="100%" fill="#fff"/>
  <g>{''.join(marks)}</g>
  <g>{''.join(cards)}</g>
</svg>"""
