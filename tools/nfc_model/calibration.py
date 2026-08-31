"""Calibration interfaces for NanoVNA and repeated HomeKey trials."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np


def _load_records(path: str | Path) -> list[dict]:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as stream:
            return list(csv.DictReader(stream))
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["measurements"] if isinstance(data, dict) and "measurements" in data else data


def calibrate_vna(path: str | Path) -> dict:
    """Normalize installed f0/Q/L/S11 records to the in-air Test A record."""
    records = _load_records(path)
    if not records:
        raise ValueError("no VNA measurements supplied")
    normalized = []
    air = records[0]
    for required in ("f0_MHz", "Q", "L_uH"):
        if required not in air:
            raise ValueError(f"VNA record requires {required}")
    for row in records:
        normalized.append({
            "test": row.get("test", f"Test {len(normalized) + 1}"),
            "f0_MHz": float(row["f0_MHz"]),
            "Q": float(row["Q"]),
            "L_uH": float(row["L_uH"]),
            "S11_min_dB": float(row["S11_min_dB"]) if row.get("S11_min_dB") not in {None, ""} else None,
            "L_ratio_to_air": float(row["L_uH"]) / float(air["L_uH"]),
            "Q_ratio_to_air": float(row["Q"]) / float(air["Q"]),
            "f0_ratio_to_air": float(row["f0_MHz"]) / float(air["f0_MHz"]),
        })
    return {
        "schema_version": 1,
        "reference_test": normalized[0]["test"],
        "normalized_measurements": normalized,
        "recommended_use": "map named mechanical states to measured L/Q ratios; refit metal/ferrite priors only with known geometry",
        "s11_trace_status": "summary only; provide Touchstone .s1p/.s2p for full complex-network fitting",
    }


def fit_homekey_threshold(path: str | Path) -> dict:
    """Fit a binomial logistic curve only when measured margin/trial data exist."""
    records = _load_records(path)
    if len(records) < 3:
        raise ValueError("at least three measured operating points are required")
    x = np.asarray([float(row["model_margin_db"]) for row in records], dtype=float)
    successes = np.asarray([float(row["successes"]) for row in records], dtype=float)
    trials = np.asarray([float(row["trials"]) for row in records], dtype=float)
    if np.any(trials <= 0) or np.any(successes < 0) or np.any(successes > trials):
        raise ValueError("invalid binomial success/trial counts")
    design = np.column_stack([np.ones_like(x), x])
    beta = np.asarray([0.0, 0.3])
    for _ in range(40):
        eta = np.clip(design @ beta, -30.0, 30.0)
        p = 1.0 / (1.0 + np.exp(-eta))
        weights = np.maximum(trials * p * (1.0 - p), 1e-6)
        gradient = design.T @ (successes - trials * p)
        hessian = design.T @ (weights[:, None] * design)
        step = np.linalg.solve(hessian + np.eye(2) * 1e-8, gradient)
        beta += step
        if np.linalg.norm(step) < 1e-8:
            break
    if beta[1] <= 0:
        raise ValueError("measured data do not support a monotonic success-vs-margin threshold")
    thresholds = {}
    for probability in (0.1, 0.5, 0.9, 0.95):
        logit = np.log(probability / (1.0 - probability))
        thresholds[f"margin_db_at_P{int(probability * 100)}"] = float((logit - beta[0]) / beta[1])
    return {
        "schema_version": 1,
        "fit": {"intercept": float(beta[0]), "slope_per_db": float(beta[1])},
        "thresholds": thresholds,
        "sample_points": len(records),
        "total_trials": int(np.sum(trials)),
        "warning": "empirical for the tested phone/orientation/firmware/mechanical assembly only",
    }
