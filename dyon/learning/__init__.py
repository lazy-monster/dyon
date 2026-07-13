"""
dyon.learning — domain-agnostic Learning-from-Demonstration toolkit.

A reusable suite for teaching any digital twin from expert demonstrations:

* **Imitation** — ``BCTrainer`` (behavioural cloning), ``DAggerTrainer``.
* **Inverse RL** — ``AIRLTrainer`` (recovers a reusable reward), ``GAILTrainer``
  (adversarial imitation), ``MaxEntIRLTrainer`` (classic, dependency-free).
* **Plumbing** — ``FeatureSpec`` (one source of truth for obs vector + space),
  ``Demonstrations``/``DemonstrationSource`` (data), ``LearnedRewardFn`` (reuse a
  recovered reward in ordinary RL), ``SkillTransferPipeline`` (chain stages,
  validate, version, promote) and ``SyncTrigger`` (when to re-sync).

These compose with the existing ``dyon.autonomous`` RL trainer/deployer and
``dyon.ml.TrainingCorpus`` versioning.
"""

from dyon.learning.demonstrations import (
    ArrayDemonstrationSource,
    CorpusDemonstrationSource,
    Demonstrations,
    DemonstrationSource,
)
from dyon.learning.features import (
    Categorical,
    FeatureColumn,
    FeatureSpec,
    Scalar,
)
from dyon.learning.imitation_trainers import BCTrainer, DAggerTrainer
from dyon.learning.irl_trainers import AIRLTrainer, GAILTrainer
from dyon.learning.maxent import MaxEntIRLTrainer
from dyon.learning.pipeline import (
    PipelineResult,
    SkillTransferPipeline,
    SyncTrigger,
    action_match_accuracy,
    mean_return,
    split_demonstrations,
)
from dyon.learning.reward import LearnedRewardFn

__all__ = [
    "AIRLTrainer",
    "ArrayDemonstrationSource",
    "BCTrainer",
    "Categorical",
    "CorpusDemonstrationSource",
    "DAggerTrainer",
    "DemonstrationSource",
    "Demonstrations",
    "FeatureColumn",
    "FeatureSpec",
    "GAILTrainer",
    "LearnedRewardFn",
    "MaxEntIRLTrainer",
    "PipelineResult",
    "Scalar",
    "SkillTransferPipeline",
    "SyncTrigger",
    "action_match_accuracy",
    "mean_return",
    "split_demonstrations",
]
