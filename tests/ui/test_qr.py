"""Address QR as text (T-44, RF-28, D-52)."""

import pytest
import segno

from xbt_terminal.ui.qr import qr_lines

ADDRESS = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
TOP = {" ": (0, 0), "▀": (1, 0), "▄": (0, 1), "█": (1, 1)}  # character → (above, below) light


def decode(lines: list[str]) -> list[list[int]]:
    """Back from characters to modules (1 = dark)."""
    rows: list[list[int]] = [[], []]
    out: list[list[int]] = []
    for line in lines:
        rows = [[], []]
        for ch in line:
            top, bottom = TOP[ch]
            # the character paints light (white): dark is the unpainted
            rows[0].append(1 - top)
            rows[1].append(1 - bottom)
        out.extend(rows)
    return out


@pytest.mark.spec("RF-28", "CA-13")
class TestQr:
    def test_matches_segno_module_by_module(self):
        lines = qr_lines(ADDRESS)
        qr = segno.make(ADDRESS, error="l", micro=False)
        expected = [list(r) for r in qr.matrix_iter(border=2)]
        got = decode(lines)
        assert got[: len(expected)] == expected
        # odd number of rows: the half row left over is light margin
        assert got[len(expected) :] in ([], [[0] * len(expected[0])])

    def test_size_for_a_bc1q_address(self):
        lines = qr_lines(ADDRESS)
        assert len(lines) == 17 and {len(ln) for ln in lines} == {33}

    def test_other_address_other_qr(self):
        assert qr_lines(ADDRESS) != qr_lines(ADDRESS[:-1] + "x")
