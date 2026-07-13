"""InfluxDB 2 time-series adapter."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from dyon.core import metrics

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)

# Field/measurement names are interpolated into Flux strings (parameters aren't
# supported for identifiers), so restrict them to a safe character set. This
# turns a stray quote in a name from a broken/forgeable query into a clear error.
_VALID_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def _safe_identifier(name: str) -> str:
    if not _VALID_IDENTIFIER.fullmatch(name):
        raise ValueError(
            f"Invalid Flux identifier {name!r}: only A-Za-z0-9_ are allowed."
        )
    return name


class InfluxAdapter:
    """InfluxDB 2 adapter for time-series sensor data."""

    def __init__(self, config: TwinConfig):
        from dyon._compat import require
        require("influxdb_client", "stores")
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        self._cfg = config.influx
        self._asset_id = config.asset_id
        self._client = InfluxDBClient(
            url=self._cfg.url,
            token=self._cfg.token,
            org=self._cfg.org,
        )
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
        self._query_api = self._client.query_api()

    def write_point(
        self,
        measurement: str,
        fields: dict[str, float],
        tags: dict[str, str] | None = None,
        timestamp: float | datetime | None = None,
    ) -> None:
        from influxdb_client import Point
        from influxdb_client.domain.write_precision import WritePrecision

        p = Point(measurement).tag("asset_id", self._asset_id)
        if tags:
            for k, v in tags.items():
                p = p.tag(k, v)
        for fk, fv in fields.items():
            p = p.field(fk, float(fv))
        # Prefer a source timestamp when the caller has one (network/queue latency
        # otherwise skews the series toward write time); fall back to now.
        if timestamp is None:
            ts = datetime.now(UTC)
        elif isinstance(timestamp, datetime):
            ts = timestamp
        else:
            ts = datetime.fromtimestamp(timestamp, UTC)
        p = p.time(ts, WritePrecision.S)
        try:
            self._write_api.write(
                bucket=self._cfg.bucket, org=self._cfg.org, record=p
            )
        except Exception as e:
            metrics.increment("storage.influx.write_errors")
            log.warning("Influx write failed (data dropped): %s", e)

    def query_recent(
        self,
        field: str,
        minutes: int = 10,
        measurement: str = "asset_telemetry",
    ):
        import pandas as pd

        _safe_identifier(field)
        _safe_identifier(measurement)
        query = f"""
        from(bucket: "{self._cfg.bucket}")
          |> range(start: -{minutes}m)
          |> filter(fn: (r) => r._measurement == "{measurement}")
          |> filter(fn: (r) => r.asset_id == "{self._asset_id}")
          |> filter(fn: (r) => r._field == "{field}")
          |> sort(columns: ["_time"])
        """
        try:
            result = self._query_api.query_data_frame(query, org=self._cfg.org)
            # A multi-table result comes back as a list of DataFrames; collapse it
            # so callers always get a single DataFrame (or an empty one).
            if isinstance(result, list):
                result = pd.concat(result, ignore_index=True) if result else pd.DataFrame()
            return result
        except Exception as e:
            log.error("InfluxDB query error: %s", e)
            return pd.DataFrame()

    def get_latest(
        self, field: str, measurement: str = "asset_telemetry"
    ) -> float | None:
        _safe_identifier(field)
        _safe_identifier(measurement)
        query = f"""
        from(bucket: "{self._cfg.bucket}")
          |> range(start: -1h)
          |> filter(fn: (r) => r._measurement == "{measurement}")
          |> filter(fn: (r) => r.asset_id == "{self._asset_id}")
          |> filter(fn: (r) => r._field == "{field}")
          |> last()
        """
        try:
            tables = self._query_api.query(query, org=self._cfg.org)
            for table in tables:
                for record in table.records:
                    return float(record.get_value())
        except Exception as e:
            log.debug("InfluxDB get_latest error for '%s': %s", field, e)
        return None

    def query_recent_fields(
        self,
        fields: list[str],
        minutes: int = 60,
        measurement: str = "asset_telemetry",
    ) -> dict[str, list[dict]]:
        """Query multiple fields over a time window in a single Flux request.

        Returns a dict mapping each field name to a list of
        ``{"ts": float_unix_seconds, "value": float}`` dicts, oldest first.
        Fields with no data will map to an empty list.
        """
        if not fields:
            return {}
        _safe_identifier(measurement)
        fields_filter = " or ".join(
            f'r._field == "{_safe_identifier(f)}"' for f in fields
        )
        query = f"""
        from(bucket: "{self._cfg.bucket}")
          |> range(start: -{minutes}m)
          |> filter(fn: (r) => r._measurement == "{measurement}")
          |> filter(fn: (r) => r.asset_id == "{self._asset_id}")
          |> filter(fn: (r) => {fields_filter})
          |> sort(columns: ["_time"])
        """
        result: dict[str, list[dict]] = {f: [] for f in fields}
        try:
            tables = self._query_api.query(query, org=self._cfg.org)
            for table in tables:
                for record in table.records:
                    field = record.get_field()
                    if field in result:
                        result[field].append({
                            "ts":    record.get_time().timestamp(),
                            "value": float(record.get_value()),
                        })
        except Exception as e:
            log.error("InfluxDB query_recent_fields error: %s", e)
        return result

    def get_latest_fields(
        self,
        fields: list[str],
        measurement: str = "asset_telemetry",
    ) -> dict[str, float | None]:
        """Latest value per field in a SINGLE query.

        Collapses N separate ``get_latest`` round trips into one Flux request —
        the high-frequency loops (rule engine, model runner) call this instead of
        looping ``get_latest`` per field.
        """
        rows = self.query_recent_fields(fields, minutes=60, measurement=measurement)
        return {f: (data[-1]["value"] if data else None) for f, data in rows.items()}

    # --- async wrappers -------------------------------------------------------
    # The underlying influxdb_client is synchronous and every call is a blocking
    # HTTP round trip. These coroutines offload the blocking call to a worker
    # thread so the asyncio event loop is never frozen mid-request. The hot layer
    # loops await these; the (already thread-offloaded) LangChain tools keep using
    # the sync methods above.

    async def awrite_point(
        self,
        measurement: str,
        fields: dict[str, float],
        tags: dict[str, str] | None = None,
        timestamp: float | datetime | None = None,
    ) -> None:
        await asyncio.to_thread(self.write_point, measurement, fields, tags, timestamp)

    async def aget_latest(
        self, field: str, measurement: str = "asset_telemetry"
    ) -> float | None:
        return await asyncio.to_thread(self.get_latest, field, measurement)

    async def aquery_recent(
        self, field: str, minutes: int = 10, measurement: str = "asset_telemetry"
    ):
        return await asyncio.to_thread(self.query_recent, field, minutes, measurement)

    async def aquery_recent_fields(
        self,
        fields: list[str],
        minutes: int = 60,
        measurement: str = "asset_telemetry",
    ) -> dict[str, list[dict]]:
        return await asyncio.to_thread(
            self.query_recent_fields, fields, minutes, measurement
        )

    async def aget_latest_fields(
        self, fields: list[str], measurement: str = "asset_telemetry"
    ) -> dict[str, float | None]:
        return await asyncio.to_thread(self.get_latest_fields, fields, measurement)

    def close(self) -> None:
        self._client.close()
