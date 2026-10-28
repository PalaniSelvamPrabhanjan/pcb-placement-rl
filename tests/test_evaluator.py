"""P0 test: the demo board's hand-made placement should beat a random one.

Needs KiCad's Python (pcbnew), kicad-cli, Java and a Freerouting jar
(FREEROUTING_JAR, default ./freerouting.jar); skipped if the jar is missing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
pcbnew = pytest.importorskip("pcbnew")
import p0_evaluator as ev  # noqa: E402

BOARD = os.path.join(os.path.dirname(__file__), "..", "boards", "demo", "ecc83", "ecc83-pp.kicad_pcb")

pytestmark = pytest.mark.skipif(not os.path.exists(ev.FREEROUTING_JAR),
                                reason=f"Freerouting jar not found: {ev.FREEROUTING_JAR}")


def test_original_placement_beats_scramble():
    original = ev.evaluate(BOARD)
    # Fixed seed so the "careless" placement is the same every run.
    scrambled = ev.evaluate(BOARD, ev.scramble(pcbnew.LoadBoard(BOARD), seed=0))

    assert original["routed_pct"] >= scrambled["routed_pct"]
    assert original["wirelength_mm"] < scrambled["wirelength_mm"]
