"""Autolock 13.56 MHz NFC reduced-order engineering model.

The public API intentionally separates geometry-derived quantities from
calibration priors.  No function in this package predicts an absolute HomeKey
success probability unless measured success/failure data have been supplied.
"""

from .model import SimulationResult, simulate
from .scenarios import Scenario, get_scenario, load_scenario

__all__ = ["Scenario", "SimulationResult", "get_scenario", "load_scenario", "simulate"]
__version__ = "0.1.0"
