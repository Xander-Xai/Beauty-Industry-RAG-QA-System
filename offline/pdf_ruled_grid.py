"""Conservative fallback for a single fully ruled rectangular PDF table."""

from __future__ import annotations

from bisect import bisect_right
from itertools import pairwise

_TOL = 1.0
_MAX_LINES = 256
_MAX_WORDS = 10000


def _coordinates(values: list[float]) -> list[float]:
    result: list[float] = []
    for value in sorted(values):
        if not result or value - result[-1] > _TOL:
            result.append(value)
    return result


def extract_ruled_grid(page) -> list[list[str]] | None:
    """Return rows only for a complete, unambiguous grid; otherwise abstain.

    Deliberately excludes merged cells, scanned tables, multiple grids and
    unrelated vector graphics. Never use this as evidence of table recall.
    """
    drawings = getattr(page, "get_drawings", None)
    get_text = getattr(page, "get_text", None)
    if not callable(drawings) or not callable(get_text):
        return None
    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    for drawing in drawings():
        for item in drawing.get("items", []):
            if item[0] != "l":
                return None
            p, q = item[1], item[2]
            if abs(p.y - q.y) <= _TOL and abs(p.x - q.x) > 2 * _TOL:
                horizontal.append((p.y, min(p.x, q.x), max(p.x, q.x)))
            elif abs(p.x - q.x) <= _TOL and abs(p.y - q.y) > 2 * _TOL:
                vertical.append((p.x, min(p.y, q.y), max(p.y, q.y)))
            else:
                return None
            if len(horizontal) + len(vertical) > _MAX_LINES:
                return None
    xs = _coordinates([x for x, _, _ in vertical])
    ys = _coordinates([y for y, _, _ in horizontal])
    if not (3 <= len(xs) <= 31 and 3 <= len(ys) <= 201):
        return None
    if len(xs) != len(vertical) or len(ys) != len(horizontal):
        return None
    if any(abs(lo - xs[0]) > _TOL or abs(hi - xs[-1]) > _TOL for _, lo, hi in horizontal):
        return None
    if any(abs(lo - ys[0]) > _TOL or abs(hi - ys[-1]) > _TOL for _, lo, hi in vertical):
        return None
    if any(b - a <= 2 * _TOL for edges in (xs, ys) for a, b in pairwise(edges)):
        return None
    words = get_text("words", sort=True)
    if len(words) > _MAX_WORDS:
        return None
    cells: list[list[list[str]]] = [[[] for _ in xs[1:]] for _ in ys[1:]]
    for word in words:
        x0, y0, x1, y1, text = word[:5]
        if x1 <= xs[0] or x0 >= xs[-1] or y1 <= ys[0] or y0 >= ys[-1]:
            continue
        col = bisect_right(xs, x0) - 1
        row = bisect_right(ys, y0) - 1
        if not (0 <= col < len(xs) - 1 and 0 <= row < len(ys) - 1):
            return None
        if x1 > xs[col + 1] or y1 > ys[row + 1] or x1 <= x0 or y1 <= y0:
            return None
        cells[row][col].append(str(text))
    rows = [[" ".join(cell) for cell in row] for row in cells]
    if any(not cell for row in rows for cell in row):
        return None
    return rows
