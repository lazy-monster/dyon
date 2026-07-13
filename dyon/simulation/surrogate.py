"""Surrogate model implementations: ONNX and scikit-learn."""

from __future__ import annotations

import logging

import numpy as np

from dyon.core.types import ModelType

log = logging.getLogger(__name__)


class ONNXSurrogate:
    """ONNX-exported surrogate model for fast inference."""

    model_name: str = "onnx_surrogate"
    model_type: str = ModelType.SURROGATE

    def __init__(
        self,
        model_path: str,
        input_fields: list[str],
        output_fields: list[str],
        input_name: str = "X",
    ):
        import onnxruntime as ort

        self.session = ort.InferenceSession(model_path)
        self.input_fields = input_fields
        self.output_fields = output_fields
        self.input_name = input_name

    def step(self, dt: float, inputs: dict[str, float]) -> dict[str, float]:
        x = np.array(
            [[inputs.get(f, 0.0) for f in self.input_fields]], dtype=np.float32
        )
        outputs = self.session.run(None, {self.input_name: x})
        return {
            f"sim_{name}": float(outputs[0][0][i])
            for i, name in enumerate(self.output_fields)
        }

    def reset(self) -> None:
        pass  # stateless


class SKLearnSurrogate:
    """scikit-learn pipeline surrogate model."""

    model_name: str = "sklearn_surrogate"
    model_type: str = ModelType.SURROGATE

    def __init__(
        self,
        model,          # fitted sklearn estimator
        input_fields: list[str],
        output_fields: list[str],
    ):
        self._model = model
        self.input_fields = input_fields
        self.output_fields = output_fields

    def step(self, dt: float, inputs: dict[str, float]) -> dict[str, float]:
        x = np.array([[inputs.get(f, 0.0) for f in self.input_fields]])
        try:
            pred = self._model.predict(x)
            if pred.ndim == 1:
                pred = pred.reshape(1, -1)
            return {
                f"sim_{name}": round(float(pred[0][i]), 4)
                for i, name in enumerate(self.output_fields)
            }
        except Exception as e:
            log.error("SKLearn surrogate step failed: %s", e)
            return {}

    def reset(self) -> None:
        pass  # stateless
