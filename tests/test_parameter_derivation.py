"""
Unit tests for Parameter Derivation Module.
"""

import pytest
from evaluation.parameter_derivation import ParameterDeriver
from evaluation.wesad_parser import WESADParser


def test_parameter_derivation_from_training_subjects():
    parser = WESADParser()
    all_subjs = list(parser.load_all_subjects(seed=42).values())

    # Use first 10 subjects as training set
    train_subjs = all_subjs[:10]
    derived = ParameterDeriver.derive_parameters(train_subjs)

    assert "medium_dev_bpm" in derived
    assert "high_dev_bpm" in derived
    assert "medium_pct_dev" in derived
    assert "high_pct_dev" in derived
    assert "rapid_slope_bpm_per_sec" in derived

    # Numerical sanity bounds
    assert 1.0 <= derived["medium_dev_bpm"] < derived["high_dev_bpm"]
    assert 2.0 <= derived["medium_pct_dev"] < derived["high_pct_dev"]
    assert derived["rapid_slope_bpm_per_sec"] > 0.1
