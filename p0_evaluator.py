"""
P0 evaluator: inspect a KiCad board, move footprints, autoroute with
Freerouting, and score the result.

Run it with the Python that ships with KiCad (it includes the `pcbnew` module):
  Windows : open "KiCad 9.0 Command Prompt" from the Start menu, then `python p0_evaluator.py ...`
  macOS   : /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 p0_evaluator.py ...
  Linux   : python3 p0_evaluator.py ...   (system Python, with KiCad installed from your package manager)

Usage:
  python p0_evaluator.py list  path/to/board.kicad_pcb
  python p0_evaluator.py eval  path/to/board.kicad_pcb               # score the current placement
  python p0_evaluator.py eval  path/to/board.kicad_pcb --scramble 0  # score a random ("careless") placement

Settings (environment variables):
  FREEROUTING_JAR   path to freerouting-<version>.jar   (default: freerouting.jar in this folder)
  FREEROUTING_ARGS  extra Freerouting flags, e.g. "--gui.enabled=false" if a window pops up
  KICAD_CLI         path to kicad-cli                   (default: kicad-cli on your PATH)

Function and flag names shift between KiCad / Freerouting versions. If a call
fails, check the KiCad 9 Python API docs and `java -jar freerouting.jar --help`.
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import tempfile

import pcbnew

NM_PER_MM = 1_000_000  # KiCad stores all coordinates in nanometres

FREEROUTING_JAR = os.environ.get("FREEROUTING_JAR", "freerouting.jar")
FREEROUTING_ARGS = os.environ.get("FREEROUTING_ARGS", "").split()
KICAD_CLI = os.environ.get("KICAD_CLI", "kicad-cli")


# ---------------------------------------------------------------- inspect

def describe(board):
    """Return (parts, nets). parts: one dict per footprint. nets: net name -> list of 'REF.pad'."""
    parts = []
    for fp in board.GetFootprints():
        pos, box = fp.GetPosition(), fp.GetBoundingBox()
        parts.append({
            "ref": fp.GetReference(),
            "x_mm": pos.x / NM_PER_MM,
            "y_mm": pos.y / NM_PER_MM,
            "rot_deg": fp.GetOrientationDegrees(),
            "w_mm": box.GetWidth() / NM_PER_MM,
            "h_mm": box.GetHeight() / NM_PER_MM,
            "side": "back" if fp.IsFlipped() else "front",
            "locked": fp.IsLocked(),
            "pads": {p.GetNumber(): p.GetNetname() for p in fp.Pads()},
        })
    nets = {}
    for part in parts:
        for pad, net in part["pads"].items():
            if net:
                nets.setdefault(net, []).append(f"{part['ref']}.{pad}")
    return parts, nets


# ---------------------------------------------------------------- edit

def place(board, positions):
    """positions: {ref: (x_mm, y_mm, rot_deg)}. Footprints not listed stay where they are."""
    by_ref = {fp.GetReference(): fp for fp in board.GetFootprints()}
    for ref, (x, y, rot) in positions.items():
        fp = by_ref[ref]
        fp.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        fp.SetOrientationDegrees(rot)


def scramble(board, seed=0, margin=0.1):
    """A deliberately careless placement: every unlocked part at a random spot inside the outline."""
    rng = random.Random(seed)
    box = board.GetBoardEdgesBoundingBox()
    mx, my = box.GetWidth() * margin, box.GetHeight() * margin
    x0, x1 = box.GetX() + mx, box.GetRight() - mx
    y0, y1 = box.GetY() + my, box.GetBottom() - my
    return {
        fp.GetReference(): (rng.uniform(x0, x1) / NM_PER_MM,
                            rng.uniform(y0, y1) / NM_PER_MM,
                            rng.choice([0, 90, 180, 270]))
        for fp in board.GetFootprints() if not fp.IsLocked()
    }


def strip_tracks(board):
    """Remove all tracks and vias so Freerouting starts from an unrouted board."""
    for item in list(board.GetTracks()):
        board.Remove(item)


def save_with_project(board, src_pcb, dst_pcb):
    """Save the board and copy its .kicad_pro next to it, so DRC uses the project's design rules."""
    pcbnew.SaveBoard(dst_pcb, board)
    src_pro = os.path.splitext(src_pcb)[0] + ".kicad_pro"
    if os.path.exists(src_pro):
        shutil.copy(src_pro, os.path.splitext(dst_pcb)[0] + ".kicad_pro")


