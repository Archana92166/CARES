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
        "/api/hardware/status",
        "REAL_HARDWARE",
        "/api/demo/start",
        "/api/demo/stop",
        "/api/demo/status",
        "DEMO_SYNTHETIC",
        "session_id",
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


def test_frontend_exposes_both_monitoring_modes_and_demo_controls():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
    for mode in ("Live Hardware Mode", "Presentation Demo Mode", 'id="mode-selector"', 'id="mode-indicator"'):
        assert mode in html
    for control in ("hardware-live-panel", "hardware-heart-rate", "hardware-spo2", "hardware-location", "hardware-reading-time", "hardware-signal-quality", "hardware-sampling-health"):
        assert control in html
    assert "REAL HARDWARE" in javascript
    for control in ("demo-control-panel", "demo-scenario", "demo-start", "demo-stop", "demo-status", "demo-guardian-status"):
        assert control in html
    for route in ("/api/demo/start", "/api/demo/stop", "/api/demo/status"):
        assert route in javascript
    for scenario in ("NORMAL", "ELEVATED", "SUSTAINED_HIGH", "RECOVERY"):
        assert scenario in html
    assert "WESAD" not in html
    assert "WESAD" not in javascript


def test_frontend_keeps_mode_source_separation_and_real_hardware_empty_states():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "Hardware location unavailable in demonstration mode." in javascript
    assert "PERSONAL BASELINE — REAL HARDWARE" in javascript
    assert "DEMO BASELINE · SIMULATED" in javascript
    assert 'if (event.data.source !== selectedSource()) return;' in javascript
    assert "function selectedLocation()" in javascript
    assert "if (state.selectedMode === \"DEMO\") return null;" in javascript
    assert "Waiting for a trusted hardware engine event" in html
    assert "demo routes are disabled by server configuration" in javascript
    assert "Realtime unavailable — retrying" in javascript
    assert "secondaryRefreshPromise" in javascript
    assert "secondaryRefreshQueued" in javascript


def test_frontend_exposes_real_hardware_and_data_controls():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
    for route in ("/api/hardware/status", "/api/system/status", "/api/data/export", "/api/monitoring/sessions/"):
        assert route in javascript
    for control in ("settings-hardware", "settings-delivery", "export-data-button", "delete-session-button"):
        assert control in html
    assert "Provider-side messages cannot be recalled" in javascript


def test_frontend_exposes_guardian_preferences_and_safe_test_workflow():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
    for control in ("guardian-sms-enabled", "guardian-voice-enabled", "guardian-test-recipient", "guardian-test-channel", "guardian-test-button"):
        assert control in html
    assert "/api/guardian/test" in javascript
    assert "no emergency" in javascript.lower()
