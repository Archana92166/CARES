"""Isolated demonstration input adapters for the CARES application.

The adapter produces only ``PhysiologicalSample`` objects. It never imports
``RiskLevel`` and never interprets WESAD labels. Every sample is processed by
the normal ``CARESDecisionEngine`` path in ``CARESBackend``.
"""

from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional

from engine.models import PhysiologicalSample
from evaluation.wesad_parser import WESADParser

from .services import BackendError, CARESBackend, ConflictError, NotFoundError, ValidationError


class DemoInputUnavailable(BackendError):
    """Raised when an explicitly requested demonstration source is absent."""

    status_code = 503


class DemoScenario(str, Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    SUSTAINED_HIGH = "SUSTAINED_HIGH"
    RECOVERY = "RECOVERY"


@dataclass(frozen=True)
class DemoStream:
    scenario: DemoScenario
    source: str
    samples: tuple[PhysiologicalSample, ...]
    input_description: str

    def __iter__(self) -> Iterator[PhysiologicalSample]:
        return iter(self.samples)


class DemoSampleAdapter:
    """Build a finite, hardware-shaped stream without making decisions."""

    def __init__(
        self,
        wesad_dir: str = "data/wesad",
        seed: int = 42,
        sample_limit: int = 360,
    ) -> None:
        self.wesad_dir = Path(wesad_dir)
        self.seed = seed
        self.sample_limit = max(301, int(sample_limit))

    def create(self, scenario: str) -> DemoStream:
        try:
            selected = DemoScenario(str(scenario).upper())
        except ValueError as exc:
            raise ValidationError(
                "Scenario must be NORMAL, ELEVATED, SUSTAINED_HIGH, or RECOVERY."
            ) from exc

        if selected is DemoScenario.NORMAL:
            return self._official_wesad_stream(selected)
        return self._synthetic_stream(selected)

    def _official_wesad_stream(self, scenario: DemoScenario) -> DemoStream:
        pickle_path = self.wesad_dir / "S2" / "S2.pkl"
        if not pickle_path.is_file():
            raise DemoInputUnavailable(
                "official WESAD wrist data is unavailable at data/wesad/S2/S2.pkl. "
                "NORMAL WESAD demonstration cannot start."
            )
        subject = WESADParser(data_dir=str(self.wesad_dir)).load_subject("S2")
        samples = tuple(subject.samples[: self.sample_limit])
        if len(samples) < 1:
            raise DemoInputUnavailable("The official WESAD stream contains no usable HR samples.")
        return DemoStream(
            scenario=scenario,
            source="DEMO_WESAD",
            samples=samples,
            input_description="Official WESAD wrist-BVP-derived HR; labels are not used for decisions.",
        )

    def _synthetic_stream(self, scenario: DemoScenario) -> DemoStream:
        """Controlled synthetic physiology, explicitly labelled as synthetic."""
        rng = random.Random(self.seed + list(DemoScenario).index(scenario))
        total = max(self.sample_limit, 380 if scenario is not DemoScenario.RECOVERY else 430)
        samples: list[PhysiologicalSample] = []
        for index in range(total):
            if index < 300:
                target = 70.0
            elif scenario is DemoScenario.ELEVATED:
                target = 84.0
            elif scenario is DemoScenario.SUSTAINED_HIGH:
                target = 110.0
            elif scenario is DemoScenario.RECOVERY:
                if index < 330:
                    target = 110.0
                else:
                    progress = min(1.0, (index - 330) / 100.0)
                    target = 110.0 - 40.0 * progress
            else:
                target = 70.0
            heart_rate = max(35.0, min(210.0, target + rng.gauss(0.0, 0.35)))
            samples.append(
                PhysiologicalSample(
                    timestamp=float(index),
                    heart_rate_bpm=heart_rate,
                    additional_metrics={"hr_valid": 1.0, "bvp_quality": 1.0},
                )
            )
        return DemoStream(
            scenario=scenario,
            source="DEMO_SYNTHETIC",
            samples=tuple(samples),
            input_description="Controlled synthetic physiological demonstration; not hardware data.",
        )


@dataclass
class _DemoRun:
    user_id: int
    session_id: str
    stream: DemoStream
    stop_event: threading.Event
    thread: threading.Thread
    sample_count: int = 0
    last_error: Optional[str] = None


class DemoController:
    """Owns at most one stoppable demo thread per authenticated user."""

    def __init__(
        self,
        backend: CARESBackend,
        adapter: Optional[DemoSampleAdapter] = None,
        interval_seconds: Optional[float] = None,
    ) -> None:
        self.backend = backend
        self.adapter = adapter or DemoSampleAdapter()
        configured = interval_seconds
        if configured is None:
            configured = float(os.getenv("CARES_DEMO_INTERVAL_SECONDS", "0.02"))
        self.interval_seconds = max(0.001, float(configured))
        self._runs: dict[int, _DemoRun] = {}
        self._last_status: dict[int, dict[str, object]] = {}
        self._lock = threading.RLock()

    def start(self, user_id: int, scenario: str) -> dict[str, object]:
        with self._lock:
            if user_id in self._runs:
                self._stop_locked(user_id)
            stream = self.adapter.create(scenario)
            session = self.backend.start_monitoring_session(
                user_id,
                source=stream.source,
                scenario=stream.scenario.value,
                metadata={
                    "input_description": stream.input_description,
                    "temporary_demo": True,
                },
            )
            stop_event = threading.Event()
            run = _DemoRun(
                user_id=user_id,
                session_id=str(session["id"]),
                stream=stream,
                stop_event=stop_event,
                thread=threading.Thread(
                    target=self._run,
                    args=(user_id, str(session["id"])),
                    name=f"cares-demo-{user_id}",
                    daemon=True,
                ),
            )
            self._runs[user_id] = run
            run.thread.start()
            status = self._status_from_run(run, session)
            self._last_status[user_id] = status
            self.backend.events.publish(user_id, {"type": "demo_status", "data": status})
            return status

    def stop(self, user_id: int) -> dict[str, object]:
        with self._lock:
            if user_id not in self._runs:
                sessions = self.backend.list_monitoring_sessions(user_id, limit=20)
                latest = next((item for item in sessions if str(item["source"]).startswith("DEMO_")), None)
                if latest is None:
                    raise NotFoundError("No active demo monitoring session.")
                return self._last_status.get(user_id, {"active": False, "session": latest, "sample_count": 0})
            return self._stop_locked(user_id)

    def status(self, user_id: int) -> dict[str, object]:
        with self._lock:
            run = self._runs.get(user_id)
            if run is None:
                sessions = self.backend.list_monitoring_sessions(user_id, limit=20)
                latest = next((item for item in sessions if str(item["source"]).startswith("DEMO_")), None)
                return self._last_status.get(user_id, {"active": False, "session": latest, "sample_count": 0})
            session = self.backend.get_monitoring_session(user_id, run.session_id)
            return self._status_from_run(run, session)

    def _stop_locked(self, user_id: int) -> dict[str, object]:
        run = self._runs.get(user_id)
        if run is None:
            raise NotFoundError("No active demo monitoring session.")
        run.stop_event.set()
        if run.thread is not threading.current_thread():
            run.thread.join(timeout=max(2.0, self.interval_seconds * 20))
        if run.thread.is_alive():
            raise ConflictError("Demo stream did not stop cleanly; try again shortly.")
        session = self.backend.get_monitoring_session(user_id, run.session_id)
        self._runs.pop(user_id, None)
        status = self._status_from_run(run, session)
        self._last_status[user_id] = status
        return status

    def _run(self, user_id: int, session_id: str) -> None:
        with self._lock:
            run = self._runs.get(user_id)
        if run is None:
            return
        try:
            for sample in run.stream:
                if run.stop_event.wait(self.interval_seconds):
                    break
                self.backend.process_sample(
                    user_id,
                    sample,
                    session_id=session_id,
                    source=run.stream.source,
                )
                run.sample_count += 1
        except Exception as exc:  # surfaced through status, never silently swallowed
            run.last_error = str(exc)
        finally:
            try:
                self.backend.stop_monitoring_session(user_id, session_id)
            except Exception as exc:
                run.last_error = run.last_error or str(exc)
            status = self._status_from_run(run, self.backend.get_monitoring_session(user_id, session_id))
            self._last_status[user_id] = status
            self.backend.events.publish(user_id, {"type": "demo_status", "data": status})
            # Do not acquire the controller lock here: stop() may be joining
            # this thread while holding it. Dict removal is atomic in CPython
            # and the public status path remains guarded.
            self._runs.pop(user_id, None)

    @staticmethod
    def _status_from_run(run: _DemoRun, session: dict[str, object]) -> dict[str, object]:
        return {
            "active": session.get("status") == "ACTIVE" and run.thread.is_alive(),
            "session": session,
            "sample_count": run.sample_count,
            "scenario": run.stream.scenario.value,
            "source": run.stream.source,
            "input_description": run.stream.input_description,
            "error": run.last_error,
        }
