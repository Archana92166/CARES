"""
Deterministic Feature Extraction for CARES Engine.

Extracts:
1. Absolute deviation from baseline (bpm)
2. Percentage deviation from baseline (%)
3. Short-term rate/trend (bpm per second)
4. Abnormality persistence (sample count and seconds duration)
5. Recovery behavior (RECOVERING / RECOVERED / NO_RECOVERY)
"""

from typing import List, Optional
from .config import FeatureConfig
from .models import PhysiologicalSample, RecoveryState, TemporalFeatures


class FeatureExtractor:
    """
    Extracts deterministic temporal physiological features from a sample history stream
    relative to a personal baseline.
    """

    def __init__(self, config: Optional[FeatureConfig] = None) -> None:
        self.config: FeatureConfig = config or FeatureConfig()
        self._sample_history: List[PhysiologicalSample] = []
        self._consecutive_abnormal_samples: int = 0
        self._abnormality_start_timestamp: Optional[float] = None
        self._peak_hr_during_abnormality: float = 0.0

    def reset(self) -> None:
        self._sample_history.clear()
        self._consecutive_abnormal_samples = 0
        self._abnormality_start_timestamp = None
        self._peak_hr_during_abnormality = 0.0

    def extract_features(
        self,
        current_sample: PhysiologicalSample,
        baseline_hr: Optional[float],
    ) -> TemporalFeatures:
        """
        Extracts temporal features for the current sample.
        
        If baseline_hr is None (not calibrated), returns fallback zeroed metrics.
        """
        current_sample.validate()
        self._sample_history.append(current_sample)

        # Maintain window for short-term rate calculation
        max_win = max(self.config.short_term_window_samples * 2, 20)
        if len(self._sample_history) > max_win:
            self._sample_history.pop(0)

        timestamp = current_sample.timestamp
        current_hr = current_sample.heart_rate_bpm

        if baseline_hr is None or baseline_hr <= 0:
            return TemporalFeatures(
                timestamp=timestamp,
                current_hr=current_hr,
                baseline_hr=0.0,
                abs_deviation=0.0,
                pct_deviation=0.0,
                rate_of_change=0.0,
                abnormality_persistence_samples=0,
                abnormality_persistence_seconds=0.0,
                is_abnormal=False,
                recovery_state=RecoveryState.NO_RECOVERY,
            )

        # 1. Absolute deviation
        abs_dev = current_hr - baseline_hr

        # 2. Percentage deviation
        pct_dev = (abs_dev / baseline_hr) * 100.0

        # 3. Short-term rate/trend (bpm per second)
        rate_of_change = self._calculate_rate_of_change()

        # 4. Abnormality persistence tracking
        is_abnormal = abs_dev >= self.config.persistence_deviation_threshold_bpm

        if is_abnormal:
            self._consecutive_abnormal_samples += 1
            if self._abnormality_start_timestamp is None:
                self._abnormality_start_timestamp = timestamp
                self._peak_hr_during_abnormality = current_hr
            else:
                self._peak_hr_during_abnormality = max(self._peak_hr_during_abnormality, current_hr)

            persistence_seconds = max(0.0, timestamp - self._abnormality_start_timestamp)
        else:
            if self._consecutive_abnormal_samples > 0:
                # Just returned to normal bounds
                persistence_seconds = 0.0
            else:
                persistence_seconds = 0.0

            # Reset abnormality tracking when sample drops below threshold
            self._consecutive_abnormal_samples = 0
            self._abnormality_start_timestamp = None

        # 5. Recovery behavior classification
        recovery_state = self._classify_recovery(
            abs_dev=abs_dev,
            rate_of_change=rate_of_change,
            is_abnormal=is_abnormal,
        )

        return TemporalFeatures(
            timestamp=timestamp,
            current_hr=current_hr,
            baseline_hr=baseline_hr,
            abs_deviation=abs_dev,
            pct_deviation=pct_dev,
            rate_of_change=rate_of_change,
            abnormality_persistence_samples=self._consecutive_abnormal_samples,
            abnormality_persistence_seconds=persistence_seconds,
            is_abnormal=is_abnormal,
            recovery_state=recovery_state,
        )

    def _calculate_rate_of_change(self) -> float:
        """
        Computes rate of heart rate change in bpm/sec over short term window.
        Uses linear regression slope or time difference over window.
        """
        n = len(self._sample_history)
        if n < 2:
            return 0.0

        win_size = min(n, self.config.short_term_window_samples)
        recent_samples = self._sample_history[-win_size:]

        dt = recent_samples[-1].timestamp - recent_samples[0].timestamp
        if dt <= 0:
            return 0.0

        d_hr = recent_samples[-1].heart_rate_bpm - recent_samples[0].heart_rate_bpm
        return d_hr / dt

    def _classify_recovery(
        self,
        abs_dev: float,
        rate_of_change: float,
        is_abnormal: bool,
    ) -> RecoveryState:
        """
        Classifies recovery state based on trend and deviation history.
        """
        if abs_dev < self.config.persistence_deviation_threshold_bpm / 2.0:
            if self._peak_hr_during_abnormality > 0:
                self._peak_hr_during_abnormality = 0.0
                return RecoveryState.RECOVERED
            return RecoveryState.NO_RECOVERY

        # If elevated but showing steady negative rate of change (decrease toward baseline)
        if rate_of_change <= self.config.recovery_rate_threshold_bpm_per_sec:
            return RecoveryState.RECOVERING

        return RecoveryState.NO_RECOVERY
