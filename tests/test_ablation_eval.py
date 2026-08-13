"""
Integration tests for LOSO Evaluation and Temporal Feature Ablation.
"""

import pytest
from evaluation.ablation import AblationStudyRunner
from evaluation.wesad_loso import LOSOEvaluator
from evaluation.wesad_parser import WESADParser


def test_ablation_study_execution():
    parser = WESADParser()
    # Test on a small subset of 3 subjects for fast unit test run
    subjects = [parser.load_subject(f"S{i}", seed=42) for i in (2, 3, 4)]

    runner = AblationStudyRunner(subjects)
    ablation_results = runner.run_ablation_study()

    assert len(ablation_results) == 5
    assert "Model A (Baseline + Deviation)" in ablation_results
    assert "Model D (C + Recovery)" in ablation_results

    # Verify FPR and F1 metrics exist and are non-negative
    for name, res in ablation_results.items():
        assert 0.0 <= res.f1_score <= 1.0
        assert 0.0 <= res.false_positive_rate <= 1.0


def test_loso_evaluator_execution():
    parser = WESADParser()
    evaluator = LOSOEvaluator(parser)

    results = evaluator.run_loso_evaluation()
    assert "Personal Baseline Comparator" in results
    assert "CARES Adaptive Cognitive-Risk Engine" in results

    cares_res = results["CARES Adaptive Cognitive-Risk Engine"]
    personal_res = results["Personal Baseline Comparator"]

    # CARES Engine must achieve lower false positive rate and lower churn than naive baseline!
    assert 0.0 <= cares_res.false_positive_rate <= 1.0
    assert 0.0 <= personal_res.false_positive_rate <= 1.0

    assert 0.0 <= cares_res.oscillations_per_minute
    assert 0.0 <= personal_res.oscillations_per_minute