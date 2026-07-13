"""PolicyDeployer: live RL inference using a trained SB3 policy."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.data.storage.base import TimeSeriesStore
    from dyon.network.transport import MQTTTransport

log = logging.getLogger(__name__)


class PolicyDeployer:
    """
    Loads a trained SB3 policy and applies it in a live twin loop.

    On each step_once() call:
    1. Reads current observations from InfluxDB
    2. Runs the policy to get an action
    3. Publishes the control command via MQTT

    ``policy_path`` is deserialized via SB3's ``.load`` (a pickle-format
    artifact) and must only be given files obtained through
    :meth:`dyon.ml.corpus.TrainingCorpus.download_version`, which verifies the
    manifest checksum before returning the file.
    """

    def __init__(
        self,
        policy_path: str | None,
        config: TwinConfig,
        ts_store: TimeSeriesStore,
        mqtt_transport: MQTTTransport,
        obs_fields: list[str],
        control_field: str,
        ctrl_min: float,
        ctrl_max: float,
        algorithm: str = "SAC",
        policy=None,
    ):
        from stable_baselines3 import A2C, PPO, SAC, TD3

        self._cfg = config
        self._ts = ts_store
        self._mqtt = mqtt_transport
        self._obs_fields = obs_fields
        self._control_field = control_field
        self._ctrl_min = ctrl_min
        self._ctrl_max = ctrl_max

        if policy is not None:
            # An already-loaded policy/algorithm (e.g. a BC or imitation policy,
            # or anything exposing .predict(obs, deterministic=...)).
            self._policy = policy
            log.info("PolicyDeployer using preloaded policy (%s)", algorithm)
        else:
            # Imitation/BC artifacts are saved in a PPO container, so "BC" loads
            # through PPO.
            _algo_map = {"SAC": SAC, "TD3": TD3, "PPO": PPO, "A2C": A2C, "BC": PPO}
            AlgoClass = _algo_map.get(algorithm.upper(), SAC)
            self._policy = AlgoClass.load(policy_path)  # type: ignore[attr-defined]
            log.info("PolicyDeployer loaded '%s' (%s)", policy_path, algorithm)

    async def _get_observation(self) -> np.ndarray:
        # A field with no data falls back to its configured nominal value rather
        # than a hard 0.0 — a 0.0 can sit outside the policy's observation bounds
        # and misrepresents "no reading" as a genuine zero.
        latest = await self._ts.aget_latest_fields(self._obs_fields)
        specs = self._cfg.field_specs
        obs = []
        for f in self._obs_fields:
            v = latest.get(f)
            if v is None:
                spec = specs.get(f)
                v = spec.nominal if spec and spec.nominal is not None else 0.0
            obs.append(v)
        return np.array(obs, dtype=np.float32)

    def _scale_action(self, action: np.ndarray) -> float:
        a = float(np.clip(action[0], -1.0, 1.0))
        return self._ctrl_min + (a + 1.0) / 2.0 * (self._ctrl_max - self._ctrl_min)

    async def step_once(self) -> float:
        obs = await self._get_observation()
        action, _ = self._policy.predict(obs, deterministic=True)
        ctrl = self._scale_action(action)
        self._mqtt.publish(
            self._cfg.topic_control,
            {self._control_field: round(ctrl, 4)},
        )
        return ctrl
