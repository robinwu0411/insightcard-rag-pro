"""RAG pipeline orchestrator — LangGraph-based implementation.

This module wraps the LangGraph RAG pipeline (see graph.py) and exposes
the same public API that main.py and other consumers depend on:

  - prepare_insight_card(vendor_id, metric_name, period)
      -> (InsightCardData, list[RetrievedChunk])
      Runs the preparation subgraph (fetch_metric -> ... -> build_card).

  - stream_insight_recommendation(vendor_id, metric_name, period, session_id)
      -> Generator[dict]
      Sync SSE generator. Runs the full graph with stream_mode="updates",
      yielding card_data, then streaming the recommendation text, then done.

  - astream_insight_recommendation(vendor_id, metric_name, period, session_id)
      -> AsyncGenerator[dict]
      Async SSE generator. Uses graph.astream_events() for true token-level
      streaming when ChatOpenAI is the generation backend.

The graph topology and all custom retrieval logic (MMR, hybrid rerank,
section-specificity bonus, conditional LLM routing) live in graph.py.
"""
import json
import logging
import time
from dataclasses import dataclass
from typing import Generator, AsyncGenerator

from backend.rag.graph import (
    get_rag_graph,
    get_preparation_graph,
    RAGState,
)
from backend.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# InsightCardData — kept for backward compatibility with main.py
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# prepare_insight_card — runs the preparation subgraph
# ---------------------------------------------------------------------------

def prepare_insight_card(
    vendor_id: str,
    metric_name: str,
    period: str = "QTD",
) -> tuple[InsightCardData, list[RetrievedChunk]]:
    """Prepare deterministic InsightCard data and retrieved context.

    Uses the LangGraph preparation subgraph:
      fetch_metric -> build_query -> vector_search -> hybrid_rerank -> build_card

    Returns (card_data, chunks) without running generation or memory save.
    """
    prep_graph = get_preparation_graph()

    initial_state: RAGState = {
        "vendor_id": vendor_id,
        "metric_name": metric_name,
        "period": period,
        "session_id": "",
    }

    final_state = prep_graph.invoke(initial_state)

    # Reconstruct InsightCardData from the graph state (dict)
    card_dict = final_state["card_data"]
    card_data = InsightCardData(
        vendor_id=card_dict["vendor_id"],
        metric_name=card_dict["metric_name"],
        display_name=card_dict["display_name"],
        value=card_dict["value"],
        unit=card_dict["unit"],
        goal=card_dict["goal"],
        goal_type=card_dict["goal_type"],
        period=card_dict["period"],
        gap_pct=card_dict["gap_pct"],
        status=card_dict["status"],
        alert_level=card_dict["alert_level"],
        trend_pct=card_dict["trend_pct"],
        trend_direction=card_dict["trend_direction"],
        contributors_top=card_dict["contributors_top"],
        contributors_bottom=card_dict["contributors_bottom"],
        retrieved_sources=card_dict["retrieved_sources"],
    )

    chunks = final_state.get("reranked_chunks", [])
    return card_data, chunks


# ---------------------------------------------------------------------------
# stream_insight_recommendation — sync SSE generator (node-level streaming)
# ---------------------------------------------------------------------------

