"""Text ingestor — ingests free-text events, scores sentiment, writes signals to storage.

Domain-agnostic: works for any asset that ingests textual data — maintenance logs,
operator notes, inspection reports, clinical observations, support tickets, etc.

The ingestor derives a small set of numeric signals from each text event and writes
them to InfluxDB so that the rest of the framework can treat them as regular sensor
readings. Raw text is written to MongoDB for full-text search and audit.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from dyon.core.base import LayerBase

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import DocumentStore, TimeSeriesStore

log = logging.getLogger(__name__)

# Lazily-built singleton — VADER's SentimentIntensityAnalyzer reads its
# lexicon file at construction time, which is expensive to repeat per call.
_VADER_SIA = None


def vader_sentiment(text: str) -> float:
    """Return 0.0–1.0 sentiment score using VADER (vaderSentiment library).

    Public helper: implementations may import this directly to score text the
    same way the built-in TextIngestor does.
    """
    global _VADER_SIA
    try:
        if _VADER_SIA is None:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _VADER_SIA = SentimentIntensityAnalyzer()
        compound = _VADER_SIA.polarity_scores(text)["compound"]  # -1.0 to 1.0
        return (compound + 1.0) / 2.0  # normalise to 0–1
    except ImportError:
        return 0.5  # neutral fallback if VADER not installed


# Backwards-compatible alias for the pre-0.6 private name.
_vader_sentiment = vader_sentiment


class TextIngestor(LayerBase):
    """
    Ingests free-text events, derives numeric signals, and writes to storage.

    Derived signals written to InfluxDB:
        sentiment_score   — 0.0 (very negative) to 1.0 (very positive)
        text_length       — character count
        is_question       — 1.0 if the text ends with "?", else 0.0

    The event_type and source_label parameters are domain-defined strings
    (e.g. "customer_message", "operator_note", "maintenance_log").

    Usage::

        ingestor = TextIngestor(config, bus, ts_store=ts, doc_store=doc)

        # Synchronous path (awaited inline):
        signals = await ingestor.ingest(
            session_id="sess_001",
            source_label="customer",
            content="I'm not sure about the price",
        )

        # Async background path:
        await ingestor.ingest_async(session_id, "customer", content)
        await ingestor.run()  # background worker
    """

    layer_name = "text_ingestor"

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        ts_store: TimeSeriesStore,
        doc_store: DocumentStore,
        event_type: str = "text_event",
        sentiment_fn: Callable[[str], float] | None = None,
    ) -> None:
        super().__init__(config, event_bus)
        self.ts = ts_store
        self.doc = doc_store
        self.event_type = event_type
        self._sentiment_fn = sentiment_fn or vader_sentiment
        # Bound the queue to apply backpressure: if downstream processing stalls,
        # put() blocks the ingest caller rather than growing memory unboundedly.
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)

    async def ingest(self, session_id: str, source_label: str, content: str,
                     metadata: dict | None = None) -> dict:
        """
        Score and store a single text event.

        Returns the derived numeric signals dict.
        """
        sentiment = self._sentiment_fn(content)
        is_question = content.strip().endswith("?")
        signals = {
            "sentiment_score": sentiment,
            "text_length":     float(len(content)),
            "is_question":     float(is_question),
        }

        self.ts.write_point("asset_telemetry", signals, tags={"session_id": session_id})

        self.doc.log_event(
            self.event_type,
            {
                "session_id":     session_id,
                "source":         source_label,
                "content":        content,
                "sentiment_score": sentiment,
                "is_question":    is_question,
                **(metadata or {}),
            },
            severity="info",
        )

        log.debug("TextIngestor: %s event (sentiment=%.2f, len=%d)",
                  source_label, sentiment, len(content))
        return signals

    async def ingest_async(self, session_id: str, source_label: str,
                           content: str, metadata: dict | None = None) -> None:
        """Non-blocking: push to internal queue, processed by run()."""
        await self._queue.put({
            "session_id":   session_id,
            "source_label": source_label,
            "content":      content,
            "metadata":     metadata,
        })

    async def start(self) -> None:
        """LayerBase entry point — drain the queue continuously."""
        self._running = True
        while self._running:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self.ingest(**item)
            except TimeoutError:
                pass
            except Exception as e:
                log.error("TextIngestor error: %s", e)

    # Backwards-compatible alias for code that called ``run()`` directly.
    async def run(self) -> None:
        await self.start()
