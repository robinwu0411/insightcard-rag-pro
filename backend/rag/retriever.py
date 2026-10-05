"""RAG retriever — two-stage retrieval: vector search + hybrid rerank + MMR.

Stage 1: Semantic vector search via vector store → Top-K candidates
Stage 2: Hybrid reranking (semantic 55% + keyword 25% + section 20%) + MMR → Top-3

This module is used by the backend API (online query).
"""
import re
import logging
from dataclasses import dataclass
from typing import Optional

from shared.config import config
from shared.vector_store import get_vector_store
from shared.embedding_client import get_embedding_client

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    text: str
    metadata: dict
    semantic_score: float
    rerank_score: float
    final_rank: int = 0


def _tokenize(text: str) -> set:
    return set(re.findall(r"[a-z]{2,}", text.lower()))


def _keyword_overlap_score(query_tokens: set, chunk_text: str) -> float:
    chunk_tokens = _tokenize(chunk_text)
    if not query_tokens:
        return 0.0
    return len(query_tokens & chunk_tokens) / len(query_tokens)


def _section_specificity_bonus(section: str, metric_name: str) -> float:
    section_lower = section.lower()
    metric_lower = metric_name.lower()
    metric_terms = {
        "net_ppm": ["net ppm", "profitability", "margin", "pricing", "profit"],
        "revenue": ["revenue", "shipped revenue", "traffic", "glance"],
        "cr": ["conversion", "cr", "conversion rate", "detail page", "review"],
        "in_stock_rate": ["in-stock", "in stock", "supply", "inventory", "fill rate"],
        "glance_views": ["glance", "traffic", "advertising", "search", "visibility"],
    }
    terms = metric_terms.get(metric_lower, [])
    bonus = 0.0
    for term in terms:
        if term in section_lower:
            bonus += 0.15
    return min(bonus, 0.45)


def _mmr_select(candidates: list[RetrievedChunk], final_k: int, lambda_param: float = 0.7) -> list[RetrievedChunk]:
    """Maximal Marginal Relevance for diversity-aware selection."""
    if len(candidates) <= final_k:
        return candidates

    selected = []
    remaining = list(candidates)
    remaining.sort(key=lambda c: c.rerank_score, reverse=True)
    selected.append(remaining.pop(0))

    while len(selected) < final_k and remaining:
        best_score = -1.0
        best_idx = 0
        for i, cand in enumerate(remaining):
            max_sim = max(
                len(_tokenize(cand.text) & _tokenize(sel.text)) /
                max(len(_tokenize(cand.text) | _tokenize(sel.text)), 1)
                for sel in selected
            ) if selected else 0.0
            mmr_score = lambda_param * cand.rerank_score - (1 - lambda_param) * max_sim
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i
        selected.append(remaining.pop(best_idx))

    return selected


def retrieve(query: str, metric_name: str = "", top_k: Optional[int] = None,
             final_k: Optional[int] = None) -> list[RetrievedChunk]:
    """Two-stage retrieval: vector search → hybrid reranking → MMR."""
    top_k = top_k or config.retrieval_top_k
    final_k = final_k or config.rerank_top_k

    embedding_client = get_embedding_client()
    vector_store = get_vector_store()

    # Stage 1: embed query and search
    query_embedding = embedding_client.embed_one(query)
    results = vector_store.query(query_embedding=query_embedding, top_k=top_k)

    if not results:
        logger.warning(f"No results retrieved for query: {query[:100]}")
        return []

    # Stage 2: hybrid reranking
    query_tokens = _tokenize(query)
    candidates = []

    for result in results:
        doc = result["text"]
        meta = result["metadata"]
        semantic_score = result["semantic_score"]

        kw_score = _keyword_overlap_score(query_tokens, doc)
        section_bonus = _section_specificity_bonus(meta.get("section", ""), metric_name)
        rerank_score = 0.55 * semantic_score + 0.25 * kw_score + section_bonus

        candidates.append(RetrievedChunk(
            text=doc,
            metadata=meta,
            semantic_score=semantic_score,
            rerank_score=rerank_score,
        ))

    # MMR for diversity
    selected = _mmr_select(candidates, final_k)

    for i, chunk in enumerate(selected):
        chunk.final_rank = i + 1

    logger.info(f"Retrieved {len(results)} → reranked → {len(selected)} final chunks")
    return selected


def build_query(metric_result, vendor_context: dict = None) -> str:
    """Construct a contextualized retrieval query from metric data."""
    parts = [
        metric_result.display_name,
        f"current value {metric_result.value} {metric_result.unit}",
        f"goal {metric_result.goal} {metric_result.unit}",
    ]

    gap = metric_result.value - metric_result.goal
    gap_pct = (gap / metric_result.goal) * 100 if metric_result.goal else 0
    if metric_result.goal_type == "higher_better":
        parts.append(f"below goal by {abs(gap_pct):.1f}%" if gap < 0 else "above goal")
    else:
        parts.append(f"above goal by {abs(gap_pct):.1f}%" if gap > 0 else "below goal")

    parts.append(f"trend {metric_result.trend_direction} {abs(metric_result.trend_pct):.1f}%")

    if metric_result.contributors_bottom:
        bottom_names = [c.name for c in metric_result.contributors_bottom[:2]]
        parts.append(f"underperforming contributors: {', '.join(bottom_names)}")

    parts.append("remediation actions optimization strategy recommendations")
    return ", ".join(parts)
