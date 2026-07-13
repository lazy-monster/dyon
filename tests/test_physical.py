"""GenericSimulator and SerialPublisher construct and behave with a default config.

No MQTT broker or serial hardware is touched — only the pure step()/parse logic.
"""

from __future__ import annotations

from dyon.core.config import SensorFieldSpec, TwinConfig
from dyon.physical.simulator import GenericSimulator, SerialPublisher


def _config():
    return TwinConfig(sensor_fields=[
        SensorFieldSpec(name="temp", nominal=25.0, noise_std=0.0),
        SensorFieldSpec(name="derived", nominal=None),   # computed field, not simulated
    ])


def test_simulator_step_emits_a_reading_per_nominal_field():
    sim = GenericSimulator(_config())
    readings = sim.step()
    assert readings["temp"] == 25.0          # noise_std=0 -> exact nominal
    assert "derived" not in readings         # None-nominal field is skipped


def test_simulator_fault_injection_overrides_and_flags():
    sim = GenericSimulator(_config())
    sim.inject_fault({"temp": 999.0})
    readings = sim.step()
    assert readings["temp"] == 999.0
    assert readings["fault_injected"] == 1.0
    sim.clear_fault()
    assert "fault_injected" not in sim.step()


def test_serial_publisher_uses_parser_and_defaults_empty():
    pub = SerialPublisher(_config(), port="/dev/null", parser=lambda line: {"temp": float(line)})
    # default parser path: no parser -> empty dict
    default_pub = SerialPublisher(_config(), port="/dev/null")
    assert default_pub._parser("anything") == {}
    assert pub._parser("42.5") == {"temp": 42.5}
