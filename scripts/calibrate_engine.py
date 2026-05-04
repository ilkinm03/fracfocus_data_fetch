#!/usr/bin/env python
"""Calibrate heuristic attribution engine parameters against ground truth labels.

Assembles event contexts from the local database once, then sweeps a parameter
grid and ranks combinations by binary log-loss. No external dependencies beyond
the project itself.

Usage
-----
    python scripts/calibrate_engine.py data/ground_truth.csv
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
to a JSON file for further analysis. After a successful run, copy the best-fit
values into attribution_service.py module constants and bump the engine label.
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
from app.services.event_context_service import EventContextService

# ---------------------------------------------------------------------------
# Parameter grid
# Extend or narrow these ranges based on your domain knowledge.
# Total combinations = product of all list lengths.
# Current defaults: swd_λ=10, frac_λ=3, time_λ=365, depth_σ=3, cap=3
# ---------------------------------------------------------------------------
GRID: dict[str, list[float]] = {
    "swd_lambda_km":    [5.0, 8.0, 10.0, 12.0, 15.0, 20.0],
    "frac_lambda_km":   [1.0, 2.0, 3.0, 4.0, 5.0],
    "time_lambda_days": [90.0, 180.0, 270.0, 365.0, 548.0, 730.0],
    "depth_sigma_km":   [1.0, 2.0, 3.0, 4.0, 5.0],
    # rate_boost_cap excluded from grid — it's a hard ceiling, not a smooth parameter
}

_EPS = 1e-9  # guard against log(0)

# Production defaults (mirrors attribution_service.py module constants)
DEFAULTS = {
    "swd_lambda_km": 10.0,
    "frac_lambda_km": 3.0,
    "time_lambda_days": 365.0,
    "depth_sigma_km": 3.0,
}


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


def evaluate(contexts, labels: dict[str, str], **params) -> dict:
    """Score every context with the given parameter set; return loss + accuracy."""
    engine = HeuristicAttributionService(**params)
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
        **{k: v for k, v in params.items() if k in GRID},
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate attribution engine parameters via grid search + log-loss"
    )
    parser.add_argument("ground_truth", help="CSV file: event_id,driver")
    parser.add_argument("--top", type=int, default=10, help="Rows to display (default: 10)")
    parser.add_argument("--output", help="Write full ranked results to this JSON file")
    args = parser.parse_args()

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
    keys   = list(GRID.keys())
    combos = list(itertools.product(*GRID.values()))
    total  = len(combos)
    print(f"Searching {total} parameter combinations …")

    results = []
    for i, combo in enumerate(combos, 1):
        params  = dict(zip(keys, combo))
        metrics = evaluate(contexts, labels, **params)
        results.append(metrics)
        if i % 200 == 0 or i == total:
            print(f"  {i}/{total} evaluated …")

    results.sort(key=lambda r: r["log_loss"])

    # --- Current defaults baseline ---
    baseline = evaluate(contexts, labels, **DEFAULTS)

    # --- Report ---
    top_n = results[: args.top]
    sep   = "─" * 82
    print(f"\n{sep}")
    print(f"Top {args.top} parameter sets by log-loss (lower = better fit)\n")
    header = f"{'Rank':<5} {'log_loss':<11} {'accuracy':<10} {'swd_λ_km':<11} {'frac_λ_km':<11} {'time_λ_d':<11} {'depth_σ_km'}"
    print(header)
    print(sep)
    for rank, r in enumerate(top_n, 1):
        print(
            f"{rank:<5} {r['log_loss']:<11.6f} {r['accuracy']:<10.4f} "
            f"{r['swd_lambda_km']:<11} {r['frac_lambda_km']:<11} "
            f"{r['time_lambda_days']:<11} {r['depth_sigma_km']}"
        )
    print(sep)
    print(
        f"\nCurrent defaults  →  "
        f"log_loss={baseline['log_loss']:.6f}  accuracy={baseline['accuracy']:.4f}"
        f"  (swd_λ={DEFAULTS['swd_lambda_km']}, frac_λ={DEFAULTS['frac_lambda_km']}, "
        f"time_λ={DEFAULTS['time_lambda_days']}, depth_σ={DEFAULTS['depth_sigma_km']})"
    )
    best = top_n[0]
    improvement = baseline["log_loss"] - best["log_loss"]
    print(
        f"Best combination  →  "
        f"log_loss={best['log_loss']:.6f}  accuracy={best['accuracy']:.4f}"
        f"  (Δlog_loss={improvement:+.6f})"
    )

    if args.output:
        payload = {
            "n_events": len(labels),
            "n_swd": n_swd,
            "n_frac": n_frac,
            "grid": GRID,
            "current_defaults": baseline,
            "all_results": results,
        }
        Path(args.output).write_text(json.dumps(payload, indent=2))
        print(f"\nFull results written to {args.output}")

    print(
        "\nNext step: copy the best-fit values into attribution_service.py "
        "module constants and bump _ENGINE to the next version label."
    )


if __name__ == "__main__":
    main()
