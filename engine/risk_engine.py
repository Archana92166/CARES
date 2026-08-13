"""
CARES Adaptive Cognitive-Risk Decision Engine.

Main decision engine orchestrator combining:

    - personal baseline estimation
    - temporal feature extraction
    - continuous risk scoring
    - evidence confidence evaluation
    - adaptive escalation state machine
    - explainable reason-code generation
    - guardian action mapping

IMPORTANT BASELINE ORDER:

    Current personal baseline
            |
            v
    Compare current HR against that baseline
            |
            v
    Make current risk decision
            |
            v
    Learn/adapt baseline only after decision
            |
            v
    Next observation uses the updated baseline

Therefore, baseline learning can NEVER rewrite the reference
used to explain the same physiological observation.

WESAD is an OFFLINE evaluation source only.

The intended runtime system is designed for a live physiological
sensor stream producing dynamic HR measurements.
"""

from typing import List, Optional

from .config import CARESConfig
from .models import (
    RiskLevel,
    BaselineState,
    RecoveryState,
    PhysiologicalSample,
    TemporalFeatures,
    EngineOutput,
)
from .baseline import BaselineEstimator
from .features import FeatureExtractor
from .escalation import EscalationStateMachine
from guardian.actions import GuardianActionMapper


class CARESDecisionEngine:
    """
    CARES Adaptive Cognitive-Risk Decision Engine.

    Pipeline:

        HR sample
            |
            v
        Personal baseline
            |
            v
        Temporal features
            |
            v
        Candidate risk
            |
            v
        Confidence
            |
            v
        Escalation state machine
            |
            v
        Baseline adaptation
            |
            v
        Explanation + guardian action
    """

    def __init__(
        self,
        config: Optional[CARESConfig] = None,
    ) -> None:

        self.config: CARESConfig = (
            config or CARESConfig()
        )

        self.baseline_estimator: BaselineEstimator = (
            BaselineEstimator(
                self.config.baseline
            )
        )

        self.feature_extractor: FeatureExtractor = (
            FeatureExtractor(
                self.config.feature
            )
        )

        self.state_machine: EscalationStateMachine = (
            EscalationStateMachine(
                self.config.escalation
            )
        )

    # =========================================================
    # RESET
    # =========================================================

    def reset(self) -> None:
        """
        Reset the CARES decision state for a new monitoring
        session.

        Persistent personal-baseline learning is handled by the
        dedicated personal baseline manager and is not modified
        here.
        """

        self.baseline_estimator.reset()
        self.feature_extractor.reset()
        self.state_machine.reset()

    # =========================================================
    # MAIN PROCESSING PIPELINE
    # =========================================================

    def process_sample(
        self,
        sample: PhysiologicalSample,
    ) -> EngineOutput:
        """
        Process one physiological HR observation.

        CRITICAL ORDER:

            1. Establish/read the current personal baseline.
            2. Calculate the current observation's deviation.
            3. Evaluate current risk.
            4. Run escalation logic.
            5. Only then allow baseline adaptation.

        This guarantees that:

            deviation = current HR - baseline used for decision

        and prevents an adaptation update from changing the
        baseline of the observation that caused that update.
        """

        sample.validate()

        # =====================================================
        # 1. BASELINE CALIBRATION / CURRENT BASELINE
        # =====================================================

        baseline_state = (
            self.baseline_estimator.add_sample(
                sample
            )
        )

        baseline_hr = (
            self.baseline_estimator.baseline_hr
        )

        # =====================================================
        # 2. FEATURE EXTRACTION
        # =====================================================
        #
        # IMPORTANT:
        #
        # The baseline passed here is the baseline that exists
        # BEFORE any learning caused by the current observation.
        #
        # Therefore all current features describe the current
        # physiological observation against the correct
        # historical personal reference.
        #

        features: TemporalFeatures = (
            self.feature_extractor.extract_features(
                sample,
                baseline_hr,
            )
        )

        # =====================================================
        # 3. CALIBRATION PHASE
        # =====================================================

        if (
            baseline_state != BaselineState.READY
            or baseline_hr is None
        ):

            return self._build_calibrating_output(
                sample,
                baseline_state,
            )

        # =====================================================
        # 4. CURRENT RISK EVALUATION
        # =====================================================

        (
            candidate_level,
            risk_score,
            is_extreme_spike,
        ) = self._evaluate_candidate_risk(
            features
        )

        # =====================================================
        # 5. EVIDENCE CONFIDENCE
        # =====================================================

        confidence = (
            self._evaluate_confidence(
                features
            )
        )

        # =====================================================
        # 6. TEMPORAL ESCALATION
        # =====================================================

        (
            escalated_risk_level,
            transition_reason,
        ) = self.state_machine.update(
            candidate_level=candidate_level,
            features=features,
            confidence=confidence,
            is_extreme_spike=is_extreme_spike,
        )

        # =====================================================
        # 7. BASELINE ADAPTATION
        # =====================================================
        #
        # THIS MUST HAPPEN AFTER CURRENT RISK EVALUATION.
        #
        # Example:
        #
        # baseline = 70
        # current HR = 71
        #
        # Current observation:
        #
        #     deviation = +1 bpm
        #
        # If the observation is confirmed LOW risk, the baseline
        # may slowly learn toward 71.
        #
        # The CURRENT observation still reports deviation +1.
        #
        # The UPDATED baseline is used only for FUTURE samples.
        #

        self.baseline_estimator.update_adaptive(
            sample,
            escalated_risk_level.value,
        )

        # =====================================================
        # 8. REASON CODES
        # =====================================================

        reason_codes = (
            self._generate_reason_codes(
                features=features,
                candidate_level=candidate_level,
                escalated_level=escalated_risk_level,
                transition_reason=transition_reason,
                is_extreme_spike=is_extreme_spike,
            )
        )

        # =====================================================
        # 9. HUMAN-READABLE EXPLANATION
        # =====================================================

        explanation = (
            self._build_explanation(
                features=features,
                risk_level=escalated_risk_level,
                reason_codes=reason_codes,
            )
        )

        # =====================================================
        # 10. GUARDIAN ACTION MAPPING
        # =====================================================

        action_payload = (
            GuardianActionMapper.map_actions(
                risk_level=escalated_risk_level,
                timestamp=sample.timestamp,
                explanation=explanation,
                reason_codes=reason_codes,
            )
        )

        action_strings = [
            str(action.value)
            for action in action_payload.actions
        ]

        # =====================================================
        # 11. FINAL ENGINE OUTPUT
        # =====================================================

        return EngineOutput(
            timestamp=sample.timestamp,

            risk_level=escalated_risk_level,

            risk_score=risk_score,

            confidence=confidence,

            trend=features.rate_of_change,

            # IMPORTANT:
            # This is the baseline used to evaluate THIS sample.
            baseline=baseline_hr,

            current_value=sample.heart_rate_bpm,

            # IMPORTANT:
            # This is the deviation calculated BEFORE adaptation.
            deviation=features.abs_deviation,

            pct_deviation=features.pct_deviation,

            persistence=(
                features.abnormality_persistence_seconds
            ),

            recovery_state=str(
                features.recovery_state.value
                if isinstance(
                    features.recovery_state,
                    RecoveryState,
                )
                else features.recovery_state
            ),

            reason_codes=reason_codes,

            human_readable_explanation=explanation,

            recommended_action=action_strings,
        )

    # =========================================================
    # STREAM PROCESSING
    # =========================================================

    def process_stream(
        self,
        samples: List[PhysiologicalSample],
    ) -> List[EngineOutput]:
        """
        Process a sequence of physiological samples in temporal
        order.
        """

        return [
            self.process_sample(sample)
            for sample in samples
        ]

    # =========================================================
    # CANDIDATE RISK
    # =========================================================

    def _evaluate_candidate_risk(
        self,
        features: TemporalFeatures,
    ) -> tuple[RiskLevel, float, bool]:
        """
        Evaluate candidate risk level and continuous risk score.

        Returns:

            (
                candidate_level,
                risk_score,
                is_extreme_spike
            )
        """

        abs_dev = features.abs_deviation

        pct_dev = features.pct_deviation

        trend = features.rate_of_change

        # -----------------------------------------------------
        # Continuous deviation score
        # -----------------------------------------------------

        dev_score = min(
            50.0,
            (
                abs_dev
                / self.config.risk.high_deviation_bpm
            )
            * 50.0,
        )

        # -----------------------------------------------------
        # Percentage deviation score
        # -----------------------------------------------------

        pct_score = min(
            30.0,
            (
                pct_dev
                / self.config.risk.high_pct_deviation
            )
            * 30.0,
        )

        # -----------------------------------------------------
        # Rapid change score
        # -----------------------------------------------------

        trend_score = min(
            20.0,
            max(
                0.0,
                (
                    trend
                    / self.config.risk.rapid_change_rate_bpm_per_sec
                )
                * 20.0,
            ),
        )

        # -----------------------------------------------------
        # Combined risk score
        # -----------------------------------------------------

        risk_score = max(
            0.0,
            min(
                100.0,
                dev_score
                + pct_score
                + trend_score,
            ),
        )

        # -----------------------------------------------------
        # Extreme physiological deviation
        # -----------------------------------------------------

        is_extreme_spike = (
            abs_dev
            >= self.config.risk.extreme_spike_threshold_bpm
        )

        # -----------------------------------------------------
        # Candidate HIGH
        # -----------------------------------------------------

        if (
            abs_dev
            >= self.config.risk.high_deviation_bpm
            or pct_dev
            >= self.config.risk.high_pct_deviation
            or is_extreme_spike
        ):

            candidate_level = RiskLevel.HIGH

        # -----------------------------------------------------
        # Candidate MEDIUM
        # -----------------------------------------------------

        elif (
            abs_dev
            >= self.config.risk.medium_deviation_bpm
            or pct_dev
            >= self.config.risk.medium_pct_deviation
            or trend
            >= self.config.risk.rapid_change_rate_bpm_per_sec
        ):

            candidate_level = RiskLevel.MEDIUM

        # -----------------------------------------------------
        # Candidate LOW
        # -----------------------------------------------------

        else:

            candidate_level = RiskLevel.LOW

        return (
            candidate_level,
            risk_score,
            is_extreme_spike,
        )

    # =========================================================
    # CONFIDENCE
    # =========================================================

    def _evaluate_confidence(
        self,
        features: TemporalFeatures,
    ) -> float:
        """
        Calculate evidence confidence.

        Confidence increases when the personal baseline is well
        established and when abnormality persists over multiple
        observations.
        """

        samples_cnt = (
            self.baseline_estimator.samples_count
        )

        # -----------------------------------------------------
        # Compatibility with existing BaselineConfig
        # -----------------------------------------------------
        #
        # Older tests/configurations use window_samples.
        # Keep that interface intact.
        #

        window_req = getattr(
            self.config.baseline,
            "window_samples",
            30,
        )

        if window_req <= 0:
            window_req = 30

        base_conf = min(
            1.0,
            samples_cnt / window_req,
        )

        # -----------------------------------------------------
        # Persistence contribution
        # -----------------------------------------------------

        if (
            features.abnormality_persistence_samples
            > 1
        ):

            persistence_factor = min(
                0.3,
                features.abnormality_persistence_samples
                * 0.1,
            )

        else:

            persistence_factor = 0.0

        # -----------------------------------------------------
        # Final confidence
        # -----------------------------------------------------

        confidence = min(
            1.0,
            base_conf * 0.7
            + persistence_factor
            + 0.1,
        )

        return confidence

    # =========================================================
    # CALIBRATION OUTPUT
    # =========================================================

    def _build_calibrating_output(
        self,
        sample: PhysiologicalSample,
        state: BaselineState,
    ) -> EngineOutput:
        """
        Output generated while the personal baseline is still
        being established.
        """

        reason = [
            "BASELINE_CALIBRATING"
        ]

        window_req = getattr(
            self.config.baseline,
            "window_samples",
            30,
        )

        calibration_seconds = getattr(
            self.config.baseline,
            "calibration_duration_seconds",
            300,
        )

        explanation = (
            "Personal baseline calibration in progress "
            f"({self.baseline_estimator.samples_count}/"
            f"{window_req} trusted samples; "
            f"minimum calibration duration "
            f"{calibration_seconds}s). "
            "System monitoring active."
        )

        action_payload = (
            GuardianActionMapper.map_actions(
                risk_level=RiskLevel.LOW,
                timestamp=sample.timestamp,
                explanation=explanation,
                reason_codes=reason,
            )
        )

        action_strings = [
            str(action.value)
            for action in action_payload.actions
        ]

        return EngineOutput(
            timestamp=sample.timestamp,

            risk_level=RiskLevel.LOW,

            risk_score=0.0,

            confidence=0.2,

            trend=0.0,

            # During calibration there is not yet a valid
            # personal baseline.
            #
            # Existing output compatibility is retained by
            # exposing the current HR here.
            baseline=sample.heart_rate_bpm,

            current_value=sample.heart_rate_bpm,

            deviation=0.0,

            pct_deviation=0.0,

            persistence=0.0,

            recovery_state=str(
                RecoveryState.NO_RECOVERY.value
            ),

            reason_codes=reason,

            human_readable_explanation=explanation,

            recommended_action=action_strings,
        )

    # =========================================================
    # REASON CODES
    # =========================================================

    def _generate_reason_codes(
        self,
        features: TemporalFeatures,
        candidate_level: RiskLevel,
        escalated_level: RiskLevel,
        transition_reason: Optional[str],
        is_extreme_spike: bool,
    ) -> List[str]:
        """
        Generate explainable reason codes for the current
        decision.
        """

        codes: List[str] = []

        # -----------------------------------------------------
        # Personal baseline deviation
        # -----------------------------------------------------

        if (
            features.abs_deviation
            >= self.config.risk.medium_deviation_bpm
        ):

            codes.append(
                "BASELINE_DEVIATION"
            )

        # -----------------------------------------------------
        # Percentage deviation
        # -----------------------------------------------------

        if (
            features.pct_deviation
            >= self.config.risk.medium_pct_deviation
        ):

            codes.append(
                "HIGH_PERCENTAGE_DEVIATION"
            )

        # -----------------------------------------------------
        # Rising trend
        # -----------------------------------------------------

        if (
            features.rate_of_change
            >= self.config.risk.rapid_change_rate_bpm_per_sec
        ):

            codes.append(
                "RISING_TREND"
            )

            codes.append(
                "RAPID_CHANGE"
            )

        # -----------------------------------------------------
        # Persistence
        # -----------------------------------------------------

        if (
            features.abnormality_persistence_samples
            >= self.config.escalation
            .escalate_medium_persistence_samples
        ):

            codes.append(
                "PERSISTENT_ABNORMALITY"
            )

        # -----------------------------------------------------
        # Recovery
        # -----------------------------------------------------

        if (
            features.recovery_state
            == RecoveryState.RECOVERING
        ):

            codes.append(
                "RECOVERY_DETECTED"
            )

        # -----------------------------------------------------
        # Escalation
        # -----------------------------------------------------

        if (
            transition_reason
            == "ESCALATION_CONFIRMED"
            or transition_reason
            == "EXTREME_SPIKE_ESCALATION"
        ):

            codes.append(
                "ESCALATION_CONFIRMED"
            )

        # -----------------------------------------------------
        # De-escalation
        # -----------------------------------------------------

        elif (
            transition_reason
            == "DEESCALATION_CONFIRMED"
        ):

            codes.append(
                "DEESCALATION_CONFIRMED"
            )

        # -----------------------------------------------------
        # Stable state
        # -----------------------------------------------------

        if not codes:

            codes.append(
                "STABLE_BASELINE"
            )

        # Remove duplicates while preserving order.
        return list(
            dict.fromkeys(codes)
        )

    # =========================================================
    # HUMAN-READABLE EXPLANATION
    # =========================================================

    def _build_explanation(
        self,
        features: TemporalFeatures,
        risk_level: RiskLevel,
        reason_codes: List[str],
    ) -> str:
        """
        Build an explainable natural-language description of
        the current physiological state.
        """

        # -----------------------------------------------------
        # LOW
        # -----------------------------------------------------

        if risk_level == RiskLevel.LOW:

            if (
                "RECOVERY_DETECTED"
                in reason_codes
            ):

                return (
                    "Physiological state recovering. "
                    f"Current HR is "
                    f"{features.current_hr:.1f} bpm "
                    f"(baseline "
                    f"{features.baseline_hr:.1f} bpm, "
                    f"deviation "
                    f"{features.abs_deviation:+.1f} bpm)."
                )

            return (
                "Physiological state is within "
                "normal personal baseline bounds. "
                f"Current HR is "
                f"{features.current_hr:.1f} bpm "
                f"(baseline "
                f"{features.baseline_hr:.1f} bpm, "
                f"deviation "
                f"{features.abs_deviation:+.1f} bpm)."
            )

        # -----------------------------------------------------
        # MEDIUM / HIGH
        # -----------------------------------------------------

        reasons_str = ", ".join(
            reason_codes
        )

        return (
            f"Risk Level {risk_level.value}: "
            f"Current HR "
            f"{features.current_hr:.1f} bpm "
            f"deviates by "
            f"{features.abs_deviation:+.1f} bpm "
            f"({features.pct_deviation:+.1f}%) "
            f"from personal baseline "
            f"{features.baseline_hr:.1f} bpm. "
            f"Rate of change: "
            f"{features.rate_of_change:+.2f} bpm/s. "
            f"Persistence: "
            f"{features.abnormality_persistence_seconds:.1f}s. "
            f"Triggers: [{reasons_str}]."
        )