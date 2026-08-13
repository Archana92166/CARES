from datetime import datetime, timezone

from engine.models import PhysiologicalSample
from engine.personal_baseline import PersonalBaselineManager


def make_sample(
    t: float,
    hr: float,
    valid: bool = True,
    quality: float = 1.0,
):
    return PhysiologicalSample(
        timestamp=t,
        heart_rate_bpm=hr,
        additional_metrics={
            "hr_valid": 1.0 if valid else 0.0,
            "bvp_quality": quality,
        },
    )


def test_requires_five_minutes_for_initial_baseline(tmp_path):

    manager = PersonalBaselineManager(
        daily_log_path=str(tmp_path / "daily.json"),
        adaptation_log_path=str(tmp_path / "adaptation.jsonl"),
    )

    for t in range(299):
        manager.add_sample(
            make_sample(float(t), 70.0),
            elapsed_seconds=float(t),
        )

    assert manager.is_calibrated is False

    manager.add_sample(
        make_sample(299.0, 70.0),
        elapsed_seconds=300.0,
    )

    assert manager.is_calibrated is True
    assert manager.personal_baseline == 70.0


def test_invalid_samples_do_not_teach_baseline(tmp_path):

    manager = PersonalBaselineManager(
        daily_log_path=str(tmp_path / "daily.json"),
        adaptation_log_path=str(tmp_path / "adaptation.jsonl"),
    )

    for t in range(300):
        manager.add_sample(
            make_sample(float(t), 70.0),
            elapsed_seconds=float(t),
        )

    original = manager.personal_baseline

    manager.add_sample(
        make_sample(
            301.0,
            120.0,
            valid=False,
        ),
        elapsed_seconds=301.0,
    )

    assert manager.personal_baseline == original


def test_three_second_adaptation_updates_slowly(tmp_path):

    manager = PersonalBaselineManager(
        daily_log_path=str(tmp_path / "daily.json"),
        adaptation_log_path=str(tmp_path / "adaptation.jsonl"),
    )

    for t in range(300):
        manager.add_sample(
            make_sample(float(t), 70.0),
            elapsed_seconds=float(t),
        )

    old_baseline = manager.personal_baseline

    for t, hr in zip(
        [301.0, 302.0, 303.0],
        [72.0, 72.0, 72.0],
    ):
        log = manager.add_sample(
            make_sample(t, hr),
            elapsed_seconds=t,
            observed_at=datetime(
                2026,
                8,
                12,
                tzinfo=timezone.utc,
            ),
        )

    assert log is not None
    assert log.decision == "UPDATED"
    assert manager.personal_baseline > old_baseline
    assert manager.personal_baseline < 72.0


def test_high_risk_does_not_teach_baseline(tmp_path):

    manager = PersonalBaselineManager(
        daily_log_path=str(tmp_path / "daily.json"),
        adaptation_log_path=str(tmp_path / "adaptation.jsonl"),
    )

    for t in range(300):
        manager.add_sample(
            make_sample(float(t), 70.0),
            elapsed_seconds=float(t),
        )

    old_baseline = manager.personal_baseline

    log = manager.add_sample(
        make_sample(301.0, 110.0),
        elapsed_seconds=301.0,
        risk_level="HIGH",
    )

    assert log is not None
    assert log.decision == "HOLD"
    assert manager.personal_baseline == old_baseline


def test_daily_record_is_created(tmp_path):

    manager = PersonalBaselineManager(
        daily_log_path=str(tmp_path / "daily.json"),
        adaptation_log_path=str(tmp_path / "adaptation.jsonl"),
    )

    for t in range(300):
        manager.add_sample(
            make_sample(float(t), 70.0),
            elapsed_seconds=float(t),
        )

    observed_at = datetime(
        2026,
        8,
        12,
        tzinfo=timezone.utc,
    )

    for t in [301.0, 302.0, 303.0]:
        manager.add_sample(
            make_sample(t, 71.0),
            elapsed_seconds=t,
            observed_at=observed_at,
        )

    record = manager.finalize_day("2026-08-12")

    assert record is not None
    assert record.date == "2026-08-12"
    assert record.trusted_samples > 0
    assert record.mean_bpm > 0
