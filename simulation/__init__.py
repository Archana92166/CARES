"""
Simulation Package for CARES.

Provides synthetic physiological stream generation and stream replay
for software-in-the-loop testing and evaluation.
"""

from .generator import ScenarioType, PhysiologicalStreamGenerator
from .replay import StreamReplayEngine

__all__ = [
    "ScenarioType",
    "PhysiologicalStreamGenerator",
    "StreamReplayEngine",
]
