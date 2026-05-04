#!/usr/bin/env python
"""Calibrate attribution engine parameters against ground truth labels.

Assembles event contexts from the local database once, then sweeps a parameter
grid and ranks combinations by binary log-loss. Supports both the heuristic and
physics attribution engines. No external dependencies beyond the project itself.

Usage
-----
    # Calibrate heuristic engine (default)
    python scripts/calibrate_engine.py data/ground_truth.csv

    # Calibrate physics engine (pore-pressure diffusion)
    python scripts/calibrate_engine.py data/ground_truth.csv --engine physics

    python scripts/calibrate_engine.py data/ground_truth.csv --top 10
    python scripts/calibrate_engine.py data/ground_truth.csv --output results.json

Ground truth CSV format (header required)
-----------------------------------------
    event_id,driver
    tx2025iqwk,swd
    us7000abcd,frac

Accepted driver values: "swd" or "frac". Rows with "indeterminate" are skipped
because log-loss requires a definite ground truth class.

Output
------
Prints a ranked table of the top N parameter sets. Optionally writes full results
to a JSON file for further analysis.

After a successful run
----------------------
  Heuristic: copy best-fit values into attribution_service.py module constants.
  Physics:   update d_swd_m2_s default in physics_attribution_service.py.
  Then bump _ENGINE to the next version label in the relevant file.
"""

import sys
import csv
import json
import math
import argparse
import itertools
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.core.database import SessionLocal, init_db
from app.repositories.fracfocus_repository import FracFocusRepository
from app.repositories.iris_repository import IRISStationRepository
from app.repositories.seismic_repository import SeismicEventRepository
from app.repositories.swd_repository import SWDRepository
from app.services.attribution_service import HeuristicAttributionService
from app.services.physics_attribution_service import PhysicsAttributionService
from app.services.event_context_service import EventContextService

# ---------------------------------------------------------------------------
# Parameter grids
# Extend or narrow these ranges based on your domain knowledge.
# Total combinations = product of all list lengths.
# rate_boost_cap excluded from both grids — it's a hard ceiling, not smooth.
# ---------------------------------------------------------------------------

# Heuristic engine grid  (6×5×6×5 = 900 combinations)
HEURISTIC_GRID: dict[str, list[float]] = {
    "swd_lambda_km":    [5.0, 8.0, 10.0, 12.0, 15.0, 20.0],
    "frac_lambda_km":   [1.0, 2.0, 3.0, 4.0, 5.0],
    "time_lambda_days": [90.0, 180.0, 270.0, 365.0, 548.0, 730.0],
    "depth_sigma_km":   [1.0, 2.0, 3.0, 4.0, 5.0],
}

# Physics engine grid  (6×5×5 = 150 combinations)
# d_swd_m2_s spans the published range for Delaware Basin formations (0.01–5 m²/s).
PHYSICS_GRID: dict[str, list[float]] = {
    "d_swd_m2_s":     [0.05, 0.1, 0.2, 0.5, 1.0, 2.0],
    "frac_lambda_km": [1.0, 2.0, 3.0, 4.0, 5.0],
    "depth_sigma_km": [1.0, 2.0, 3.0, 4.0, 5.0],
}

HEURISTIC_DEFAULTS = {
    "swd_lambda_km": 10.0,
    "frac_lambda_km": 3.0,
    "time_lambda_days": 365.0,
    "depth_sigma_km": 3.0,
}

PHYSICS_DEFAULTS = {
    "d_swd_m2_s": 0.5,
    "frac_lambda_km": 3.0,
    "depth_sigma_km": 3.0,
}

_EPS = 1e-9  # guard against log(0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_labels(path: str) -> dict[str, str]:
    """Load event_id → driver from CSV. Skips indeterminate rows."""
    labels: dict[str, str] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            event_id = row["event_id"].strip()
            driver = row["driver"].strip().lower()
            if driver in ("swd", "frac"):
                labels[event_id] = driver
            elif driver not in ("indeterminate", ""):
                print(f"  Warning: unknown driver '{driver}' for {event_id} — skipped")
    return labels


def binary_log_loss(p_swd: float, true_driver: str) -> float:
    """Log-loss contribution for one event.

    p_swd is the model's probability that SWD is the driver (0–1).
    true_driver is the ground truth label ("swd" or "frac").
    """
    p = p_swd if true_driver == "swd" else (1.0 - p_swd)
    return -math.log(max(p, _EPS))


