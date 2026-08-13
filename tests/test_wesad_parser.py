"""
Unit tests for WESAD Dataset Parser and Protocol Loader.
"""

import pytest
from evaluation.wesad_parser import WESADParser, WESADSubjectData


def test_wesad_valid_subject_ids():
    parser = WESADParser()
    assert len(parser.VALID_SUBJECT_IDS) == 15
    assert "S2" in parser.VALID_SUBJECT_IDS
    assert "S17" in parser.VALID_SUBJECT_IDS
    assert "S12" not in parser.VALID_SUBJECT_IDS  # Excluded in WESAD paper protocol


def test_wesad_subject_stream_generation():
    parser = WESADParser()
    subj = parser.load_subject("S2", seed=42)

    assert isinstance(subj, WESADSubjectData)
    assert subj.subject_id == "S2"
    assert len(subj.samples) == len(subj.labels)
    assert len(subj.samples) > 500

    # Verify labels contain Neutral (1), Stress (2), Amusement (3), Recovery (4)
    unique_labels = set(subj.labels)
    assert 1 in unique_labels
    assert 2 in unique_labels
    assert 3 in unique_labels
    assert 4 in unique_labels


def test_wesad_all_subjects_loading():
    parser = WESADParser()
    all_subjs = parser.load_all_subjects(seed=42)
    assert len(all_subjs) == 15
    for sid in parser.VALID_SUBJECT_IDS:
        assert sid in all_subjs
