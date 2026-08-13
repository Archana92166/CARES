"""
Physiological Stream Generator for CARES.

Generates reproducible software-in-the-loop synthetic physiological sample streams
representing different cognitive-risk scenarios.
"""

import math
import random
from enum import Enum
from typing import List, Optional
from engine.models import PhysiologicalSample


class ScenarioType(str, Enum):
    """Synthetic scenario categories for evaluation."""
    RESTING = "RESTING"
    TRANSIENT_SPIKE = "TRANSIENT_SPIKE"
    SUSTAINED_PANIC = "SUSTAINED_PANIC"
    GRADUAL_STRESS = "GRADUAL_STRESS"
    RECOVERY_SCENARIO = "RECOVERY_SCENARIO"
    NOISY_SIGNAL = "NOISY_SIGNAL"


class PhysiologicalStreamGenerator:
    """
    Generates synthetic physiological sample streams for testing.
    """

    def __init__(self, seed: Optional[int] = 42) -> None:
        if seed is not None:
            random.seed(seed)

    def generate_scenario(
        self,
        scenario_type: ScenarioType,
        duration_seconds: int = 120,
        sampling_interval_seconds: float = 1.0,
        base_hr: float = 70.0,
        noise_std: float = 1.0,
        calibration_window_seconds: int = 30,
    ) -> List[PhysiologicalSample]:
        """
        Generates a sequence of PhysiologicalSample instances for a scenario.
        """
        samples: List[PhysiologicalSample] = []
        total_samples = int(duration_seconds / sampling_interval_seconds)
        current_time = 0.0

        for i in range(total_samples):
            hr = base_hr + random.gauss(0, noise_std)

            # Apply scenario dynamics after initial calibration window
            if current_time >= calibration_window_seconds:
                rel_time = current_time - calibration_window_seconds

                if scenario_type == ScenarioType.TRANSIENT_SPIKE:
                    # Single spike at t=40s lasting 1.5 seconds
                    if 10.0 <= rel_time <= 11.5:
                        hr += 45.0  # Large spike artifact

                elif scenario_type == ScenarioType.SUSTAINED_PANIC:
                    # Rapid spike at t=10s and stays high for 40s
                    if rel_time >= 10.0:
                        hr += 35.0

                elif scenario_type == ScenarioType.GRADUAL_STRESS:
                    # Ramp up 1 bpm/sec for 30s
                    if 10.0 <= rel_time <= 40.0:
                        hr += (rel_time - 10.0) * 1.0
                    elif rel_time > 40.0:
                        hr += 30.0

                elif scenario_type == ScenarioType.RECOVERY_SCENARIO:
                    # High panic from t=10 to t=40, then gradual drop back to base by t=70
                    if 10.0 <= rel_time < 40.0:
                        hr += 35.0
                    elif 40.0 <= rel_time <= 70.0:
                        decay_ratio = (70.0 - rel_time) / 30.0
                        hr += 35.0 * max(0.0, decay_ratio)

                elif scenario_type == ScenarioType.NOISY_SIGNAL:
                    # High amplitude random noise
                    hr += random.gauss(0, noise_std * 3.0)

            # Clamp to valid bounds
            hr = max(35.0, min(210.0, hr))

            samples.append(
                PhysiologicalSample(
                    timestamp=current_time,
                    heart_rate_bpm=hr,
                )
            )
            current_time += sampling_interval_seconds

        return samples
