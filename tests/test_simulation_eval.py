from simulation.generator import (
    PhysiologicalStreamGenerator,
    ScenarioType,
)
from simulation.replay import StreamReplayEngine

from engine.risk_engine import CARESDecisionEngine
from evaluation.baselines import PersonalBaselineComparator


def test_scenario_generation_and_replay():
    generator = PhysiologicalStreamGenerator(seed=42)

    samples = generator.generate_scenario(
        scenario_type=ScenarioType.RESTING,
        duration_seconds=30,
        calibration_window_seconds=10,
    )

    assert len(samples) == 30

    engine = CARESDecisionEngine()

    replayer = StreamReplayEngine(engine)

    results = replayer.replay_stream(samples)

    assert len(results) == len(samples)


def test_personal_baseline_comparator_execution():
    generator = PhysiologicalStreamGenerator(seed=42)

    samples = generator.generate_scenario(
        scenario_type=ScenarioType.RESTING,
        duration_seconds=30,
        calibration_window_seconds=10,
    )

    comparator = PersonalBaselineComparator(
        calibration_seconds=300,
    )

    results = comparator.process_stream(samples)

    assert len(results) == len(samples)


def test_cares_and_personal_baseline_are_separate():
    """
    CARES and the comparator are intentionally separate models.

    CARES:
        personal baseline
        + temporal features
        + persistence
        + recovery
        + confidence
        + escalation

    Comparator:
        personal baseline
        + deviation thresholds

    There is no population-wide 85/100 BPM comparator.
    """

    generator = PhysiologicalStreamGenerator(seed=42)

    samples = generator.generate_scenario(
        scenario_type=ScenarioType.RESTING,
        duration_seconds=30,
        calibration_window_seconds=10,
    )

    cares = CARESDecisionEngine()

    comparator = PersonalBaselineComparator(
        calibration_seconds=300,
    )

    cares_results = cares.process_stream(samples)

    comparator_results = comparator.process_stream(samples)

    assert len(cares_results) == len(samples)
    assert len(comparator_results) == len(samples)