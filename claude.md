# PCB placement RL

Goal: an RL agent (MaskablePPO, Stable-Baselines3) places PCB parts on a coarse grid; Freerouting routes the result; we score routed %, wirelength, vias and DRC errors against random, greedy and simulated-annealing placements.

- Python: KiCad's bundled Python for anything that imports pcbnew; a separate venv for RL training.
- Evaluator: p0_evaluator.py, evaluate(board, positions) -> metrics.
- Environments: Gymnasium; always run check_env; action_masks() enforces no overlap, board bounds and connectors on edges.
- Reward: per-step change in wirelength, normalised by board size.
- Keep code small and commented. I'm learning RL, so explain non-obvious choices.
- Every feature ships with a test. Fix random seeds; log to TensorBoard.

Current phase: P0 (see tracker).