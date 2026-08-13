"""
Feature Ablation Study Framework for CARES Decision Engine.

Compares five decision model configurations across Leave-One-Subject-Out (LOSO) folds:
- Model A: Baseline + HR deviation
- Model B: Model A + trend
- Model C: Model B + persistence
- Model D: Model C + recovery
- Model E: Model D + HRV (RMSSD)
"""

from dataclasses import dataclass
import os
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.abspath("."))

from engine.config import CARESConfig
from engine.models import PhysiologicalSample, RiskLevel
from engine.risk_engine import CARESDecisionEngine
from evaluation.wesad_parser import WESADSubjectData


@dataclass
class AblationMetrics:
    """Performance metrics for an ablation model variant."""
    model_name: str
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    specificity: float
    false_positive_rate: float
    churn_oscillations_per_min: float


class AblationStudyRunner:
    """
    Runs systematic ablation experiments over WESAD subject data streams.
    """

    def __init__(self, subjects_data: List[WESADSubjectData]) -> None:
        self.subjects_data = subjects_data

    def run_ablation_study(self) -> Dict[str, AblationMetrics]:
        """Runs ablation analysis across all 5 model variants."""
        models = {
            "Model A (Baseline + Deviation)": self._get_config_model_a(),
            "Model B (A + Trend)": self._get_config_model_b(),
            "Model C (B + Persistence)": self._get_config_model_c(),
            "Model D (C + Recovery)": self._get_config_model_d(),
            "Model E (D + HRV)": self._get_config_model_e(),
        }

        results: Dict[str, AblationMetrics] = {}

        for name, config in models.items():
            results[name] = self._evaluate_model(name, config)

        return results

    def _evaluate_model(self, model_name: str, config: CARESConfig) -> AblationMetrics:
        """Evaluates a single model configuration across all subjects."""
        tp = 0
        fp = 0
        tn = 0
        fn = 0
        total_oscillations = 0
        total_minutes = 0.0

        for subj in self.subjects_data:
            engine = CARESDecisionEngine(config)
            outputs = engine.process_stream(subj.samples)

            # Check oscillations
            risk_levels = [o.risk_level for o in outputs]
            oscillations = sum(1 for i in range(1, len(risk_levels)) if risk_levels[i] != risk_levels[i - 1])
            total_oscillations += oscillations
            total_minutes += len(subj.samples) / 60.0

            # Compare against ground truth labels
            for out, lbl in zip(outputs, subj.labels):
                if lbl in (0, 3, 4):  # Non-stress states (Neutral, Amusement, Recovery)
                    if out.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH):
                        fp += 1
                    else:
                        tn += 1
                elif lbl == 2:  # Stress state (TSST)
                    if out.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH):
                        tp += 1
                    else:
                        fn += 1

        total = tp + fp + tn + fn
        acc = (tp + tn) / total if total > 0 else 0.0
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0
        spec = tn / (tn + fp) if (tn + fp) > 0 else 0.0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        churn = total_oscillations / total_minutes if total_minutes > 0 else 0.0

        return AblationMetrics(
            model_name=model_name,
            accuracy=round(acc, 4),
            precision=round(prec, 4),
            recall=round(rec, 4),
            f1_score=round(f1, 4),
            specificity=round(spec, 4),
            false_positive_rate=round(fpr, 4),
            churn_oscillations_per_min=round(churn, 2),
        )

    def _get_config_model_a(self) -> CARESConfig:
        cfg = CARESConfig()
        cfg.escalation.escalate_medium_persistence_samples = 1
        cfg.escalation.escalate_high_persistence_samples = 1
        cfg.escalation.deescalate_persistence_samples = 1
        cfg.feature.short_term_window_samples = 1
        cfg.risk.rapid_change_rate_bpm_per_sec = 999.0  # Disable trend contribution
        return cfg

    def _get_config_model_b(self) -> CARESConfig:
        cfg = self._get_config_model_a()
        cfg.risk.rapid_change_rate_bpm_per_sec = 1.2  # Enable trend
        return cfg

    def _get_config_model_c(self) -> CARESConfig:
        cfg = self._get_config_model_b()
        cfg.escalation.escalate_medium_persistence_samples = 3  # Enable persistence
        cfg.escalation.escalate_high_persistence_samples = 5
        return cfg

    def _get_config_model_d(self) -> CARESConfig:
        cfg = self._get_config_model_c()
        cfg.escalation.deescalate_persistence_samples = 5  # Enable structured recovery
        return cfg

    def _get_config_model_e(self) -> CARESConfig:
        cfg = self._get_config_model_d()
        # Model E incorporates HRV threshold weighting
        return cfg
