"""LangGraph-based RAG pipeline orchestrator for InsightCard.

Graph topology:

  START
    -> fetch_metric       (deterministic metric data from internal tools)
    -> build_query        (contextualized retrieval query from metric state)
    -> vector_search      (Stage 1: semantic vector search -> Top-K candidates)
    -> hybrid_rerank      (Stage 2: hybrid rerank + MMR diversity selection)
    -> build_card         (assemble InsightCard data structure)
    -> [conditional edge: route_generation]
        |-- generate_openai    (ChatOpenAI — supports native token streaming)
        |-- generate_bedrock   (AWS Bedrock — Claude via boto3)
        |-- generate_template  (rule-based fallback, no LLM required)
    -> save_memory        (persist conversation to DynamoDB)
    -> END

Design notes:
  - Each node is a pure function: (state) -> partial state update.
  - Custom retrieval logic (MMR, hybrid rerank, section-specificity bonus)
    is preserved as the implementation of hybrid_rerank — LangGraph does NOT
    force you to use LangChain's retriever abstractions. You write the node,
    LangGraph handles the orchestration, state, and routing.
  - The generate_openai node uses langchain_openai.ChatOpenAI, which enables
    token-level streaming via graph.astream_events(). This works with
    OpenAI, DeepSeek, and any OpenAI-compatible API (via base_url).
  - generate_bedrock and generate_template are fallback nodes that don't
    use LangChain LLM abstractions; they produce the full text in one shot.
"""
from __future__ import annotations

import json
import logging
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END, START

