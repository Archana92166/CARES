"""
CARES Personal Baseline Learning System.

Architecture:

    Trusted live HR
        |
        +--> real-time risk comparison (~1 second)
        |
        +--> initial personal calibration (>= 5 minutes)
        |
        +--> 3 consecutive trusted HR observations
        |       |
        |       +--> short-term mean
        |       +--> compare against personal baseline
        |       +--> UPDATE / HOLD
        |
        +--> daily trusted-data accumulation
        |       |
        |       +--> daily mean / median / std
        |
        +--> multi-day learning
                |
                +--> long-term personal baseline


IMPORTANT:

1. WESAD is an offline evaluation source only.
2. Runtime operation is designed for live wrist PPG/BVP-derived HR.
3. Risk evaluation may occur approximately every second.
4. Initial personal baseline requires at least 5 minutes of trusted
   LOW-risk observations.
5. Baseline adaptation uses three consecutive trusted observations.
6. HIGH/MEDIUM risk observations NEVER teach the personal baseline.
7. Invalid HR observations NEVER teach the personal baseline.
8. Every adaptation decision is logged with a reason.
9. Daily physiological summaries are stored separately.
10. The numerical adaptation parameters are CARES engineering
    decisions and are NOT claimed to be clinically validated.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Dict, List, Optional, Tuple

from .models import PhysiologicalSample


# =============================================================
# AUDIT DATA MODELS
# =============================================================


@dataclass
class BaselineAdaptationLog:
    """
    Audit record for one baseline-learning decision.

    Every UPDATE, HOLD, and PENDING decision is recorded so that
    the system can explain why the personal baseline did or did
    not change.
    """

    timestamp: str

    previous_baseline_bpm: Optional[float]

    observation_mean_bpm: Optional[float]

    observation_std_bpm: Optional[float]

    deviation_bpm: Optional[float]

    risk_level: str

    valid_samples: int

    required_samples: int

    signal_quality_mean: Optional[float]

    decision: str

    new_baseline_bpm: Optional[float]

    reason: str


@dataclass
class DailyBaselineRecord:
    """
    Trusted physiological summary for one calendar day.

    This record represents the person's observed HR behavior for
    that day. It is not a clinical diagnosis or medical baseline.
    """

    date: str

    trusted_samples: int

    mean_bpm: float

    median_bpm: float

    std_bpm: float

    minimum_bpm: float

    maximum_bpm: float

    eligible_observations: int

    adaptation_updates: int

    adaptation_holds: int


# =============================================================
# PERSONAL BASELINE MANAGER
# =============================================================


class PersonalBaselineManager:
    """
    Multi-timescale personal HR baseline manager.

    CARES timing protocol:

        Initial calibration:
            >= 300 trusted LOW-risk HR observations
            representing approximately 5 minutes at 1 Hz.

        Real-time comparison:
            approximately every HR update.

        Short-term adaptation:
            3 consecutive trusted LOW-risk observations.

        Adaptation:
            slow EMA-style update.

        Daily learning:
            calendar-day trusted HR aggregation.

        Long-term learning:
            gradual learning from completed daily summaries.

    The manager deliberately separates:

        CURRENT HR
            from
        PERSONAL BASELINE
            from
        DAILY BASELINE
            from
        LONG-TERM BASELINE
    """

    def __init__(
        self,
        initial_calibration_seconds: int = 300,
        adaptation_window_seconds: int = 3,
        adaptation_interval_seconds: int = 3,
        adaptation_alpha: float = 0.01,
        max_adaptation_deviation_bpm: float = 10.0,
        daily_log_path: str = "logs/personal_baseline_daily.json",
        adaptation_log_path: str = "logs/baseline_adaptation.jsonl",
    ) -> None:

        # -----------------------------------------------------
        # Configuration validation
        # -----------------------------------------------------

        if initial_calibration_seconds < 300:
            raise ValueError(
                "Initial calibration must be at least 300 seconds."
            )

        if adaptation_window_seconds <= 0:
            raise ValueError(
                "adaptation_window_seconds must be positive."
            )

        if adaptation_interval_seconds <= 0:
            raise ValueError(
                "adaptation_interval_seconds must be positive."
            )

        if not 0.0 < adaptation_alpha <= 1.0:
            raise ValueError(
                "adaptation_alpha must be in (0, 1]."
            )

        if max_adaptation_deviation_bpm <= 0:
            raise ValueError(
                "max_adaptation_deviation_bpm must be positive."
            )

        # -----------------------------------------------------
        # Timing configuration
        # -----------------------------------------------------

        self.initial_calibration_seconds = int(
            initial_calibration_seconds
        )

        self.adaptation_window_seconds = int(
            adaptation_window_seconds
        )

        self.adaptation_interval_seconds = int(
            adaptation_interval_seconds
        )

        self.adaptation_alpha = float(
            adaptation_alpha
        )

        self.max_adaptation_deviation_bpm = float(
            max_adaptation_deviation_bpm
        )

        # -----------------------------------------------------
        # Persistent log locations
        # -----------------------------------------------------

        self.daily_log_path = Path(
            daily_log_path
        )

        self.adaptation_log_path = Path(
            adaptation_log_path
        )

        # -----------------------------------------------------
        # Initial calibration state
        # -----------------------------------------------------

        self._calibration_samples: List[float] = []

        self._session_elapsed_seconds = 0.0

        self._personal_baseline: Optional[float] = None

        # -----------------------------------------------------
        # Short-term adaptation state
        # -----------------------------------------------------

        self._adaptation_samples: List[
            Tuple[float, float]
        ] = []

        self._last_adaptation_timestamp: Optional[
            float
        ] = None

        # -----------------------------------------------------
        # Daily learning state
        # -----------------------------------------------------

        self._daily_values: Dict[
            str,
            List[float],
        ] = defaultdict(list)

        self._daily_updates: Dict[
            str,
            int,
        ] = defaultdict(int)

        self._daily_holds: Dict[
            str,
            int,
        ] = defaultdict(int)

        self._daily_records: Dict[
            str,
            DailyBaselineRecord,
        ] = {}

        # -----------------------------------------------------
        # Long-term learning state
        # -----------------------------------------------------

        self._long_term_baseline: Optional[
            float
        ] = None

        self._long_term_days: int = 0

    # =========================================================
    # PROPERTIES
    # =========================================================

    @property
    def is_calibrated(self) -> bool:
        """
        True once the initial personal baseline has been
        established.
        """

        return self._personal_baseline is not None

    @property
    def personal_baseline(self) -> Optional[float]:
        """
        Current short/medium-term personal HR baseline.
        """

        return self._personal_baseline

    @property
    def long_term_baseline(self) -> Optional[float]:
        """
        Learned multi-day personal HR baseline.
        """

        return self._long_term_baseline

    @property
    def calibration_seconds(self) -> float:
        """
        Elapsed runtime observed by this manager.
        """

        return self._session_elapsed_seconds

    @property
    def calibration_samples(self) -> int:
        """
        Number of trusted LOW-risk observations used during
        initial calibration.
        """

        return len(self._calibration_samples)

    # =========================================================
    # SAMPLE TRUST
    # =========================================================

    @staticmethod
    def _sample_is_valid(
        sample: PhysiologicalSample,
    ) -> bool:
        """
        Only trusted HR measurements may teach CARES.

        hr_valid=False means the value may be a held previous
        estimate and therefore is not physiological evidence.
        """

        return bool(sample.hr_valid)

    @staticmethod
    def _sample_quality(
        sample: PhysiologicalSample,
    ) -> float:
        """
        Return BVP signal quality when available.

        If the source does not provide a quality metric,
        default to 1.0 because the HR measurement is already
        explicitly marked valid.
        """

        quality = float(
            sample.additional_metrics.get(
                "bvp_quality",
                1.0,
            )
        )

        return max(
            0.0,
            min(1.0, quality),
        )

    # =========================================================
    # SAMPLE PROCESSING
    # =========================================================

    def add_sample(
        self,
        sample: PhysiologicalSample,
        elapsed_seconds: float,
        risk_level: str = "LOW",
        observed_at: Optional[datetime] = None,
    ) -> Optional[BaselineAdaptationLog]:
        """
        Process one HR observation.

        Processing order:

        1. Validate the physiological sample.
        2. Reject invalid HR measurements from learning.
        3. Establish the initial personal baseline from >=5 minutes
           of trusted LOW-risk observations.
        4. After calibration, collect trusted LOW-risk HR values for
           daily learning.
        5. Build a 3-observation short adaptation window.
        6. Compare its mean with the current personal baseline.
        7. UPDATE slowly or HOLD with an explicit reason.
        8. Log the decision.

        IMPORTANT:

        Risk evaluation itself is not performed here.

        The risk engine supplies risk_level.

        This manager is responsible only for personal-baseline
        learning and its audit trail.
        """

        sample.validate()

        elapsed_seconds = float(
            elapsed_seconds
        )

        if elapsed_seconds < 0:
            raise ValueError(
                "elapsed_seconds cannot be negative."
            )

        self._session_elapsed_seconds = max(
            self._session_elapsed_seconds,
            elapsed_seconds,
        )

        if observed_at is None:
            observed_at = datetime.now(
                timezone.utc
            )

        # -----------------------------------------------------
        # Invalid measurement
        # -----------------------------------------------------

        if not self._sample_is_valid(sample):

            return self._handle_non_learning_sample(
                observed_at=observed_at,
                reason="INVALID_HR_MEASUREMENT",
            )

        hr = float(
            sample.heart_rate_bpm
        )

        date_key = observed_at.date().isoformat()

        # -----------------------------------------------------
        # INITIAL PERSONAL CALIBRATION
        # -----------------------------------------------------

        if not self.is_calibrated:

            # Only LOW-risk observations are trusted for
            # initial personal baseline construction.
            if risk_level == "LOW":

                self._calibration_samples.append(
                    hr
                )

            # At 1 Hz, 300 trusted observations represent
            # approximately five minutes of monitoring.
            #
            # We use sample count as the primary calibration
            # criterion because real-time HR observations are
            # discrete measurements.
            #
            # The elapsed-time requirement prevents a rapidly
            # arriving burst of samples from being interpreted
            # as five minutes of monitoring.
            minimum_observations = (
                self.initial_calibration_seconds
            )

            elapsed_requirement_met = (
                self._session_elapsed_seconds
                >= self.initial_calibration_seconds - 1
            )

            enough_observations = (
                len(self._calibration_samples)
                >= minimum_observations
            )

            if (
                enough_observations
                and elapsed_requirement_met
            ):

                self._personal_baseline = mean(
                    self._calibration_samples
                )

                # The sample that completes calibration is not
                # immediately used for adaptation. It belongs to
                # the calibration phase.
                return None

            return None

        # -----------------------------------------------------
        # DAILY TRUSTED DATA ACCUMULATION
        # -----------------------------------------------------

        # Daily summaries are built only from LOW-risk trusted
        # observations. This prevents an acute abnormal state
        # from becoming the person's "normal" daily baseline.
        if risk_level == "LOW":

            self._daily_values[
                date_key
            ].append(hr)

        # -----------------------------------------------------
        # NON-LOW RISK
        # -----------------------------------------------------

        if risk_level != "LOW":

            self._daily_holds[
                date_key
            ] += 1

            return self._create_hold_log(
                observed_at=observed_at,
                risk_level=risk_level,
                reason="RISK_STATE_NOT_ELIGIBLE",
            )

        # -----------------------------------------------------
        # SHORT-TERM ADAPTATION BUFFER
        # -----------------------------------------------------

        self._adaptation_samples.append(
            (
                elapsed_seconds,
                hr,
            )
        )

        # Keep a small rolling history.
        maximum_history_seconds = max(
            self.adaptation_window_seconds * 3,
            9,
        )

        cutoff = (
            elapsed_seconds
            - maximum_history_seconds
        )

        self._adaptation_samples = [
            item
            for item in self._adaptation_samples
            if item[0] >= cutoff
        ]

        # -----------------------------------------------------
        # THREE-OBSERVATION ADAPTATION WINDOW
        # -----------------------------------------------------

        #
        # At a 1-Hz HR stream:
        #
        #   t=301 -> observation 1
        #   t=302 -> observation 2
        #   t=303 -> observation 3
        #
        # These three trusted observations constitute the CARES
        # short adaptation window.
        #
        # We intentionally do NOT require the timestamp difference
        # to equal exactly 3 seconds because three 1-Hz observations
        # occupy two timestamp intervals.
        #

        required_samples = max(
            3,
            self.adaptation_window_seconds,
        )

        if (
            len(self._adaptation_samples)
            < required_samples
        ):

            return self._create_pending_log(
                observed_at=observed_at,
                risk_level="LOW",
                reason="WAITING_FOR_ADAPTATION_WINDOW",
                valid_samples=len(
                    self._adaptation_samples
                ),
                required_samples=required_samples,
            )

        # Use the most recent N trusted observations.
        window = self._adaptation_samples[
            -required_samples:
        ]

        # -----------------------------------------------------
        # ADAPTATION INTERVAL
        # -----------------------------------------------------

        if self._last_adaptation_timestamp is not None:

            elapsed_since_last_update = (
                elapsed_seconds
                - self._last_adaptation_timestamp
            )

            if (
                elapsed_since_last_update
                < self.adaptation_interval_seconds
            ):

                return self._create_pending_log(
                    observed_at=observed_at,
                    risk_level="LOW",
                    reason="ADAPTATION_INTERVAL_NOT_REACHED",
                    valid_samples=len(window),
                    required_samples=required_samples,
                )

        # Mark the decision time.
        self._last_adaptation_timestamp = (
            elapsed_seconds
        )

        return self._evaluate_adaptation_window(
            window=window,
            sample=sample,
            observed_at=observed_at,
        )

    # =========================================================
    # ADAPTATION
    # =========================================================

    def _evaluate_adaptation_window(
        self,
        window: List[Tuple[float, float]],
        sample: PhysiologicalSample,
        observed_at: datetime,
    ) -> BaselineAdaptationLog:
        """
        Evaluate a completed short adaptation window.

        The short-window mean is compared against the current
        personal baseline.

        A small consistent change can slowly update the baseline.

        A large change or inconsistent window is held.
        """

        values = [
            value
            for _, value in window
        ]

        observation_mean = mean(
            values
        )

        observation_std = (
            pstdev(values)
            if len(values) > 1
            else 0.0
        )

        previous_baseline = (
            self._personal_baseline
        )

        if previous_baseline is None:
            raise RuntimeError(
                "Adaptation attempted before baseline calibration."
            )

        deviation = (
            observation_mean
            - previous_baseline
        )

        quality = self._sample_quality(
            sample
        )

        # -----------------------------------------------------
        # GATE 1 — SIGNAL QUALITY
        # -----------------------------------------------------

        if quality <= 0.10:

            decision = "HOLD"

            reason = (
                "LOW_SIGNAL_QUALITY"
            )

        # -----------------------------------------------------
        # GATE 2 — LARGE DEVIATION
        # -----------------------------------------------------

        elif (
            abs(deviation)
            > self.max_adaptation_deviation_bpm
        ):

            decision = "HOLD"

            reason = (
                "LARGE_BASELINE_DEVIATION"
            )

        # -----------------------------------------------------
        # GATE 3 — INCONSISTENT SHORT WINDOW
        # -----------------------------------------------------

        elif observation_std > 5.0:

            decision = "HOLD"

            reason = (
                "SHORT_WINDOW_INCONSISTENCY"
            )

        # -----------------------------------------------------
        # UPDATE
        # -----------------------------------------------------

        else:

            new_baseline = (
                (1.0 - self.adaptation_alpha)
                * previous_baseline
                + self.adaptation_alpha
                * observation_mean
            )

            self._personal_baseline = (
                new_baseline
            )

            decision = "UPDATED"

            reason = (
                "VALID_LOW_RISK_CONSISTENT_VARIATION"
            )

        # -----------------------------------------------------
        # Resulting baseline
        # -----------------------------------------------------

        if decision == "UPDATED":

            new_value = (
                self._personal_baseline
            )

        else:

            new_value = (
                previous_baseline
            )

        date_key = (
            observed_at.date().isoformat()
        )

        if decision == "UPDATED":

            self._daily_updates[
                date_key
            ] += 1

        else:

            self._daily_holds[
                date_key
            ] += 1

        # -----------------------------------------------------
        # Audit record
        # -----------------------------------------------------

        log = BaselineAdaptationLog(
            timestamp=observed_at.isoformat(),

            previous_baseline_bpm=round(
                previous_baseline,
                4,
            ),

            observation_mean_bpm=round(
                observation_mean,
                4,
            ),

            observation_std_bpm=round(
                observation_std,
                4,
            ),

            deviation_bpm=round(
                deviation,
                4,
            ),

            risk_level="LOW",

            valid_samples=len(
                values
            ),

            required_samples=len(
                values
            ),

            signal_quality_mean=round(
                quality,
                4,
            ),

            decision=decision,

            new_baseline_bpm=round(
                new_value,
                4,
            ),

            reason=reason,
        )

        self._write_adaptation_log(
            log
        )

        return log

    # =========================================================
    # DAILY BASELINE
    # =========================================================

    def finalize_day(
        self,
        date_key: str,
    ) -> Optional[DailyBaselineRecord]:
        """
        Finalize one calendar day's trusted HR observations.

        The daily record is intentionally separate from the
        instantaneous personal baseline.

        This allows CARES to answer:

            "What was this person's overall HR behavior
             on this date?"

        without confusing that statistic with a single live
        HR measurement.
        """

        values = self._daily_values.get(
            date_key,
            [],
        )

        if not values:

            return None

        record = DailyBaselineRecord(
            date=date_key,

            trusted_samples=len(
                values
            ),

            mean_bpm=round(
                mean(values),
                4,
            ),

            median_bpm=round(
                median(values),
                4,
            ),

            std_bpm=round(
                pstdev(values)
                if len(values) > 1
                else 0.0,
                4,
            ),

            minimum_bpm=round(
                min(values),
                4,
            ),

            maximum_bpm=round(
                max(values),
                4,
            ),

            eligible_observations=len(
                values
            ),

            adaptation_updates=self._daily_updates.get(
                date_key,
                0,
            ),

            adaptation_holds=self._daily_holds.get(
                date_key,
                0,
            ),
        )

        self._daily_records[
            date_key
        ] = record

        self._write_daily_records()

        self._update_long_term_baseline()

        return record

    # =========================================================
    # LONG-TERM BASELINE
    # =========================================================

    def _update_long_term_baseline(
        self,
    ) -> None:
        """
        Learn gradually from completed daily summaries.

        The system does NOT replace the live personal baseline
        with a single day's mean.

        Instead, completed days become long-term evidence.
        """

        daily_records = list(
            self._daily_records.values()
        )

        if not daily_records:

            return

        daily_means = [
            record.mean_bpm
            for record in daily_records
        ]

        self._long_term_days = len(
            daily_means
        )

        if self._long_term_baseline is None:

            self._long_term_baseline = mean(
                daily_means
            )

            return

        # Conservative multi-day learning.
        #
        # As more days accumulate, each new day has less
        # influence on the long-term estimate.
        alpha = min(
            0.10,
            1.0
            / max(
                10,
                self._long_term_days,
            ),
        )

        latest_mean = daily_means[-1]

        self._long_term_baseline = (
            (1.0 - alpha)
            * self._long_term_baseline
            + alpha
            * latest_mean
        )

    # =========================================================
    # PENDING LOGGING
    # =========================================================

    def _create_pending_log(
        self,
        observed_at: datetime,
        risk_level: str,
        reason: str,
        valid_samples: int,
        required_samples: int,
    ) -> BaselineAdaptationLog:
        """
        Record that an adaptation decision is not yet possible.

        IMPORTANT:

            PENDING does not change the personal baseline.

        It simply records that CARES is still collecting evidence.
        """

        baseline = (
            self._personal_baseline
        )

        log = BaselineAdaptationLog(
            timestamp=observed_at.isoformat(),

            previous_baseline_bpm=(
                round(
                    baseline,
                    4,
                )
                if baseline is not None
                else None
            ),

            observation_mean_bpm=None,

            observation_std_bpm=None,

            deviation_bpm=None,

            risk_level=risk_level,

            valid_samples=valid_samples,

            required_samples=required_samples,

            signal_quality_mean=None,

            decision="PENDING",

            new_baseline_bpm=(
                round(
                    baseline,
                    4,
                )
                if baseline is not None
                else None
            ),

            reason=reason,
        )

        self._write_adaptation_log(
            log
        )

        return log

    # =========================================================
    # HOLD LOGGING
    # =========================================================

    def _handle_non_learning_sample(
        self,
        observed_at: datetime,
        reason: str,
    ) -> BaselineAdaptationLog:
        """
        Record an observation that cannot teach the baseline.
        """

        return self._create_hold_log(
            observed_at=observed_at,
            risk_level="NOT_ELIGIBLE",
            reason=reason,
        )

    def _create_hold_log(
        self,
        observed_at: datetime,
        risk_level: str,
        reason: str,
    ) -> BaselineAdaptationLog:
        """
        Create an explicit HOLD audit record.

        HOLD means:

            The system observed something,
            but deliberately refused to teach the baseline.
        """

        baseline = (
            self._personal_baseline
        )

        date_key = (
            observed_at.date().isoformat()
        )

        self._daily_holds[
            date_key
        ] += 1

        log = BaselineAdaptationLog(
            timestamp=observed_at.isoformat(),

            previous_baseline_bpm=(
                round(
                    baseline,
                    4,
                )
                if baseline is not None
                else None
            ),

            observation_mean_bpm=None,

            observation_std_bpm=None,

            deviation_bpm=None,

            risk_level=risk_level,

            valid_samples=0,

            required_samples=max(
                3,
                self.adaptation_window_seconds,
            ),

            signal_quality_mean=None,

            decision="HOLD",

            new_baseline_bpm=(
                round(
                    baseline,
                    4,
                )
                if baseline is not None
                else None
            ),

            reason=reason,
        )

        self._write_adaptation_log(
            log
        )

        return log

    # =========================================================
    # ADAPTATION LOGGING
    # =========================================================

    def _write_adaptation_log(
        self,
        log: BaselineAdaptationLog,
    ) -> None:
        """
        Append one audit decision to JSONL storage.
        """

        self.adaptation_log_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with self.adaptation_log_path.open(
            "a",
            encoding="utf-8",
        ) as f:

            f.write(
                json.dumps(
                    asdict(log),
                    separators=(
                        ",",
                        ":",
                    ),
                )
                + "\n"
            )

    # =========================================================
    # DAILY LOGGING
    # =========================================================

    def _write_daily_records(
        self,
    ) -> None:
        """
        Persist all completed daily records.
        """

        self.daily_log_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = {
            date: asdict(record)
            for date, record
            in self._daily_records.items()
        }

        with self.daily_log_path.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                payload,
                f,
                indent=2,
            )

    # =========================================================
    # SESSION RESET
    # =========================================================

    def reset_session(
        self,
    ) -> None:
        """
        Reset live-session calibration/adaptation state.

        Persistent daily and long-term learning remains intact.

        This is useful when a new live monitoring session starts.
        """

        self._calibration_samples.clear()

        self._adaptation_samples.clear()

        self._session_elapsed_seconds = 0.0

        self._last_adaptation_timestamp = None

        self._personal_baseline = None