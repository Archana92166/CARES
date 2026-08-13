"""
Leave-One-Subject-Out (LOSO) Evaluation Pipeline for CARES.

Evaluation architecture
-----------------------
Control:
    PersonalBaselineComparator

Experimental system:
    CARES Adaptive Cognitive-Risk Engine

Both systems use an individual personal HR baseline.

The comparator intentionally has no temporal adaptation.

CARES adds:
    - temporal deviation features
    - trend
    - persistence
    - recovery
    - adaptive decision logic

Calibration:
    >= 300 seconds

The held-out test subject is never used to derive deviation
parameters.

WESAD is an offline benchmark only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from engine.config import CARESConfig
from engine.models import RiskLevel
from engine.risk_engine import CARESDecisionEngine

from evaluation.baselines import (
    PersonalBaselineComparator,
)

from evaluation.parameter_derivation import (
    ParameterDeriver,
)

from evaluation.wesad_parser import (
    WESADParser,
    WESADSubjectData,
)


@dataclass
class ModelEvaluationResult:
    """Quantitative metrics for one evaluation model."""

    model_name: str
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    specificity: float
    false_positive_rate: float
    false_negative_rate: float
    mean_detection_latency_sec: float
    oscillations_per_minute: float


class LOSOEvaluator:
    """
    Executes Leave-One-Subject-Out evaluation.

    Every fold:

        training subjects
              |
              +--> derive deviation parameters
              |
        held-out subject
              |
              +--> personal baseline calibration
              |
              +--> comparator / CARES evaluation
    """

    CALIBRATION_SECONDS = 300

    def __init__(
        self,
        parser: Optional[WESADParser] = None,
    ) -> None:

        self.parser = (
            parser
            or WESADParser()
        )

    # =========================================================
    # MAIN EVALUATION
    # =========================================================

    def run_loso_evaluation(
        self,
    ) -> Dict[str, ModelEvaluationResult]:

        all_subjects = (
            self.parser.load_all_subjects(
                seed=42
            )
        )

        subject_ids = list(
            all_subjects.keys()
        )

        # -----------------------------------------------------
        # Aggregate comparator metrics
        # -----------------------------------------------------

        comparator_tp = 0
        comparator_fp = 0
        comparator_tn = 0
        comparator_fn = 0

        comparator_latencies: List[
            float
        ] = []

        comparator_oscillations = 0

        # -----------------------------------------------------
        # Aggregate CARES metrics
        # -----------------------------------------------------

        cares_tp = 0
        cares_fp = 0
        cares_tn = 0
        cares_fn = 0

        cares_latencies: List[
            float
        ] = []

        cares_oscillations = 0

        total_evaluated_minutes = 0.0

        # =====================================================
        # LOSO FOLDS
        # =====================================================

        for test_sid in subject_ids:

            test_subject = (
                all_subjects[test_sid]
            )

            training_subjects = [
                all_subjects[sid]
                for sid in subject_ids
                if sid != test_sid
            ]

            # -------------------------------------------------
            # Derive parameters ONLY from training subjects
            # -------------------------------------------------

            derived = (
                ParameterDeriver.derive_parameters(
                    training_subjects,
                    baseline_calibration_window_sec=(
                        self.CALIBRATION_SECONDS
                    ),
                )
            )

            # -------------------------------------------------
            # Personal-baseline comparator
            # -------------------------------------------------

            comparator = (
                PersonalBaselineComparator(
                    calibration_seconds=(
                        self.CALIBRATION_SECONDS
                    ),
                    medium_deviation_bpm=(
                        derived["medium_dev_bpm"]
                    ),
                    high_deviation_bpm=(
                        derived["high_dev_bpm"]
                    ),
                )
            )

            # -------------------------------------------------
            # CARES configuration
            # -------------------------------------------------

            cares_config = CARESConfig()

            cares_config.risk.medium_deviation_bpm = (
                derived["medium_dev_bpm"]
            )

            cares_config.risk.high_deviation_bpm = (
                derived["high_dev_bpm"]
            )

            cares_config.risk.medium_pct_deviation = (
                derived["medium_pct_dev"]
            )

            cares_config.risk.high_pct_deviation = (
                derived["high_pct_dev"]
            )

            cares_config.risk.rapid_change_rate_bpm_per_sec = (
                derived[
                    "rapid_slope_bpm_per_sec"
                ]
            )

            cares_engine = (
                CARESDecisionEngine(
                    cares_config
                )
            )

            # -------------------------------------------------
            # Process complete held-out stream
            # -------------------------------------------------

            comparator_outputs = (
                comparator.process_stream(
                    test_subject.samples
                )
            )

            cares_outputs = (
                cares_engine.process_stream(
                    test_subject.samples
                )
            )

            # -------------------------------------------------
            # Evaluate only POST-CALIBRATION observations
            # -------------------------------------------------
            #
            # This prevents the five-minute calibration period
            # from artificially inflating TN counts.
            # -------------------------------------------------

            evaluation_indices = [
                i
                for i, sample
                in enumerate(
                    test_subject.samples
                )
                if sample.timestamp
                >= self.CALIBRATION_SECONDS
            ]

            if not evaluation_indices:
                continue

            evaluated_seconds = (
                test_subject.samples[
                    evaluation_indices[-1]
                ].timestamp
                - self.CALIBRATION_SECONDS
            )

            if evaluated_seconds > 0:
                total_evaluated_minutes += (
                    evaluated_seconds
                    / 60.0
                )

            # -------------------------------------------------
            # Oscillation counts
            # -------------------------------------------------

            comparator_levels = [
                comparator_outputs[i]
                for i in evaluation_indices
            ]

            cares_levels = [
                cares_outputs[i].risk_level
                for i in evaluation_indices
            ]

            comparator_oscillations += sum(
                1
                for i in range(
                    1,
                    len(comparator_levels),
                )
                if (
                    comparator_levels[i]
                    != comparator_levels[i - 1]
                )
            )

            cares_oscillations += sum(
                1
                for i in range(
                    1,
                    len(cares_levels),
                )
                if (
                    cares_levels[i]
                    != cares_levels[i - 1]
                )
            )

            # -------------------------------------------------
            # Stress onset latency
            # -------------------------------------------------

            stress_indices = [
                i
                for i, label
                in enumerate(
                    test_subject.labels
                )
                if (
                    label == 2
                    and test_subject.samples[i].timestamp
                    >= self.CALIBRATION_SECONDS
                )
            ]

            if stress_indices:

                stress_start_idx = (
                    stress_indices[0]
                )

                stress_start_time = (
                    test_subject.samples[
                        stress_start_idx
                    ].timestamp
                )

                # Comparator latency

                for i in range(
                    stress_start_idx,
                    len(test_subject.samples),
                ):

                    if (
                        comparator_outputs[i]
                        in (
                            RiskLevel.MEDIUM,
                            RiskLevel.HIGH,
                        )
                    ):

                        comparator_latencies.append(
                            test_subject.samples[i].timestamp
                            - stress_start_time
                        )

                        break

                # CARES latency

                for i in range(
                    stress_start_idx,
                    len(test_subject.samples),
                ):

                    if (
                        cares_outputs[i].risk_level
                        in (
                            RiskLevel.MEDIUM,
                            RiskLevel.HIGH,
                        )
                    ):

                        cares_latencies.append(
                            test_subject.samples[i].timestamp
                            - stress_start_time
                        )

                        break

            # -------------------------------------------------
            # Confusion matrices
            # -------------------------------------------------

            for i in evaluation_indices:

                label = (
                    test_subject.labels[i]
                )

                comparator_level = (
                    comparator_outputs[i]
                )

                cares_level = (
                    cares_outputs[i].risk_level
                )

                is_stress = (
                    label == 2
                )

                comparator_positive = (
                    comparator_level
                    in (
                        RiskLevel.MEDIUM,
                        RiskLevel.HIGH,
                    )
                )

                cares_positive = (
                    cares_level
                    in (
                        RiskLevel.MEDIUM,
                        RiskLevel.HIGH,
                    )
                )

                # =============================================
                # PERSONAL BASELINE COMPARATOR
                # =============================================

                if is_stress:

                    if comparator_positive:
                        comparator_tp += 1
                    else:
                        comparator_fn += 1

                else:

                    if comparator_positive:
                        comparator_fp += 1
                    else:
                        comparator_tn += 1

                # =============================================
                # CARES
                # =============================================

                if is_stress:

                    if cares_positive:
                        cares_tp += 1
                    else:
                        cares_fn += 1

                else:

                    if cares_positive:
                        cares_fp += 1
                    else:
                        cares_tn += 1

        # =====================================================
        # RESULTS
        # =====================================================

        return {
            "Personal Baseline Comparator": (
                self._build_metrics_result(
                    "Personal Baseline Comparator",
                    comparator_tp,
                    comparator_fp,
                    comparator_tn,
                    comparator_fn,
                    comparator_latencies,
                    comparator_oscillations,
                    total_evaluated_minutes,
                )
            ),
            "CARES Adaptive Cognitive-Risk Engine": (
                self._build_metrics_result(
                    "CARES Adaptive Cognitive-Risk Engine",
                    cares_tp,
                    cares_fp,
                    cares_tn,
                    cares_fn,
                    cares_latencies,
                    cares_oscillations,
                    total_evaluated_minutes,
                )
            ),
        }

    # =========================================================
    # METRICS
    # =========================================================

    @staticmethod
    def _build_metrics_result(
        name: str,
        tp: int,
        fp: int,
        tn: int,
        fn: int,
        latencies: List[float],
        oscillations: int,
        total_minutes: float,
    ) -> ModelEvaluationResult:

        total = (
            tp
            + fp
            + tn
            + fn
        )

        accuracy = (
            (tp + tn) / total
            if total > 0
            else 0.0
        )

        precision = (
            tp / (tp + fp)
            if (tp + fp) > 0
            else 0.0
        )

        recall = (
            tp / (tp + fn)
            if (tp + fn) > 0
            else 0.0
        )

        f1 = (
            (2 * precision * recall)
            / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        specificity = (
            tn / (tn + fp)
            if (tn + fp) > 0
            else 0.0
        )

        false_positive_rate = (
            fp / (fp + tn)
            if (fp + tn) > 0
            else 0.0
        )

        false_negative_rate = (
            fn / (fn + tp)
            if (fn + tp) > 0
            else 0.0
        )

        mean_latency = (
            sum(latencies)
            / len(latencies)
            if latencies
            else 0.0
        )

        oscillations_per_minute = (
            oscillations
            / total_minutes
            if total_minutes > 0
            else 0.0
        )

        return ModelEvaluationResult(
            model_name=name,
            accuracy=round(
                accuracy,
                4,
            ),
            precision=round(
                precision,
                4,
            ),
            recall=round(
                recall,
                4,
            ),
            f1_score=round(
                f1,
                4,
            ),
            specificity=round(
                specificity,
                4,
            ),
            false_positive_rate=round(
                false_positive_rate,
                4,
            ),
            false_negative_rate=round(
                false_negative_rate,
                4,
            ),
            mean_detection_latency_sec=round(
                mean_latency,
                2,
            ),
            oscillations_per_minute=round(
                oscillations_per_minute,
                2,
            ),
        )