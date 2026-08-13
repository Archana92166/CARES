"""
Personal Baseline Comparator for CARES Evaluation.

This comparator is the evaluation control model for CARES.

It does NOT use population-wide HR thresholds such as:
    85 BPM
    100 BPM

Instead, each subject receives an individual baseline calculated from
the first 5 minutes (300 seconds) of trusted HR observations.

After calibration:

    deviation = |current HR - personal baseline|

The comparator then uses empirically derived deviation thresholds from
the WESAD training folds.

This comparator intentionally does NOT contain the additional temporal
intelligence of the complete CARES engine.

It does NOT use:
    - adaptive baseline learning
    - persistence state machine
    - recovery logic
    - CARES confidence logic
    - daily baseline learning
    - long-term baseline learning

Its purpose is to isolate the value added by CARES's adaptive and
temporal decision architecture.

WESAD is used only for offline evaluation.
"""

from __future__ import annotations

from typing import List, Optional

from engine.models import PhysiologicalSample, RiskLevel


class PersonalBaselineComparator:
    """
    Individual-person baseline comparator.

    Calibration:
        Minimum 300 seconds.

    Decision:
        LOW    : deviation < medium threshold
        MEDIUM : deviation >= medium threshold
        HIGH   : deviation >= high threshold

    The deviation thresholds are supplied by the evaluation pipeline
    from training subjects. They are not population HR thresholds.
    """

    def __init__(
        self,
        calibration_seconds: int = 300,
        medium_deviation_bpm: float = 7.0,
        high_deviation_bpm: float = 23.0,
    ) -> None:

        if calibration_seconds < 300:
            raise ValueError(
                "Personal baseline calibration must be at least 300 seconds."
            )

        if medium_deviation_bpm <= 0:
            raise ValueError(
                "medium_deviation_bpm must be positive."
            )

        if high_deviation_bpm <= medium_deviation_bpm:
            raise ValueError(
                "high_deviation_bpm must exceed medium_deviation_bpm."
            )

        self.calibration_seconds = int(
            calibration_seconds
        )

        self.medium_deviation_bpm = float(
            medium_deviation_bpm
        )

        self.high_deviation_bpm = float(
            high_deviation_bpm
        )

        self._calibration_values: List[float] = []

        self._baseline: Optional[float] = None

        self._calibration_start: Optional[float] = None

    # =========================================================
    # PROPERTIES
    # =========================================================

    @property
    def baseline(self) -> Optional[float]:
        """Current individual personal baseline."""
        return self._baseline

    @property
    def is_calibrated(self) -> bool:
        """True once the 5-minute calibration is complete."""
        return self._baseline is not None

    @property
    def calibration_samples(self) -> int:
        """Number of trusted calibration observations."""
        return len(self._calibration_values)

    # =========================================================
    # SAMPLE PROCESSING
    # =========================================================

    def process_sample(
        self,
        sample: PhysiologicalSample,
    ) -> RiskLevel:

        sample.validate()

        # -----------------------------------------------------
        # Invalid HR is not physiological evidence.
        # -----------------------------------------------------

        if not sample.hr_valid:
            return RiskLevel.LOW

        timestamp = float(
            sample.timestamp
        )

        hr = float(
            sample.heart_rate_bpm
        )

        # -----------------------------------------------------
        # Initial personal calibration
        # -----------------------------------------------------

        if self._baseline is None:

            if self._calibration_start is None:
                self._calibration_start = timestamp

            self._calibration_values.append(hr)

            elapsed = (
                timestamp
                - self._calibration_start
            )

            if (
                elapsed >= self.calibration_seconds
                and self._calibration_values
            ):

                self._baseline = (
                    sum(self._calibration_values)
                    / len(self._calibration_values)
                )

            # No risk decision during calibration.
            return RiskLevel.LOW

        # -----------------------------------------------------
        # Personal-baseline comparison
        # -----------------------------------------------------

        deviation = abs(
            hr - self._baseline
        )

        if (
            deviation
            >= self.high_deviation_bpm
        ):
            return RiskLevel.HIGH

        if (
            deviation
            >= self.medium_deviation_bpm
        ):
            return RiskLevel.MEDIUM

        return RiskLevel.LOW

    # =========================================================
    # STREAM PROCESSING
    # =========================================================

    def process_stream(
        self,
        samples: List[PhysiologicalSample],
    ) -> List[RiskLevel]:

        return [
            self.process_sample(sample)
            for sample in samples
        ]

    # =========================================================
    # RESET
    # =========================================================

    def reset(self) -> None:
        """Reset the comparator for a new subject/session."""

        self._calibration_values.clear()

        self._baseline = None

        self._calibration_start = None