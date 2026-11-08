"""The bot decides the same on the real replay sample (T-92, RNF-22, RNF-23): every
`decide` call of the run, in order, against the sequence recorded before the
optimisations of M19. A legitimate change of the bot's behaviour regenerates it with
`uv run python -m tests.services.test_bot_decisions_replay`."""

from pathlib import Path

import pytest

from xbt_terminal.__main__ import replay_report
from xbt_terminal.config import load_settings
from xbt_terminal.services import bot as bot_service

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "replay"
GOLDEN = FIXTURES / "decisiones.txt"


def decisions(tmp: Path, patch) -> list[str]:
    """One line per `decide` call: virtual time and the result."""
    seen: list[str] = []
    original = bot_service.decide

    def recording(views, state, limits, now, target=None):
        result = original(views, state, limits, now, target)
        seen.append(f"{now}\t{result!r}")
        return result

    patch(bot_service, "decide", recording)
    settings = load_settings(FIXTURES / "config.toml", tmp / "no.env", False, replay=True)
    replay_report(settings, FIXTURES / "muestra.jsonl")
    return seen


@pytest.mark.spec("RNF-22", "RNF-23", "RF-19", "RF-20")
def test_same_decisions_as_before_on_real_sample(tmp_path, monkeypatch):
    got = decisions(tmp_path, monkeypatch.setattr)
    want = GOLDEN.read_text(encoding="utf-8").splitlines()
    assert len(got) == len(want)
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        assert g == w, f"decision {i} changed"


if __name__ == "__main__":
    import tempfile

    def setattr_(obj, name, value):
        setattr(obj, name, value)

    with tempfile.TemporaryDirectory() as tmp:
        lines = decisions(Path(tmp), setattr_)
    GOLDEN.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(lines)} decisions -> {GOLDEN}")
