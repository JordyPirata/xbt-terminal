"""QR de la dirección en texto (T-44, RF-28, D-52)."""

import pytest
import segno

from btcb2_terminal.ui.qr import qr_lines

ADDRESS = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
TOP = {" ": (0, 0), "▀": (1, 0), "▄": (0, 1), "█": (1, 1)}  # carácter → (arriba, abajo) claros


def decode(lines: list[str]) -> list[list[int]]:
    """Vuelve de los caracteres a módulos (1 = oscuro)."""
    rows: list[list[int]] = [[], []]
    out: list[list[int]] = []
    for line in lines:
        rows = [[], []]
        for ch in line:
            top, bottom = TOP[ch]
            # el carácter pinta en claro (blanco): oscuro es lo no pintado
            rows[0].append(1 - top)
            rows[1].append(1 - bottom)
        out.extend(rows)
    return out


@pytest.mark.spec("RF-28", "CA-13")
class TestQr:
    def test_coincide_modulo_a_modulo_con_segno(self):
        lines = qr_lines(ADDRESS)
        qr = segno.make(ADDRESS, error="l", micro=False)
        expected = [list(r) for r in qr.matrix_iter(border=2)]
        got = decode(lines)
        assert got[: len(expected)] == expected
        # número impar de filas: la media fila que sobra es margen claro
        assert got[len(expected) :] in ([], [[0] * len(expected[0])])

    def test_tamano_para_una_direccion_bc1q(self):
        lines = qr_lines(ADDRESS)
        assert len(lines) == 17 and {len(ln) for ln in lines} == {33}

    def test_otra_direccion_otro_qr(self):
        assert qr_lines(ADDRESS) != qr_lines(ADDRESS[:-1] + "x")