# ---------------------------------------------------------------- route + check

def autoroute(board, workdir, passes=20, timeout_s=600):
    dsn = os.path.join(workdir, "board.dsn")
    ses = os.path.join(workdir, "board.ses")
    pcbnew.ExportSpecctraDSN(board, dsn)
    if not os.path.exists(dsn):
        raise RuntimeError("DSN export failed (does the board have a closed Edge.Cuts outline?)")
    cmd = ["java", "-jar", FREEROUTING_JAR, "-de", dsn, "-do", ses, "-mp", str(passes)] + FREEROUTING_ARGS
    subprocess.run(cmd, check=True, timeout=timeout_s)
    if not os.path.exists(ses):
        raise RuntimeError("Freerouting produced no .ses file")
    pcbnew.ImportSpecctraSES(board, ses)


def run_drc(pcb_path, report_path):
    """Return (unconnected connections, DRC errors) from kicad-cli's JSON report.

    If your board uses copper zones (e.g. a GND pour), add "--refill-zones"
    (KiCad 9+) so zone connections count as routed.
    """
    cmd = [KICAD_CLI, "pcb", "drc", "--format", "json", "--severity-error",
           "--output", report_path, pcb_path]
    subprocess.run(cmd, check=False)
    with open(report_path) as f:
        report = json.load(f)
    return len(report.get("unconnected_items", [])), len(report.get("violations", []))


def copper_stats(board):
    length_nm, vias = 0, 0
    for item in board.GetTracks():
        if isinstance(item, pcbnew.PCB_VIA):
            vias += 1
        else:
            length_nm += item.GetLength()
    return length_nm / NM_PER_MM, vias


# ---------------------------------------------------------------- the P0 deliverable

def evaluate(pcb_path, positions=None, passes=20, out_pcb=None):
    """Place parts, autoroute, and score. This is P0's 'Done when' function."""
    pcb_path = os.path.abspath(pcb_path)
    workdir = tempfile.mkdtemp(prefix="p0_")
    board = pcbnew.LoadBoard(pcb_path)
    strip_tracks(board)
    if positions:
        place(board, positions)

    unrouted_pcb = os.path.join(workdir, "unrouted.kicad_pcb")
    save_with_project(board, pcb_path, unrouted_pcb)
    connections, _ = run_drc(unrouted_pcb, os.path.join(workdir, "drc_before.json"))

    autoroute(board, workdir, passes)

    routed_pcb = os.path.abspath(out_pcb) if out_pcb else os.path.join(workdir, "routed.kicad_pcb")
    save_with_project(board, pcb_path, routed_pcb)
    unrouted, drc_errors = run_drc(routed_pcb, os.path.join(workdir, "drc_after.json"))
    wirelength_mm, vias = copper_stats(board)

    return {
        "routed_pct": round(100 * (1 - unrouted / connections), 1) if connections else 100.0,
        "connections": connections,
        "unrouted": unrouted,
        "wirelength_mm": round(wirelength_mm, 1),
        "vias": vias,
        "drc_errors": drc_errors,
        "routed_board": routed_pcb,
    }


# ---------------------------------------------------------------- command line

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["list", "eval"])
    ap.add_argument("board")
    ap.add_argument("--scramble", type=int, metavar="SEED", help="score a random placement instead")
    ap.add_argument("--passes", type=int, default=20)
    ap.add_argument("--out", help="where to save the routed board")
    args = ap.parse_args()

    if args.command == "list":
        parts, nets = describe(pcbnew.LoadBoard(args.board))
        print(f"{len(parts)} footprints, {len(nets)} nets\n")
        for p in parts:
            print(f"{p['ref']:8} ({p['x_mm']:7.2f}, {p['y_mm']:7.2f}) mm  rot {p['rot_deg']:5.0f}  "
                  f"{p['w_mm']:5.1f} x {p['h_mm']:5.1f} mm  {p['side']}{'  LOCKED' if p['locked'] else ''}")
        print()
        for net, pins in sorted(nets.items()):
            print(f"{net:24} {', '.join(pins)}")
        return

    positions = scramble(pcbnew.LoadBoard(args.board), seed=args.scramble) if args.scramble is not None else None
    print(json.dumps(evaluate(args.board, positions, args.passes, args.out), indent=2))


if __name__ == "__main__":
    main()