def evaluate(contexts, labels: dict[str, str], engine_cls, **params) -> dict:
    """Score every context with the given parameter set; return loss + accuracy."""
    engine = engine_cls(**params)
    total_loss = 0.0
    correct = 0
    n = len(contexts)

    for event_id, ctx in contexts.items():
        result = engine.score(ctx)
        total = result.swd_score + result.frac_score
        p_swd = result.swd_score / total if total > 0 else 0.5

        total_loss += binary_log_loss(p_swd, labels[event_id])
        if result.likely_driver == labels[event_id]:
            correct += 1

    return {
        "log_loss": round(total_loss / n, 6),
        "accuracy": round(correct / n, 4),
        **params,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate attribution engine parameters via grid search + log-loss"
    )
    parser.add_argument("ground_truth", help="CSV file: event_id,driver")
    parser.add_argument(
        "--engine",
        choices=["heuristic", "physics"],
        default="heuristic",
        help="Which engine to calibrate (default: heuristic)",
    )
    parser.add_argument("--top", type=int, default=10, help="Rows to display (default: 10)")
    parser.add_argument("--output", help="Write full ranked results to this JSON file")
    args = parser.parse_args()

    # Select engine, grid, and defaults based on --engine flag
    if args.engine == "physics":
        engine_cls = PhysicsAttributionService
        grid       = PHYSICS_GRID
        defaults   = PHYSICS_DEFAULTS
    else:
        engine_cls = HeuristicAttributionService
        grid       = HEURISTIC_GRID
        defaults   = HEURISTIC_DEFAULTS

    # --- Load labels ---
    print(f"Loading ground truth from {args.ground_truth} …")
    labels = load_labels(args.ground_truth)
    if not labels:
        print("No valid labels found. Ensure the CSV has driver values of 'swd' or 'frac'.")
        sys.exit(1)
    n_swd  = sum(v == "swd"  for v in labels.values())
    n_frac = sum(v == "frac" for v in labels.values())
    print(f"  {len(labels)} labeled events: {n_swd} SWD, {n_frac} frac\n")

    # --- Assemble contexts (once — the expensive step) ---
    print("Initialising database …")
    init_db()
    settings = get_settings()
    db = SessionLocal()
    try:
        ctx_service = EventContextService(
            seismic_repo=SeismicEventRepository(db),
            swd_repo=SWDRepository(db),
            fracfocus_repo=FracFocusRepository(db),
            iris_repo=IRISStationRepository(db),
            settings=settings,
        )
        print("Assembling event contexts …")
        contexts = {}
        missing = []
        for event_id in labels:
            ctx = ctx_service.assemble(event_id)
            if ctx is None:
                missing.append(event_id)
            else:
                contexts[event_id] = ctx
    finally:
        db.close()

    if missing:
        print(f"  Warning: {len(missing)} events not found in database — {missing}")
    if not contexts:
        print("No contexts could be assembled. Load seismic data first.")
        sys.exit(1)

    # Restrict labels to events we could assemble
    labels = {k: v for k, v in labels.items() if k in contexts}
    print(f"  {len(contexts)} contexts assembled successfully\n")

    # --- Grid search ---
    keys   = list(grid.keys())
    combos = list(itertools.product(*grid.values()))
    total  = len(combos)
    print(f"Engine: {args.engine}  |  Searching {total} parameter combinations …")

    results = []
    for i, combo in enumerate(combos, 1):
        params  = dict(zip(keys, combo))
        metrics = evaluate(contexts, labels, engine_cls, **params)
        results.append(metrics)
        if i % 200 == 0 or i == total:
            print(f"  {i}/{total} evaluated …")

    results.sort(key=lambda r: r["log_loss"])

    # --- Current defaults baseline ---
    baseline = evaluate(contexts, labels, engine_cls, **defaults)

    # --- Report ---
    top_n       = results[: args.top]
    param_keys  = list(defaults.keys())
    sep         = "─" * 90
    print(f"\n{sep}")
    print(f"Top {args.top} parameter sets by log-loss (lower = better fit)  [engine: {args.engine}]\n")
    param_header = "  ".join(f"{k:<14}" for k in param_keys)
    print(f"{'Rank':<5} {'log_loss':<11} {'accuracy':<10} {param_header}")
    print(sep)
    for rank, row in enumerate(top_n, 1):
        param_vals = "  ".join(f"{row.get(k, '—'):<14}" for k in param_keys)
        print(f"{rank:<5} {row['log_loss']:<11.6f} {row['accuracy']:<10.4f} {param_vals}")
    print(sep)

    defaults_str = ", ".join(f"{k}={v}" for k, v in defaults.items())
    best         = top_n[0]
    improvement  = baseline["log_loss"] - best["log_loss"]
    print(f"\nCurrent defaults  →  log_loss={baseline['log_loss']:.6f}  accuracy={baseline['accuracy']:.4f}  ({defaults_str})")
    print(f"Best combination  →  log_loss={best['log_loss']:.6f}  accuracy={best['accuracy']:.4f}  (Δlog_loss={improvement:+.6f})")

    if args.output:
        payload = {
            "engine": args.engine,
            "n_events": len(labels),
            "n_swd": n_swd,
            "n_frac": n_frac,
            "grid": grid,
            "current_defaults": baseline,
            "all_results": results,
        }
        Path(args.output).write_text(json.dumps(payload, indent=2))
        print(f"\nFull results written to {args.output}")

    target_file = "physics_attribution_service.py" if args.engine == "physics" else "attribution_service.py"
    print(f"\nNext step: copy the best-fit values into {target_file} and bump _ENGINE to the next version label.")


if __name__ == "__main__":
    main()
