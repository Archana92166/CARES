"""
Training Data Parameter Derivation Module for CARES.

Purpose
-------
Derives evaluation parameters strictly from the training subjects in
each LOSO fold.

Important architecture rules
----------------------------
1. The held-out test subject is NEVER used to derive thresholds.
2. Personal baseline calibration uses the first 300 seconds.
3. Baseline is calculated independently for every training subject.
4. Deviation is measured relative to that subject's personal baseline.
5. No population-wide HR threshold is used.
6. Medium deviation is selected using Youden's J statistic.
7. High deviation is selected using the 90th percentile of stress
   deviation.
8. Rapid HR change is derived from the positive stress-onset slope
   distribution.

WESAD is an offline benchmark only.
The production CARES system will receive live sensor-derived HR.
"""

from __future__ import annotations

from typing import Dict, List

from evaluation.wesad_parser import WESADSubjectData


class ParameterDeriver:
    """
    Derives decision parameters from training-subject distributions.
    """

    DEFAULT_CALIBRATION_SECONDS = 300

    @staticmethod
    def derive_parameters(
        training_subjects: List[WESADSubjectData],
        baseline_calibration_window_sec: int = DEFAULT_CALIBRATION_SECONDS,
    ) -> Dict[str, float]:
        """
        Derive deviation and temporal parameters from training subjects.

        Each training subject gets an independent personal baseline from
        their first 300 seconds.

        Ground truth:
            2       -> stress
            0, 3, 4 -> non-stress

        Returns
        -------
        Dict[str, float]
            Derived parameters used by the held-out fold.
        """

        if baseline_calibration_window_sec < 300:
            raise ValueError(
                "Baseline calibration must be at least 300 seconds."
            )

        neutral_abs_devs: List[float] = []
        neutral_pct_devs: List[float] = []

        stress_abs_devs: List[float] = []
        stress_pct_devs: List[float] = []

        stress_slopes: List[float] = []

        for subject in training_subjects:

            if len(subject.samples) <= baseline_calibration_window_sec:
                continue

            # -------------------------------------------------
            # PERSONAL BASELINE
            # -------------------------------------------------
            #
            # Each subject has their own baseline.
            #
            calibration_samples = subject.samples[
                :baseline_calibration_window_sec
            ]

            trusted_hr = [
                sample.heart_rate_bpm
                for sample in calibration_samples
                if sample.hr_valid
            ]

            if not trusted_hr:
                continue

            baseline_hr = (
                sum(trusted_hr)
                / len(trusted_hr)
            )

            if baseline_hr <= 0:
                continue

            # -------------------------------------------------
            # POST-CALIBRATION DATA
            # -------------------------------------------------

            for i in range(
                baseline_calibration_window_sec,
                len(subject.samples),
            ):

                sample = subject.samples[i]

                if not sample.hr_valid:
                    continue

                label = subject.labels[i]

                # ---------------------------------------------
                # TRUE ABSOLUTE PERSONAL-BASELINE DEVIATION
                # ---------------------------------------------

                signed_deviation = (
                    sample.heart_rate_bpm
                    - baseline_hr
                )

                abs_dev = abs(
                    signed_deviation
                )

                pct_dev = (
                    abs_dev
                    / baseline_hr
                ) * 100.0

                # ---------------------------------------------
                # TEMPORAL SLOPE
                # ---------------------------------------------

                slope = 0.0

                if i >= 5:

                    previous = subject.samples[i - 5]

                    if (
                        previous.hr_valid
                        and sample.timestamp
                        > previous.timestamp
                    ):

                        dt = (
                            sample.timestamp
                            - previous.timestamp
                        )

                        slope = (
                            sample.heart_rate_bpm
                            - previous.heart_rate_bpm
                        ) / dt

                # ---------------------------------------------
                # GROUND TRUTH GROUPS
                # ---------------------------------------------

                if label in (0, 3, 4):

                    neutral_abs_devs.append(
                        abs_dev
                    )

                    neutral_pct_devs.append(
                        pct_dev
                    )

                elif label == 2:

                    stress_abs_devs.append(
                        abs_dev
                    )

                    stress_pct_devs.append(
                        pct_dev
                    )

                    if slope > 0:
                        stress_slopes.append(
                            slope
                        )

        # -----------------------------------------------------
        # MEDIUM DEVIATION
        # -----------------------------------------------------

        medium_dev_bpm = (
            ParameterDeriver._find_youden_threshold(
                neutral_abs_devs,
                stress_abs_devs,
                default=7.0,
            )
        )

        medium_pct_dev = (
            ParameterDeriver._find_youden_threshold(
                neutral_pct_devs,
                stress_pct_devs,
                default=10.0,
            )
        )

        # -----------------------------------------------------
        # HIGH DEVIATION
        # -----------------------------------------------------

        high_dev_bpm = (
            ParameterDeriver._percentile(
                stress_abs_devs,
                90.0,
                default=23.0,
            )
        )

        high_pct_dev = (
            ParameterDeriver._percentile(
                stress_pct_devs,
                90.0,
                default=32.0,
            )
        )

        # -----------------------------------------------------
        # RAPID CHANGE
        # -----------------------------------------------------

        rapid_slope = (
            ParameterDeriver._percentile(
                stress_slopes,
                90.0,
                default=1.7,
            )
        )

        # -----------------------------------------------------
        # SAFETY / ORDERING
        # -----------------------------------------------------

        if high_dev_bpm <= medium_dev_bpm:
            high_dev_bpm = max(
                medium_dev_bpm + 1.0,
                high_dev_bpm,
            )

        if high_pct_dev <= medium_pct_dev:
            high_pct_dev = max(
                medium_pct_dev + 1.0,
                high_pct_dev,
            )

        return {
            "medium_dev_bpm": round(
                medium_dev_bpm,
                2,
            ),
            "high_dev_bpm": round(
                high_dev_bpm,
                2,
            ),
            "medium_pct_dev": round(
                medium_pct_dev,
                2,
            ),
            "high_pct_dev": round(
                high_pct_dev,
                2,
            ),
            "rapid_slope_bpm_per_sec": round(
                rapid_slope,
                2,
            ),
        }

    # =========================================================
    # YOUDEN J
    # =========================================================

    @staticmethod
    def _find_youden_threshold(
        negatives: List[float],
        positives: List[float],
        default: float = 7.0,
    ) -> float:
        """
        Find threshold maximizing:

            Youden J = sensitivity + specificity - 1
        """

        if not negatives or not positives:
            return default

        all_values = sorted(
            set(
                negatives
                + positives
            )
        )

        if not all_values:
            return default

        # Limit candidate count for efficiency while preserving
        # distribution coverage.
        step = max(
            1,
            len(all_values) // 200,
        )

        candidates = all_values[::step]

        best_j = -1.0
        best_threshold = default

        total_positive = len(
            positives
        )

        total_negative = len(
            negatives
        )

        for threshold in candidates:

            true_positive = sum(
                1
                for value in positives
                if value >= threshold
            )

            true_negative = sum(
                1
                for value in negatives
                if value < threshold
            )

            sensitivity = (
                true_positive
                / total_positive
            )

            specificity = (
                true_negative
                / total_negative
            )

            youden_j = (
                sensitivity
                + specificity
                - 1.0
            )

            if youden_j > best_j:

                best_j = youden_j
                best_threshold = threshold

        return best_threshold

    # =========================================================
    # PERCENTILE
    # =========================================================

    @staticmethod
    def _percentile(
        data: List[float],
        percentile: float,
        default: float = 0.0,
    ) -> float:
        """
        Compute a simple empirical percentile.
        """

        if not data:
            return default

        sorted_data = sorted(
            data
        )

        if len(sorted_data) == 1:
            return sorted_data[0]

        position = (
            percentile / 100.0
        ) * (len(sorted_data) - 1)

        lower = int(position)
        upper = min(
            lower + 1,
            len(sorted_data) - 1,
        )

        fraction = (
            position - lower
        )

        return (
            sorted_data[lower]
            + fraction
            * (
                sorted_data[upper]
                - sorted_data[lower]
            )
        )