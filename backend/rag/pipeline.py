"""RAG pipeline orchestrator — ties together metric tools, retrieval, and generation.

Full flow:
  1. Query metric tools → deterministic metric data
  2. Build contextualized retrieval query from metric state
  3. Two-stage retrieval: vector search → hybrid rerank → MMR
  4. Generate streaming recommendation grounded in retrieved knowledge
  5. Save conversation to DynamoDB (short-term memory)
"""
import json
import logging
from dataclasses import dataclass, asdict
from typing import Generator

from shared.config import config
from shared.dynamodb_client import get_dynamodb_client
from backend.tools.metric_tools import MetricResult, get_metric
from backend.rag.retriever import retrieve, build_query, RetrievedChunk
from backend.rag.generator import generate_recommendation_stream, _status_label, _gap_pct

logger = logging.getLogger(__name__)


@dataclass
class InsightCardData:
    vendor_id: str
    metric_name: str
    display_name: str
    value: float
    unit: str
    goal: float
    goal_type: str
    period: str
    gap_pct: float
    status: str
    alert_level: str
    trend_pct: float
    trend_direction: str
    contributors_top: list
    contributors_bottom: list
    retrieved_sources: list


def prepare_insight_card(vendor_id: str, metric_name: str, period: str = "QTD") -> tuple:
    """Prepare deterministic InsightCard data and retrieved context."""
    metric = get_metric(vendor_id, metric_name, period)
    if not metric:
        raise ValueError(f"Metric '{metric_name}' not found for vendor '{vendor_id}'")

    query = build_query(metric, vendor_context={"vendor_id": vendor_id})
    chunks = retrieve(query, metric_name=metric_name)

    status, level = _status_label(metric)
    gap = _gap_pct(metric)

    card_data = InsightCardData(
        vendor_id=vendor_id,
        metric_name=metric.metric_name,
        display_name=metric.display_name,
        value=metric.value,
        unit=metric.unit,
        goal=metric.goal,
        goal_type=metric.goal_type,
        period=metric.period,
        gap_pct=gap,
        status=status,
        alert_level=level,
        trend_pct=metric.trend_pct,
        trend_direction=metric.trend_direction,
        contributors_top=[
            {"name": c.name, "value": c.value, "contribution_pct": c.contribution_pct, "trend": c.trend}
            for c in metric.contributors_top
        ],
        contributors_bottom=[
            {"name": c.name, "value": c.value, "contribution_pct": c.contribution_pct, "trend": c.trend}
            for c in metric.contributors_bottom
        ],
        retrieved_sources=[
            {
                "source": c.metadata.get("source", ""),
                "section": c.metadata.get("section", ""),
                "semantic_score": round(c.semantic_score, 3),
                "rerank_score": round(c.rerank_score, 3),
                "rank": c.final_rank,
            }
            for c in chunks
        ],
    )

    return card_data, chunks


def stream_insight_recommendation(vendor_id: str, metric_name: str, period: str = "QTD",
                                   session_id: str = "") -> Generator[dict, None, None]:
    """Full streaming pipeline: prepare card → stream recommendation → save memory."""
    card_data, chunks = prepare_insight_card(vendor_id, metric_name, period)

    yield {"event": "card_data", "data": json.dumps(asdict(card_data), ensure_ascii=False)}

    metric = get_metric(vendor_id, metric_name, period)
    recommendation_text = ""

    for token in generate_recommendation_stream(metric, chunks):
        recommendation_text += token
        yield {"event": "token", "data": json.dumps({"text": token}, ensure_ascii=False)}

    # Save conversation to DynamoDB (short-term memory)
    if session_id:
        try:
            db = get_dynamodb_client()
            db.save_conversation(session_id, [
                {"role": "user", "content": f"Show insight for {metric_name} ({vendor_id}, {period})"},
                {"role": "assistant", "content": recommendation_text[:2000]},
            ])
        except Exception as e:
            logger.warning(f"Failed to save conversation memory: {e}")

    yield {"event": "done", "data": json.dumps({"status": "complete"})}
