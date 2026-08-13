from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def test_responsive_cares_frontend_assets_exist():
    assert (FRONTEND / "index.html").is_file()
    assert (FRONTEND / "styles.css").is_file()
    assert (FRONTEND / "app.js").is_file()

    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for page in ("dashboard", "live", "baseline", "history", "incidents", "guardian", "location", "settings"):
        assert f'data-page-view="{page}"' in html
    assert 'name="viewport"' in html


def test_frontend_consumes_engine_contract_without_threshold_logic():
    javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
    for field in (
        "risk_level",
        "risk_score",
        "confidence",
        "baseline",
        "deviation",
        "reason_codes",
        "recommended_actions",
        "/api/events/stream",
    ):
        assert field in javascript

    forbidden_patterns = (
        "heartRate >",
        "heart_rate >",
        "bpm >",
        "riskScore >",
        "if (hr >",
    )
    assert not any(pattern in javascript for pattern in forbidden_patterns)


def test_frontend_has_explicit_unavailable_states_and_action_statuses():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "Location unavailable" in html
    assert "No notification was sent" in javascript
    for status in ("GENERATED", "PENDING", "SENT", "DELIVERED", "FAILED", "UNAVAILABLE"):
        assert status in html or status in javascript
