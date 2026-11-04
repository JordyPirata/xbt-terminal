"""QR of a text drawn with half block characters.

Two modules per character vertically (`▀▄█` and space). Characters paint the light
modules: the panel shows them white on black with explicit colours, so the QR comes
out dark on light with any theme (D-52).

Spec: RF-28 · design.md §6.11 · Tasks: T-44
"""

from __future__ import annotations

import segno

BORDER = 2
# (light above, light below) → character
_CHARS = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}


def qr_lines(text: str) -> list[str]:
    """QR lines of `text` (`L` correction, no micro QR, margin 2)."""
    matrix = [
        [not dark for dark in row]
        for row in segno.make(text, error="l", micro=False).matrix_iter(border=BORDER)
    ]
    if len(matrix) % 2:
        matrix.append([True] * len(matrix[0]))  # half a row more of margin
    return [
        "".join(_CHARS[top, bottom] for top, bottom in zip(upper, lower, strict=True))
        for upper, lower in zip(matrix[::2], matrix[1::2], strict=True)
    ]
