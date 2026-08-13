"""
Stream Replay Engine for CARES.

Replays pre-generated physiological sample streams or scenario files
through the CARES Decision Engine.
"""

import json
from typing import Dict, List, Tuple
from engine.models import EngineOutput, PhysiologicalSample
from engine.risk_engine import CARESDecisionEngine


class StreamReplayEngine:
    """
    Executes sample stream replays for verification and benchmarking.
    """

    def __init__(self, engine: CARESDecisionEngine) -> None:
        self.engine: CARESDecisionEngine = engine

    def replay_stream(
        self, samples: List[PhysiologicalSample]
    ) -> List[Tuple[PhysiologicalSample, EngineOutput]]:
        """Replays samples sequentially and returns pairs of (input, output)."""
        self.engine.reset()
        results: List[Tuple[PhysiologicalSample, EngineOutput]] = []
        for sample in samples:
            output = self.engine.process_sample(sample)
            results.append((sample, output))
        return results

    def replay_scenario_file(
        self, filepath: str
    ) -> List[Tuple[PhysiologicalSample, EngineOutput]]:
        """Loads a JSON scenario file and replays it."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        samples = [PhysiologicalSample.from_dict(item) for item in data["samples"]]
        return self.replay_stream(samples)
