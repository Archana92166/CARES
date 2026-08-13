"""
WESAD Dataset Parser for CARES.

Final CARES signal path:

    WESAD wrist BVP (64 Hz)
        -> WristBVPHREstimator
        -> 1 Hz HR estimates
        -> PhysiologicalSample
        -> CARES Decision Engine

ECG is intentionally NOT used.

The parser preserves WESAD condition labels by mapping each generated
HR timestamp back to the original WESAD label timeline.
"""

import os
import pickle
import random
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from engine.hr_estimator import WristBVPHREstimator
from engine.models import PhysiologicalSample


@dataclass
class WESADSubjectData:
    """Container for one WESAD subject's wrist-BVP-derived HR stream."""

    subject_id: str
    samples: List[PhysiologicalSample]
    labels: List[int]

    # Retained for compatibility with the existing evaluation framework.
    # CARES itself does not use HRV.
    hrv_rmssd: Optional[List[float]] = None


class WESADParser:
    """
    Loads official WESAD wrist-BVP data and converts it into
    hardware-compatible HR samples.

    No ECG processing is performed.
    """

    VALID_SUBJECT_IDS = [
        f"S{i}" for i in range(2, 18) if i != 12
    ]

    BVP_FS = 64.0
    LABEL_FS = 700.0

    def __init__(self, data_dir: str = "data/wesad") -> None:
        self.data_dir = data_dir

    def load_subject(
        self,
        subject_id: str,
        seed: Optional[int] = 42,
    ) -> WESADSubjectData:
        """
        Load a subject.

        If the official WESAD pickle exists, use the real wrist BVP.
        Otherwise retain the existing protocol-stream fallback.
        """

        pkl_path = os.path.join(
            self.data_dir,
            subject_id,
            f"{subject_id}.pkl",
        )

        if os.path.exists(pkl_path):
            return self._parse_pickle_file(
                subject_id,
                pkl_path,
            )

        return self._generate_wesad_protocol_stream(
            subject_id,
            seed=seed,
        )

    def load_all_subjects(
        self,
        seed: Optional[int] = 42,
    ) -> Dict[str, WESADSubjectData]:
        """Load all supported WESAD subjects."""

        return {
            sid: self.load_subject(sid, seed=seed)
            for sid in self.VALID_SUBJECT_IDS
        }

    # =========================================================
    # REAL WESAD DATA
    # =========================================================

    def _parse_pickle_file(
        self,
        subject_id: str,
        filepath: str,
    ) -> WESADSubjectData:
        """
        Parse official WESAD data using ONLY wrist BVP.

        Important:
            BVP = 64 Hz
            WESAD labels = 700 Hz

        HR timestamps are therefore mapped to labels using time,
        not by incorrectly treating BVP as a 700 Hz signal.
        """

        with open(filepath, "rb") as f:
            data = pickle.load(f, encoding="latin1")

        if "signal" not in data:
            raise ValueError(
                f"{subject_id}: missing 'signal' in WESAD file."
            )

        wrist = data["signal"].get("wrist", {})

        if "BVP" not in wrist:
            raise ValueError(
                f"{subject_id}: wrist BVP signal not found."
            )

        if "label" not in data:
            raise ValueError(
                f"{subject_id}: WESAD labels not found."
            )

        bvp = np.asarray(
            wrist["BVP"],
            dtype=float,
        ).reshape(-1)

        labels = np.asarray(
            data["label"]
        ).reshape(-1)

        if len(bvp) == 0:
            raise ValueError(
                f"{subject_id}: empty wrist BVP signal."
            )

        if not np.isfinite(bvp).all():
            raise ValueError(
                f"{subject_id}: wrist BVP contains non-finite values."
            )

        print(
            f"[WESAD] {subject_id}: "
            f"{len(bvp)} BVP samples @ {self.BVP_FS:g} Hz"
        )

        estimator = WristBVPHREstimator(
            sampling_rate=self.BVP_FS,
            window_seconds=10.0,
            step_seconds=1.0,
            min_hr=40.0,
            max_hr=180.0,
            min_quality=0.10,
        )

        estimates = estimator.estimate_stream(
            bvp,
            start_timestamp=0.0,
        )

        if not estimates:
            raise ValueError(
                f"{subject_id}: no HR estimates generated."
            )

        samples: List[PhysiologicalSample] = []
        output_labels: List[int] = []

        last_valid_hr: Optional[float] = None

        for estimate in estimates:

            # -------------------------------------------------
            # Accepted estimate
            # -------------------------------------------------

            if estimate.accepted:
                hr = estimate.heart_rate_bpm
                last_valid_hr = hr

            # -------------------------------------------------
            # Rejected estimate
            #
            # We do not invent a new physiological value.
            # For continuous engine operation, hold the last
            # trusted HR and explicitly mark this sample invalid.
            # -------------------------------------------------

            elif last_valid_hr is not None:
                hr = last_valid_hr

            else:
                # No trusted HR exists yet.
                continue

            sample = PhysiologicalSample(
                timestamp=estimate.timestamp,
                heart_rate_bpm=float(hr),
                additional_metrics={
                    "bvp_quality": float(estimate.quality),
                    "hr_valid": 1.0 if estimate.accepted else 0.0,
                },
            )

            samples.append(sample)

            # -------------------------------------------------
            # Map HR timestamp to original WESAD label.
            # -------------------------------------------------

            label_index = int(
                round(
                    estimate.timestamp
                    * self.LABEL_FS
                )
            )

            label_index = max(
                0,
                min(
                    label_index,
                    len(labels) - 1,
                ),
            )

            output_labels.append(
                int(labels[label_index])
            )

        if not samples:
            raise ValueError(
                f"{subject_id}: no usable HR samples generated."
            )

        valid_count = sum(
            1
            for s in samples
            if s.additional_metrics.get("hr_valid", 0.0) >= 1.0
        )

        print(
            f"[WESAD] {subject_id}: "
            f"{len(samples)} HR samples, "
            f"{valid_count} directly estimated"
        )

        return WESADSubjectData(
            subject_id=subject_id,
            samples=samples,
            labels=output_labels,
            hrv_rmssd=None,
        )

    # =========================================================
    # FALLBACK BENCHMARK STREAM
    # =========================================================

    def _generate_wesad_protocol_stream(
        self,
        subject_id: str,
        seed: Optional[int] = 42,
    ) -> WESADSubjectData:
        """
        Existing protocol-aligned synthetic fallback.

        This fallback exists only when official WESAD files are absent.
        Real evaluation should use official wrist-BVP files.
        """

        subj_num = int(
            subject_id.replace("S", "")
        )

        rng = random.Random(
            seed + subj_num * 100
            if seed is not None
            else None
        )

        subject_baseline_hr = (
            65.0
            + (subj_num % 7) * 3.0
            + rng.uniform(-2.0, 2.0)
        )

        subject_resting_rmssd = (
            45.0 + rng.uniform(-8.0, 8.0)
        )

        samples: List[PhysiologicalSample] = []
        labels: List[int] = []
        rmssd_list: List[float] = []

        current_time = 0.0

        protocol_segments = [
            (1, 300, 0.0, 0.0, 1.0),
            (2, 300, 18.0, 10.0, 1.5),
            (3, 180, 5.0, 2.0, 1.2),
            (4, 180, 1.0, -0.3, 1.0),
        ]

        current_hr = subject_baseline_hr

        for (
            label_id,
            duration_sec,
            target_elevation,
            target_slope,
            noise_std,
        ) in protocol_segments:

            target_hr = (
                subject_baseline_hr
                + target_elevation
            )

            for t in range(duration_sec):

                if label_id == 2 and t < 30:

                    current_hr += (
                        target_slope * 0.1
                        + rng.gauss(0, noise_std)
                    )

                    current_hr = min(
                        target_hr + 10.0,
                        max(
                            subject_baseline_hr,
                            current_hr,
                        ),
                    )

                elif label_id == 4:

                    current_hr = (
                        subject_baseline_hr
                        + (
                            current_hr
                            - subject_baseline_hr
                        ) * 0.98
                        + rng.gauss(0, noise_std)
                    )

                else:

                    current_hr = (
                        current_hr * 0.9
                        + target_hr * 0.1
                        + rng.gauss(0, noise_std)
                    )

                current_hr = max(
                    45.0,
                    min(190.0, current_hr),
                )

                if label_id == 2:

                    current_rmssd = max(
                        12.0,
                        subject_resting_rmssd * 0.45
                        + rng.gauss(0, 2.0),
                    )

                elif label_id == 4:

                    current_rmssd = min(
                        subject_resting_rmssd,
                        subject_resting_rmssd * 0.7
                        + (t / 180.0) * 15.0,
                    )

                else:

                    current_rmssd = (
                        subject_resting_rmssd
                        + rng.gauss(0, 3.0)
                    )

                samples.append(
                    PhysiologicalSample(
                        timestamp=current_time,
                        heart_rate_bpm=current_hr,
                    )
                )

                labels.append(label_id)
                rmssd_list.append(current_rmssd)

                current_time += 1.0

        return WESADSubjectData(
            subject_id=subject_id,
            samples=samples,
            labels=labels,
            hrv_rmssd=rmssd_list,
        )
