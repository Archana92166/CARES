"""
Unit tests for Invalid Input Handling and Bounds Verification.
"""

import pytest
from engine.models import PhysiologicalSample


def test_invalid_timestamp_raises_value_error():
    with pytest.raises(ValueError, match="Invalid timestamp"):
        PhysiologicalSample(timestamp=-5.0, heart_rate_bpm=70.0)

    with pytest.raises(ValueError, match="Invalid timestamp"):
        PhysiologicalSample(timestamp="invalid", heart_rate_bpm=70.0)  # type: ignore


def test_out_of_bounds_heart_rate_raises_value_error():
    # Below plausible bounds (< 30 bpm)
    with pytest.raises(ValueError, match="physiological plausible bounds"):
        PhysiologicalSample(timestamp=1.0, heart_rate_bpm=20.0)

    # Above plausible bounds (> 220 bpm)
    with pytest.raises(ValueError, match="physiological plausible bounds"):
        PhysiologicalSample(timestamp=1.0, heart_rate_bpm=250.0)


def test_non_numeric_heart_rate_raises_value_error():
    with pytest.raises(ValueError, match="Must be numeric"):
        PhysiologicalSample(timestamp=1.0, heart_rate_bpm="seventy")  # type: ignore
