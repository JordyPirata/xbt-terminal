"""QR de un texto dibujado con caracteres de medio bloque.

Dos módulos por carácter en vertical (`▀▄█` y espacio). Los caracteres pintan
los módulos claros: el panel los muestra en blanco sobre negro con colores
explícitos, para que el QR salga oscuro sobre claro con cualquier tema (D-52).

Spec: RF-28 · design.md §6.11 · Tareas: T-44
"""

from __future__ import annotations

import segno

BORDER = 2
# (arriba claro, abajo claro) → carácter
_CHARS = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}


def qr_lines(text: str) -> list[str]:
    """Líneas del QR de `text` (corrección `L`, sin micro QR, margen 2)."""
    matrix = [
        [not dark for dark in row]
        for row in segno.make(text, error="l", micro=False).matrix_iter(border=BORDER)
    ]
    if len(matrix) % 2:
        matrix.append([True] * len(matrix[0]))  # media fila más de margen
    return [
        "".join(_CHARS[top, bottom] for top, bottom in zip(upper, lower, strict=True))
        for upper, lower in zip(matrix[::2], matrix[1::2], strict=True)
    ]
