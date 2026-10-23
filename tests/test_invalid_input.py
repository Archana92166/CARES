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


def test_non_finite_sample_values_raise_value_error():
    import math

    with pytest.raises(ValueError, match="Invalid timestamp"):
        PhysiologicalSample(timestamp=math.nan, heart_rate_bpm=70.0)
    with pytest.raises(ValueError, match="physiological plausible bounds"):
        PhysiologicalSample(timestamp=1.0, heart_rate_bpm=math.inf)
    with pytest.raises(ValueError, match="bvp_quality"):
        PhysiologicalSample(timestamp=1.0, heart_rate_bpm=70.0, additional_metrics={"bvp_quality": math.nan})


def test_hr_valid_is_binary_and_legacy_default_is_valid():
    import pytest

    assert PhysiologicalSample(1.0, 70.0).hr_valid is True
    assert PhysiologicalSample(1.0, 70.0, {"hr_valid": True}).hr_valid is True
    assert PhysiologicalSample(1.0, 70.0, {"hr_valid": False}).hr_valid is False
    assert PhysiologicalSample(1.0, 70.0, {"hr_valid": 1}).hr_valid is True
    assert PhysiologicalSample(1.0, 70.0, {"hr_valid": 1.0}).hr_valid is True

    for value in (0.5, 2, "1"):
        with pytest.raises(ValueError, match="hr_valid"):
            PhysiologicalSample(1.0, 70.0, {"hr_valid": value})


def test_is_trusted_rejects_mutated_invalid_metadata():
    sample = PhysiologicalSample(1.0, 70.0)
    assert sample.is_trusted is True

    sample.additional_metrics["hr_valid"] = 2
    assert sample.is_trusted is False

    sample.additional_metrics["hr_valid"] = 1.0
    sample.additional_metrics["bvp_quality"] = 2.0
    assert sample.is_trusted is False