def stream_insight_recommendation(
    vendor_id: str,
    metric_name: str,
    period: str = "QTD",
    session_id: str = "",
) -> Generator[dict, None, None]:
    """Full streaming pipeline via LangGraph (sync, node-level updates).

    Yields SSE events:
      1. {"event": "card_data",   "data": <json>}   -- after build_card node
      2. {"event": "token",       "data": <json>}   -- recommendation tokens
      3. {"event": "done",        "data": <json>}   -- after save_memory node

    For sync mode, token streaming is simulated by chunking the completed
    recommendation text (the LLM call blocks until complete). For true
    token-level streaming, use astream_insight_recommendation() instead.
    """
    rag_graph = get_rag_graph()

    initial_state: RAGState = {
        "vendor_id": vendor_id,
        "metric_name": metric_name,
        "period": period,
        "session_id": session_id,
    }

    card_data_sent = False
    recommendation = ""

    # Stream node-level updates from the graph
    for update in rag_graph.stream(initial_state, stream_mode="updates"):
        for node_name, node_output in update.items():
            if node_name == "build_card" and not card_data_sent:
                card_data = node_output.get("card_data")
                if card_data:
                    yield {
                        "event": "card_data",
                        "data": json.dumps(card_data, ensure_ascii=False),
                    }
                    card_data_sent = True

            if node_name in (
                "generate_openai",
                "generate_bedrock",
                "generate_template",
            ):
                recommendation = node_output.get("recommendation", "")

            if node_name == "save_memory":
                # Stream the recommendation text in batches (sync mode)
                if recommendation:
                    words = recommendation.split(" ")
                    for i in range(0, len(words), 3):
                        batch = words[i : i + 3]
                        yield {
                            "event": "token",
                            "data": json.dumps(
                                {"text": " ".join(batch) + " "},
                                ensure_ascii=False,
                            ),
                        }
                        time.sleep(0.03)

                yield {
                    "event": "done",
                    "data": json.dumps({"status": "complete"}),
                }


# ---------------------------------------------------------------------------
# astream_insight_recommendation — async SSE generator (token-level streaming)
# ---------------------------------------------------------------------------

async def astream_insight_recommendation(
    vendor_id: str,
    metric_name: str,
    period: str = "QTD",
    session_id: str = "",
) -> AsyncGenerator[dict, None]:
    """Full streaming pipeline via LangGraph (async, token-level streaming).

    Uses graph.astream_events(version="v2") which yields fine-grained events:
      - on_chain_end (build_card)  -> card_data SSE event
      - on_chat_model_stream       -> token SSE events (from ChatOpenAI)
      - on_chain_end (save_memory) -> done SSE event

    This enables TRUE token-by-token streaming from the LLM, not simulated.
    Only works when the generation backend is ChatOpenAI (generate_openai node).
    For template/bedrock backends, falls back to word-batch streaming.
    """
    rag_graph = get_rag_graph()

    initial_state: RAGState = {
        "vendor_id": vendor_id,
        "metric_name": metric_name,
        "period": period,
        "session_id": session_id,
    }

    card_data_sent = False
    recommendation = ""
    generation_node = None

    async for event in rag_graph.astream_events(initial_state, version="v2"):
        kind = event["event"]
        name = event.get("name", "")

        # Yield card_data when build_card node completes
        if kind == "on_chain_end" and name == "build_card":
            output = event["data"].get("output", {})
            card_data = output.get("card_data")
            if card_data and not card_data_sent:
                yield {
                    "event": "card_data",
                    "data": json.dumps(card_data, ensure_ascii=False),
                }
                card_data_sent = True

        # Token-level streaming from ChatOpenAI
        elif kind == "on_chat_model_stream":
            chunk = event["data"].get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                yield {
                    "event": "token",
                    "data": json.dumps(
                        {"text": chunk.content}, ensure_ascii=False
                    ),
                }

        # Track which generation node ran
        elif kind == "on_chain_start" and name in (
            "generate_openai",
            "generate_bedrock",
            "generate_template",
        ):
            generation_node = name

        # Capture full recommendation when generation node ends
        elif (
            kind == "on_chain_end"
            and name in (
                "generate_openai",
                "generate_bedrock",
                "generate_template",
            )
        ):
            output = event["data"].get("output", {})
            recommendation = output.get("recommendation", "")

        # Done when save_memory completes
        elif kind == "on_chain_end" and name == "save_memory":
            # For non-LLM backends (template/bedrock), stream the text now
            # since there were no on_chat_model_stream events
            if recommendation and generation_node in (
                "generate_template",
                "generate_bedrock",
            ):
                words = recommendation.split(" ")
                for i in range(0, len(words), 3):
                    batch = words[i : i + 3]
                    yield {
                        "event": "token",
                        "data": json.dumps(
                            {"text": " ".join(batch) + " "},
                            ensure_ascii=False,
                        ),
                    }

            yield {
                "event": "done",
                "data": json.dumps({"status": "complete"}),
            }
