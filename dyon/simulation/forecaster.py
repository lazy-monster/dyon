"""Prophet-based time-series forecaster."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.data.storage.base import TimeSeriesStore

log = logging.getLogger(__name__)


class ProphetForecaster:
    """
    Fits a Prophet model on recent time-series data and produces forecasts.

    Usage::

        forecaster = ProphetForecaster(ts_store, field="temperature_c")
        forecaster.fit(lookback_hours=48)
        forecast = forecaster.predict(periods=24, freq="h")
    """

    def __init__(self, ts_store: TimeSeriesStore, field: str):
        self._ts = ts_store
        self._field = field
        self._model = None

    def fit(self, lookback_hours: int = 48) -> None:
        import pandas as pd

        from dyon._compat import require
        require("prophet", "forecast")
        from prophet import Prophet

        df = self._ts.query_recent(self._field, minutes=lookback_hours * 60)
        if df is None or len(df) < 10:
            log.warning("Insufficient data to fit Prophet for '%s'", self._field)
            return

        prophet_df = df[["_time", "_value"]].rename(
            columns={"_time": "ds", "_value": "y"}
        )
        prophet_df["ds"] = pd.to_datetime(prophet_df["ds"], utc=True).dt.tz_localize(None)

        model = Prophet(daily_seasonality=True, yearly_seasonality=False)
        model.fit(prophet_df)
        self._model = model
        log.info("Prophet model fitted for field '%s'", self._field)

    def predict(self, periods: int = 24, freq: str = "h") -> list[dict]:
        if self._model is None:
            log.warning("Prophet model not fitted for '%s'", self._field)
            return []
        future = self._model.make_future_dataframe(periods=periods, freq=freq)
        forecast = self._model.predict(future)
        return forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(periods).to_dict("records")
