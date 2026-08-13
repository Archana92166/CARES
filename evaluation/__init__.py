"""
CARES Evaluation Package.

WESAD is used only for offline evaluation.

The primary comparison is:

    Personal Baseline Comparator
            versus
    CARES Adaptive Cognitive-Risk Engine
"""

from .baselines import PersonalBaselineComparator
from .ablation import AblationStudyRunner
from .wesad_loso import (
    LOSOEvaluator,
    ModelEvaluationResult,
)
from .wesad_parser import (
    WESADParser,
    WESADSubjectData,
)

__all__ = [
    "PersonalBaselineComparator",
    "AblationStudyRunner",
    "LOSOEvaluator",
    "ModelEvaluationResult",
    "WESADParser",
    "WESADSubjectData",
]