from shared.config import config
from shared.dynamodb_client import get_dynamodb_client
from shared.vector_store import get_vector_store
from shared.embedding_client import get_embedding_client
from backend.tools.metric_tools import MetricResult, get_metric
from backend.rag.retriever import (
    RetrievedChunk,
    _tokenize,
    _keyword_overlap_score,
    _section_specificity_bonus,
    _mmr_select,
    build_query as _build_query_str,
)
from backend.rag.generator import (
    _status_label,
    _gap_pct,
    _SYSTEM_PROMPT,
    _build_template_recommendation,
    _build_openai_prompt,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph State — the single source of truth that flows through every node
# ---------------------------------------------------------------------------

class RAGState(TypedDict, total=False):
    """Typed state that flows through the LangGraph pipeline.

    `total=False` allows partial returns from nodes (each node only
    returns the keys it updates).
    """
    # --- Input (set at invocation) ---
    vendor_id: str
    metric_name: str
    period: str
    session_id: str

    # --- Intermediate results ---
    metric: Optional[MetricResult]
    query: str
    raw_results: list[dict]
    reranked_chunks: list[RetrievedChunk]
    card_data: Optional[dict]
    recommendation: str
    messages: list[dict]


# ---------------------------------------------------------------------------
# Node 1: fetch_metric — deterministic metric data from internal tools
# ---------------------------------------------------------------------------

def fetch_metric_node(state: RAGState) -> dict:
    """Fetch vendor performance metric from internal tools (mock Ripple API)."""
    metric = get_metric(
        state["vendor_id"],
        state["metric_name"],
        state.get("period", "QTD"),
    )
    if not metric:
        raise ValueError(
            f"Metric '{state['metric_name']}' not found for vendor '{state['vendor_id']}'"
        )
    return {"metric": metric}


# ---------------------------------------------------------------------------
# Node 2: build_query — contextualized retrieval query from metric state
# ---------------------------------------------------------------------------

def build_query_node(state: RAGState) -> dict:
    """Construct a contextualized retrieval query from the metric data."""
    query = _build_query_str(
        state["metric"],
        vendor_context={"vendor_id": state["vendor_id"]},
    )
    return {"query": query}


# ---------------------------------------------------------------------------
# Node 3: vector_search — Stage 1: semantic vector search -> Top-K
# ---------------------------------------------------------------------------

def vector_search_node(state: RAGState) -> dict:
    """Embed the query and search the vector store for Top-K candidates."""
    embedding_client = get_embedding_client()
    vector_store = get_vector_store()

    query_embedding = embedding_client.embed_one(state["query"])
    results = vector_store.query(
        query_embedding=query_embedding,
        top_k=config.retrieval_top_k,
    )

    if not results:
        logger.warning(f"No results retrieved for query: {state['query'][:100]}")

    return {"raw_results": results}


# ---------------------------------------------------------------------------
# Node 4: hybrid_rerank — Stage 2: hybrid reranking + MMR diversity
# ---------------------------------------------------------------------------

def hybrid_rerank_node(state: RAGState) -> dict:
    """Hybrid reranking (semantic 55% + keyword 25% + section bonus 20%) + MMR.

    This is the SAME custom retrieval logic from the original retriever.py,
    now expressed as a LangGraph node. LangGraph doesn't constrain the
    implementation — you can use any Python code inside a node.
    """
    results = state.get("raw_results", [])
    metric_name = state.get("metric_name", "")
    query = state.get("query", "")
    final_k = config.rerank_top_k

    if not results:
        return {"reranked_chunks": []}

    query_tokens = _tokenize(query)
    candidates: list[RetrievedChunk] = []

    for result in results:
        doc = result["text"]
        meta = result["metadata"]
        semantic_score = result["semantic_score"]

        kw_score = _keyword_overlap_score(query_tokens, doc)
        section_bonus = _section_specificity_bonus(
            meta.get("section", ""), metric_name
        )
        rerank_score = 0.55 * semantic_score + 0.25 * kw_score + section_bonus

        candidates.append(RetrievedChunk(
            text=doc,
            metadata=meta,
            semantic_score=semantic_score,
            rerank_score=rerank_score,
        ))

    # MMR for diversity-aware final selection
    selected = _mmr_select(candidates, final_k)
    for i, chunk in enumerate(selected):
        chunk.final_rank = i + 1

    logger.info(
        f"Retrieved {len(results)} -> reranked -> {len(selected)} final chunks"
    )
    return {"reranked_chunks": selected}


# ---------------------------------------------------------------------------
# Node 5: build_card — assemble InsightCard data structure
# ---------------------------------------------------------------------------

def build_card_node(state: RAGState) -> dict:
    """Assemble the deterministic InsightCard data from metric + retrieved chunks."""
    metric = state["metric"]
    chunks = state["reranked_chunks"]

    status, level = _status_label(metric)
    gap = _gap_pct(metric)

    card_data = {
        "vendor_id": state["vendor_id"],
        "metric_name": metric.metric_name,
        "display_name": metric.display_name,
        "value": metric.value,
        "unit": metric.unit,
        "goal": metric.goal,
        "goal_type": metric.goal_type,
        "period": metric.period,
        "gap_pct": gap,
        "status": status,
        "alert_level": level,
        "trend_pct": metric.trend_pct,
        "trend_direction": metric.trend_direction,
        "contributors_top": [
            {
                "name": c.name,
                "value": c.value,
                "contribution_pct": c.contribution_pct,
                "trend": c.trend,
            }
            for c in metric.contributors_top
        ],
        "contributors_bottom": [
            {
                "name": c.name,
                "value": c.value,
                "contribution_pct": c.contribution_pct,
                "trend": c.trend,
            }
            for c in metric.contributors_bottom
        ],
        "retrieved_sources": [
            {
                "source": c.metadata.get("source", ""),
                "section": c.metadata.get("section", ""),
                "semantic_score": round(c.semantic_score, 3),
                "rerank_score": round(c.rerank_score, 3),
                "rank": c.final_rank,
            }
            for c in chunks
        ],
    }

    return {"card_data": card_data}


# ---------------------------------------------------------------------------
# Conditional edge: route_generation
# ---------------------------------------------------------------------------

def route_generation(state: RAGState) -> str:
    """Conditional edge — route to the appropriate generation node.

    Routing logic:
      - LLM_PROVIDER=bedrock + AWS_REGION set  -> generate_bedrock
      - OPENAI_API_KEY set                     -> generate_openai (ChatOpenAI)
      - neither                                -> generate_template (fallback)
    """
    if config.llm_provider == "bedrock" and config.aws_region:
        return "generate_bedrock"
    elif config.use_openai:
        return "generate_openai"
    else:
        return "generate_template"


# ---------------------------------------------------------------------------
# Node 6a: generate_openai — ChatOpenAI (LangChain integration)
# ---------------------------------------------------------------------------

def generate_openai_node(state: RAGState) -> dict:
    """Generate recommendation via langchain_openai.ChatOpenAI.

    Using ChatOpenAI (instead of raw openai SDK) enables:
      - Native token-level streaming via graph.astream_events()
      - LangChain callback system for observability/tracing
      - Consistent interface with other LangChain LLM integrations

    Supports OpenAI, DeepSeek, and any OpenAI-compatible API via base_url.
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage

    metric = state["metric"]
    chunks = state["reranked_chunks"]

    llm = ChatOpenAI(
        model=config.llm_model,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
        api_key=config.openai_api_key,
        base_url=config.openai_base_url or None,
        streaming=True,
    )

    prompt = _build_openai_prompt(metric, chunks)
    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ]

    response = llm.invoke(messages)
    recommendation = response.content

    return {
        "recommendation": recommendation,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Show insight for {state['metric_name']} "
                    f"({state['vendor_id']}, {state.get('period', 'QTD')})"
                ),
            },
            {"role": "assistant", "content": recommendation[:2000]},
        ],
    }


# ---------------------------------------------------------------------------
# Node 6b: generate_bedrock — AWS Bedrock (Claude via boto3)
# ---------------------------------------------------------------------------

def generate_bedrock_node(state: RAGState) -> dict:
    """Generate recommendation via AWS Bedrock (Claude).

    Uses boto3 directly. For LangGraph native streaming with Bedrock,
    you could swap this for langchain_aws.BedrockChat.
    """
    import boto3

    metric = state["metric"]
    chunks = state["reranked_chunks"]

    client = boto3.client("bedrock-runtime", region_name=config.aws_region)
    prompt = _build_openai_prompt(metric, chunks)
    full_prompt = f"{_SYSTEM_PROMPT}\n\n{prompt}"

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": config.llm_max_tokens,
        "temperature": config.llm_temperature,
        "messages": [{"role": "user", "content": full_prompt}],
    })

    response = client.invoke_model(
        modelId="anthropic.claude-3-haiku-20240307-v1:0",
        body=body,
    )

    response_body = json.loads(response["body"].read())
    recommendation = response_body.get("content", [{}])[0].get("text", "")

    return {
        "recommendation": recommendation,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Show insight for {state['metric_name']} "
                    f"({state['vendor_id']}, {state.get('period', 'QTD')})"
                ),
            },
            {"role": "assistant", "content": recommendation[:2000]},
        ],
    }


# ---------------------------------------------------------------------------
# Node 6c: generate_template — rule-based fallback (no LLM required)
# ---------------------------------------------------------------------------

def generate_template_node(state: RAGState) -> dict:
    """Template-based generation — extracts actions from retrieved chunks.

    No API key or LLM call required. Produces structured recommendations
    from the retrieved playbook content using rule-based extraction.
    """
    metric = state["metric"]
    chunks = state["reranked_chunks"]

    recommendation = _build_template_recommendation(metric, chunks)

    return {
        "recommendation": recommendation,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Show insight for {state['metric_name']} "
                    f"({state['vendor_id']}, {state.get('period', 'QTD')})"
                ),
            },
            {"role": "assistant", "content": recommendation[:2000]},
        ],
    }


# ---------------------------------------------------------------------------
# Node 7: save_memory — persist conversation to DynamoDB
# ---------------------------------------------------------------------------

def save_memory_node(state: RAGState) -> dict:
    """Save conversation to DynamoDB for short-term memory."""
    session_id = state.get("session_id", "")
    messages = state.get("messages", [])

    if session_id and messages:
        try:
            db = get_dynamodb_client()
            db.save_conversation(session_id, messages)
        except Exception as e:
            logger.warning(f"Failed to save conversation memory: {e}")

    return {}


# ---------------------------------------------------------------------------
# Build & compile the graph
# ---------------------------------------------------------------------------

def build_rag_graph() -> StateGraph:
    """Build and compile the LangGraph RAG pipeline.

    Returns a compiled graph that can be invoked via:
      - graph.invoke(input_state)              — sync, returns final state
      - graph.stream(input_state)              — sync, yields node updates
      - await graph.ainvoke(input_state)       — async, returns final state
      - async for ev in graph.astream(...)     — async, yields node updates
      - async for ev in graph.astream_events() — async, yields fine-grained
        events including token-level LLM streaming
    """
    graph = StateGraph(RAGState)

    # Register nodes
    graph.add_node("fetch_metric", fetch_metric_node)
    graph.add_node("build_query", build_query_node)
    graph.add_node("vector_search", vector_search_node)
    graph.add_node("hybrid_rerank", hybrid_rerank_node)
    graph.add_node("build_card", build_card_node)
    graph.add_node("generate_openai", generate_openai_node)
    graph.add_node("generate_bedrock", generate_bedrock_node)
    graph.add_node("generate_template", generate_template_node)
    graph.add_node("save_memory", save_memory_node)

    # Linear edges: START -> fetch_metric -> ... -> build_card
    graph.add_edge(START, "fetch_metric")
    graph.add_edge("fetch_metric", "build_query")
    graph.add_edge("build_query", "vector_search")
    graph.add_edge("vector_search", "hybrid_rerank")
    graph.add_edge("hybrid_rerank", "build_card")

    # Conditional edge: build_card -> {generate_openai | generate_bedrock | generate_template}
    graph.add_conditional_edges(
        "build_card",
        route_generation,
        {
            "generate_openai": "generate_openai",
            "generate_bedrock": "generate_bedrock",
            "generate_template": "generate_template",
        },
    )

    # All generation nodes converge to save_memory
    graph.add_edge("generate_openai", "save_memory")
    graph.add_edge("generate_bedrock", "save_memory")
    graph.add_edge("generate_template", "save_memory")

    # save_memory -> END
    graph.add_edge("save_memory", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Singleton accessor — the compiled graph is stateless and thread-safe
# ---------------------------------------------------------------------------

def build_preparation_graph() -> StateGraph:
    """Build a subgraph for the preparation phase only (no generation/memory).

    This is used by prepare_insight_card() when we only need the card data
    and retrieved chunks without generating a recommendation. Runs:
      fetch_metric -> build_query -> vector_search -> hybrid_rerank -> build_card -> END
    """
    graph = StateGraph(RAGState)

    graph.add_node("fetch_metric", fetch_metric_node)
    graph.add_node("build_query", build_query_node)
    graph.add_node("vector_search", vector_search_node)
    graph.add_node("hybrid_rerank", hybrid_rerank_node)
    graph.add_node("build_card", build_card_node)

    graph.add_edge(START, "fetch_metric")
    graph.add_edge("fetch_metric", "build_query")
    graph.add_edge("build_query", "vector_search")
    graph.add_edge("vector_search", "hybrid_rerank")
    graph.add_edge("hybrid_rerank", "build_card")
    graph.add_edge("build_card", END)

    return graph.compile()


_prep_graph = None


def get_preparation_graph():
    """Return the compiled preparation subgraph (singleton)."""
    global _prep_graph
    if _prep_graph is None:
        _prep_graph = build_preparation_graph()
    return _prep_graph


# ---------------------------------------------------------------------------
# Singleton accessor — the compiled graph is stateless and thread-safe
# ---------------------------------------------------------------------------

_rag_graph = None


def get_rag_graph():
    """Return the compiled RAG graph (singleton).

    The compiled graph is stateless — all state lives in the TypedDict
    that flows through it. Safe to reuse across requests.
    """
    global _rag_graph
    if _rag_graph is None:
        _rag_graph = build_rag_graph()
    return _rag_graph
