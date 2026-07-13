"""Shared type aliases and enumerations used across the framework."""

from enum import Enum
from typing import Any


class OperationalState(str, Enum):
    RUNNING = "running"
    WARNING = "warning"
    SHUTDOWN = "shutdown"
    UNKNOWN = "unknown"
    INITIALISING = "initialising"


class LayerName(str, Enum):
    DATA = "data"
    DATA_MANAGEMENT = "data_management"
    SIMULATION = "simulation"
    SERVICES = "services"
    SERVICE_DITTO = "service_ditto"
    REACTIVE = "reactive"
    REACTIVE_PID = "reactive_pid"
    INTELLIGENT = "intelligent"
    AUTONOMOUS = "autonomous"
    NETWORK = "network"


class ModelType(str, Enum):
    PHYSICS = "physics"
    SURROGATE = "surrogate"
    ML = "ml"
    STATISTICAL = "statistical"
    DISCRETE_EVENT = "discrete_event"


class ConnectorType(str, Enum):
    MQTT = "mqtt"
    DITTO = "ditto"
    API = "api"
    GRPC = "grpc"


class CollectionType(str, Enum):
    AGGREGATE = "aggregate"
    COLLECTION = "collection"
    COMPOSITE = "composite"
    NETWORK = "network"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


Telemetry = dict[str, float | None]
Tags = dict[str, str]
JsonDict = dict[str, Any]
