"""
CARES Wrist-BVP Heart-Rate Estimator.

Signal path:

    Raw wrist BVP
        -> band-pass filtering
        -> candidate HR estimation
           - autocorrelation
           - spectral candidate
           - pulse consistency
        -> signal quality assessment
        -> continuity-aware HR selection

This module deliberately contains NO ECG processing.

The estimator is designed to accept the same BVP stream whether it
originates from WESAD or, later, a real wrist PPG sensor.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt


@dataclass
class HREstimate:
    """Single HR estimate produced from one BVP analysis window."""

    timestamp: float
    heart_rate_bpm: float
    quality: float
    accepted: bool
    method: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "timestamp": float(self.timestamp),
            "heart_rate_bpm": float(self.heart_rate_bpm),
            "quality": float(self.quality),
            "accepted": bool(self.accepted),
            "method": self.method,
            "reason": self.reason,
        }


class WristBVPHREstimator:
    """
    Continuity-aware wrist-BVP heart-rate estimator.

    Default operating point:
        BVP sampling rate : 64 Hz
        analysis window   : 10 seconds
        update interval   : 1 second
        HR range          : 40-180 BPM

    The estimator is intentionally independent of hardware SDKs.
    A future PPG microcontroller only needs to provide BVP samples.
    """

    def __init__(
        self,
        sampling_rate: float = 64.0,
        window_seconds: float = 10.0,
        step_seconds: float = 1.0,
        min_hr: float = 40.0,
        max_hr: float = 180.0,
        min_quality: float = 0.10,
        max_jump_bpm: float = 20.0,
        continuity_weight: float = 0.015,
    ) -> None:
        if sampling_rate <= 0:
            raise ValueError("sampling_rate must be positive.")

        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive.")

        if step_seconds <= 0:
            raise ValueError("step_seconds must be positive.")

        if min_hr <= 0 or max_hr <= min_hr:
            raise ValueError("Invalid HR range.")

        self.fs = float(sampling_rate)
        self.window_seconds = float(window_seconds)
        self.step_seconds = float(step_seconds)
        self.window_samples = int(round(self.fs * self.window_seconds))
        self.step_samples = int(round(self.fs * self.step_seconds))

        self.min_hr = float(min_hr)
        self.max_hr = float(max_hr)
        self.min_quality = float(min_quality)
        self.max_jump_bpm = float(max_jump_bpm)
        self.continuity_weight = float(continuity_weight)

        self.min_lag = max(
            1,
            int(self.fs * 60.0 / self.max_hr),
        )
        self.max_lag = int(
            self.fs * 60.0 / self.min_hr
        )

        self._previous_hr: Optional[float] = None

        # Filter designed once and reused for every window.
        self._sos = butter(
            4,
            [0.7, 3.5],
            btype="bandpass",
            fs=self.fs,
            output="sos",
        )

    def reset(self) -> None:
        """Reset temporal continuity state."""
        self._previous_hr = None

    # ---------------------------------------------------------
    # PREPROCESSING
    # ---------------------------------------------------------

    def preprocess(self, signal: np.ndarray) -> np.ndarray:
        """Band-pass filter a BVP segment in the pulse-frequency region."""

        x = np.asarray(signal, dtype=float).reshape(-1)

        if len(x) < self.window_samples:
            raise ValueError(
                f"At least {self.window_samples} BVP samples are required."
            )

        if not np.isfinite(x).all():
            raise ValueError("BVP signal contains non-finite values.")

        filtered = sosfiltfilt(self._sos, x)
        filtered -= np.mean(filtered)

        return filtered

    # ---------------------------------------------------------
    # AUTOCORRELATION
    # ---------------------------------------------------------

    def _autocorrelation_candidate(
        self,
        x: np.ndarray,
    ) -> Tuple[Optional[float], float]:
        """
        Estimate HR from the strongest autocorrelation lag.

        Returns:
            (heart_rate_bpm, quality)
        """

        x = x - np.mean(x)
        energy = float(np.dot(x, x))

        if energy <= 1e-12:
            return None, 0.0

        autocorr = np.correlate(x, x, mode="full")
        autocorr = autocorr[len(x) - 1:]

        autocorr /= autocorr[0] + 1e-12

        upper = min(self.max_lag, len(autocorr) - 1)

        if upper <= self.min_lag:
            return None, 0.0

        search = autocorr[self.min_lag:upper + 1]

        # Local maxima are preferable to simply taking an arbitrary lag.
        peaks, _ = find_peaks(search)

        if len(peaks) == 0:
            relative_index = int(np.argmax(search))
        else:
            relative_index = int(
                peaks[np.argmax(search[peaks])]
            )

        lag = self.min_lag + relative_index

        quality = float(autocorr[lag])

        if quality <= 0:
            return None, 0.0

        hr = 60.0 * self.fs / lag

        if not self.min_hr <= hr <= self.max_hr:
            return None, 0.0

        return float(hr), float(np.clip(quality, 0.0, 1.0))

    # ---------------------------------------------------------
    # SPECTRAL CANDIDATE
    # ---------------------------------------------------------

    def _spectral_candidate(
        self,
        x: np.ndarray,
    ) -> Tuple[Optional[float], float]:
        """
        Estimate HR from the dominant pulse-frequency component.
        """

        x = x - np.mean(x)

        frequencies = np.fft.rfftfreq(
            len(x),
            d=1.0 / self.fs,
        )

        spectrum = np.abs(np.fft.rfft(x))

        valid = (
            (frequencies >= self.min_hr / 60.0)
            & (frequencies <= self.max_hr / 60.0)
        )

        if not np.any(valid):
            return None, 0.0

        valid_indices = np.flatnonzero(valid)
        magnitudes = spectrum[valid_indices]

        if len(magnitudes) == 0:
            return None, 0.0

        peak_index = valid_indices[int(np.argmax(magnitudes))]
        peak_frequency = frequencies[peak_index]

        if peak_frequency <= 0:
            return None, 0.0

        hr = float(peak_frequency * 60.0)

        # Spectral concentration quality.
        total_power = float(np.sum(magnitudes ** 2)) + 1e-12
        peak_power = float(spectrum[peak_index] ** 2)

        quality = peak_power / total_power
        quality = float(np.clip(quality * 5.0, 0.0, 1.0))

        return hr, quality

    # ---------------------------------------------------------
    # PULSE CONSISTENCY
    # ---------------------------------------------------------

    def _peak_candidate(
        self,
        x: np.ndarray,
    ) -> Tuple[Optional[float], float]:
        """
        Estimate HR from pulse peaks and evaluate beat regularity.
        """

        std = float(np.std(x))

        if std <= 1e-9:
            return None, 0.0

        peaks, _ = find_peaks(
            x,
            distance=int(self.fs * 60.0 / self.max_hr),
            prominence=std * 0.20,
        )

        if len(peaks) < 3:
            return None, 0.0

        intervals = np.diff(peaks) / self.fs

        valid = intervals[
            (intervals >= 60.0 / self.max_hr)
            & (intervals <= 60.0 / self.min_hr)
        ]

        if len(valid) < 2:
            return None, 0.0

        median_ibi = float(np.median(valid))

        if median_ibi <= 0:
            return None, 0.0

        hr = 60.0 / median_ibi

        interval_cv = float(
            np.std(valid) / (np.mean(valid) + 1e-12)
        )

        regularity = 1.0 / (1.0 + interval_cv)

        # More valid beats provide stronger evidence.
        count_factor = min(1.0, len(valid) / 8.0)

        quality = float(
            np.clip(regularity * count_factor, 0.0, 1.0)
        )

        return float(hr), quality

    # ---------------------------------------------------------
    # CANDIDATE SELECTION
    # ---------------------------------------------------------

    def _select_candidate(
        self,
        candidates: List[Tuple[str, float, float]],
    ) -> Tuple[float, float, str, str]:
        """
        Select the most trustworthy HR candidate.

        Candidate tuple:
            (method, HR, quality)
        """

        if not candidates:
            raise ValueError("No valid HR candidates available.")

        scored = []

        for method, hr, quality in candidates:
            score = quality

            # Temporal continuity is deliberately a small tie-breaking
            # influence rather than a mechanism that can override signal
            # evidence indefinitely.
            if self._previous_hr is not None:
                jump = abs(hr - self._previous_hr)

                if jump <= self.max_jump_bpm:
                    score += self.continuity_weight * (
                        1.0 - jump / self.max_jump_bpm
                    )
                else:
                    score -= self.continuity_weight

            scored.append(
                (score, method, hr, quality)
            )

        scored.sort(reverse=True)

        _, method, hr, quality = scored[0]

        reason = "SIGNAL_SUPPORTED"

        if self._previous_hr is not None:
            jump = abs(hr - self._previous_hr)

            if jump > self.max_jump_bpm:
                reason = "LARGE_CHANGE"

        accepted = quality >= self.min_quality

        if not accepted:
            reason = "LOW_QUALITY"

        return (
            float(hr),
            float(np.clip(quality, 0.0, 1.0)),
            method,
            reason,
        )

    # ---------------------------------------------------------
    # SINGLE WINDOW
    # ---------------------------------------------------------

    def estimate_window(
        self,
        signal: np.ndarray,
        timestamp: float = 0.0,
    ) -> HREstimate:
        """
        Estimate HR from one complete BVP window.
        """

        x = self.preprocess(signal)

        candidates: List[Tuple[str, float, float]] = []

        ac_hr, ac_quality = self._autocorrelation_candidate(x)
        if ac_hr is not None:
            candidates.append(
                ("AUTOCORRELATION", ac_hr, ac_quality)
            )

        fft_hr, fft_quality = self._spectral_candidate(x)
        if fft_hr is not None:
            candidates.append(
                ("SPECTRAL", fft_hr, fft_quality)
            )

        peak_hr, peak_quality = self._peak_candidate(x)
        if peak_hr is not None:
            candidates.append(
                ("PULSE_CONSISTENCY", peak_hr, peak_quality)
            )

        if not candidates:
            return HREstimate(
                timestamp=float(timestamp),
                heart_rate_bpm=(
                    self._previous_hr
                    if self._previous_hr is not None
                    else 0.0
                ),
                quality=0.0,
                accepted=False,
                method="NONE",
                reason="NO_VALID_CANDIDATE",
            )

        hr, quality, method, reason = self._select_candidate(
            candidates
        )

        # A large unexplained jump is not automatically rejected;
        # the decision engine must remain capable of detecting genuine
        # physiological change. Signal quality determines validity.
        if quality >= self.min_quality:
            self._previous_hr = hr

        return HREstimate(
            timestamp=float(timestamp),
            heart_rate_bpm=hr,
            quality=quality,
            accepted=quality >= self.min_quality,
            method=method,
            reason=reason,
        )

    # ---------------------------------------------------------
    # STREAM PROCESSING
    # ---------------------------------------------------------

    def estimate_stream(
        self,
        bvp: np.ndarray,
        start_timestamp: float = 0.0,
    ) -> List[HREstimate]:
        """
        Produce 1-Hz HR estimates from a continuous BVP stream.
        """

        x = np.asarray(bvp, dtype=float).reshape(-1)

        if len(x) < self.window_samples:
            return []

        self.reset()

        estimates: List[HREstimate] = []

        for start in range(
            0,
            len(x) - self.window_samples + 1,
            self.step_samples,
        ):
            end = start + self.window_samples

            window = x[start:end]

            timestamp = (
                start_timestamp
                + start / self.fs
            )

            estimate = self.estimate_window(
                window,
                timestamp=timestamp,
            )

            estimates.append(estimate)

        return estimates
