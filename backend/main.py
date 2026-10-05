"""FastAPI server for InsightCard RAG Pro — production version.

Endpoints:
  GET  /api/health              — health check + chunk count
  GET  /api/scorecard           — full scorecard (all metrics)
  GET  /api/metrics             — list available metrics
  GET  /api/metrics/{name}      — get specific metric data
  GET  /api/insight/stream      — SSE stream: card data + AI recommendation
  GET  /api/insight/preview     — non-streaming preview (debug)
  POST /api/rag/ingest          — trigger knowledge base ingestion
  GET  /api/rag/sources         — list knowledge base chunks
  GET  /api/conversation/{sid}  — get conversation history (short-term memory)
  DELETE /api/conversation/{sid} — clear conversation history
"""
import sys
import json
import logging
from pathlib import Path
from dataclasses import asdict
from uuid import uuid4

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from shared.config import config
from backend.tools.metric_tools import (
    get_metric, list_available_metrics, get_scorecard_summary,
)
from backend.rag.pipeline import prepare_insight_card, stream_insight_recommendation
from backend.rag.generator import _status_label, _gap_pct
from backend.rag.chunker import ingest_directory
from backend.rag.cleaner import clean_chunks
from shared.vector_store import get_vector_store
from shared.embedding_client import get_embedding_client
from shared.dynamodb_client import get_dynamodb_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="InsightCard RAG Pro",
    description="Vendor Performance Insight Card with RAG-powered recommendations",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Production: restrict to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """Ensure knowledge base is ingested on startup (local dev only)."""
    if config.vector_store_type == "chromadb":
        logger.info("Local dev mode — ingesting knowledge docs on startup...")
        try:
            vs = get_vector_store()
            if vs.count() == 0 and config.knowledge_docs_dir.exists():
                chunks = ingest_directory(str(config.knowledge_docs_dir), config.chunk_size, config.chunk_overlap)
                chunks = clean_chunks(chunks)
                emb_client = get_embedding_client()

                texts = [c.text for c in chunks]
                metadatas = [c.metadata for c in chunks]
                ids = [f"chunk_{i}" for i in range(len(chunks))]
                embeddings = emb_client.embed_batch(texts)

                vs.add(ids=ids, texts=texts, metadatas=metadatas, embeddings=embeddings)
                logger.info(f"Knowledge base ready: {len(chunks)} chunks in vector store")
        except Exception as e:
            logger.warning(f"Startup ingestion failed: {e}")
    else:
        logger.info(f"Production mode — vector store: {config.vector_store_type}")


@app.get("/api/health")
async def health():
    try:
        vs = get_vector_store()
        chunk_count = vs.count()
    except Exception:
        chunk_count = 0
    return {
        "status": "ok",
        "knowledge_base_chunks": chunk_count,
        "vector_store": config.vector_store_type,
        "embedding_provider": config.embedding_provider,
        "llm_provider": config.llm_provider if config.use_openai else "template",
    }


@app.get("/api/scorecard")
async def get_scorecard(
    vendor_id: str = Query("toshiba_hl"),
    period: str = Query("QTD"),
):
    return get_scorecard_summary(vendor_id, period)


@app.get("/api/metrics")
async def list_metrics():
    return {"metrics": list_available_metrics()}


@app.get("/api/metrics/{metric_name}")
async def get_metric_data(
    metric_name: str,
    vendor_id: str = Query("toshiba_hl"),
    period: str = Query("QTD"),
):
    metric = get_metric(vendor_id, metric_name, period)
    if not metric:
        raise HTTPException(status_code=404, detail=f"Metric '{metric_name}' not found")

    status, level = _status_label(metric)
    gap = _gap_pct(metric)

    return {
        "vendor_id": vendor_id,
        "metric_name": metric.metric_name,
        "display_name": metric.display_name,
        "value": metric.value,
        "unit": metric.unit,
        "goal": metric.goal,
        "goal_type": metric.goal_type,
        "period": metric.period,
        "gap_pct": round(gap, 1),
        "status": status,
        "alert_level": level,
        "trend_pct": metric.trend_pct,
        "trend_direction": metric.trend_direction,
        "contributors_top": [
            {"name": c.name, "value": c.value, "contribution_pct": c.contribution_pct, "trend": c.trend}
            for c in metric.contributors_top
        ],
        "contributors_bottom": [
            {"name": c.name, "value": c.value, "contribution_pct": c.contribution_pct, "trend": c.trend}
            for c in metric.contributors_bottom
        ],
    }


@app.get("/api/insight/stream")
async def insight_stream(
    vendor_id: str = Query("toshiba_hl"),
    metric_name: str = Query("net_ppm"),
    period: str = Query("QTD"),
    x_session_id: str = Header(default=""),
):
    session_id = x_session_id or str(uuid4())

    def event_generator():
        try:
            for event in stream_insight_recommendation(vendor_id, metric_name, period, session_id):
                yield event
        except Exception as e:
            logger.error(f"Stream error: {e}", exc_info=True)
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_generator())


@app.get("/api/insight/preview")
async def insight_preview(
    vendor_id: str = Query("toshiba_hl"),
    metric_name: str = Query("net_ppm"),
    period: str = Query("QTD"),
):
    card_data, chunks = prepare_insight_card(vendor_id, metric_name, period)
    result = asdict(card_data)
    result["retrieved_chunks"] = [
        {
            "text": c.text[:200] + "...",
            "source": c.metadata.get("source", ""),
            "section": c.metadata.get("section", ""),
            "semantic_score": round(c.semantic_score, 3),
            "rerank_score": round(c.rerank_score, 3),
            "rank": c.final_rank,
        }
        for c in chunks
    ]
    return result


@app.post("/api/rag/ingest")
async def trigger_ingest(force: bool = False):
    """Trigger local knowledge base ingestion (dev mode)."""
    if not config.knowledge_docs_dir.exists():
        raise HTTPException(status_code=400, detail="Knowledge docs directory not found")

    vs = get_vector_store()
    if force:
        # In production, would delete and recreate
        pass

    chunks = ingest_directory(str(config.knowledge_docs_dir), config.chunk_size, config.chunk_overlap)
    chunks = clean_chunks(chunks)
    emb_client = get_embedding_client()

    texts = [c.text for c in chunks]
    metadatas = [c.metadata for c in chunks]
    ids = [f"chunk_{i}" for i in range(len(chunks))]
    embeddings = emb_client.embed_batch(texts)

    vs.add(ids=ids, texts=texts, metadatas=metadatas, embeddings=embeddings)

    return {"status": "ok", "chunks": len(chunks), "total_in_store": vs.count()}


@app.get("/api/rag/sources")
async def list_sources():
    vs = get_vector_store()
    # This works for ChromaDB; for Milvus would need a different query
    if hasattr(vs, '_collection'):
        results = vs._collection.get(include=["documents", "metadatas"], limit=100)
        sources = []
        for i, (doc, meta) in enumerate(zip(results["documents"], results["metadatas"])):
            sources.append({
                "id": results["ids"][i],
                "source": meta.get("source", ""),
                "section": meta.get("section", ""),
                "preview": doc[:120] + "..." if len(doc) > 120 else doc,
            })
        return {"total": vs.count(), "sources": sources}
    return {"total": vs.count(), "sources": []}


@app.get("/api/conversation/{session_id}")
async def get_conversation(session_id: str):
    """Get conversation history from short-term memory (DynamoDB)."""
    db = get_dynamodb_client()
    messages = db.get_conversation(session_id)
    return {"session_id": session_id, "messages": messages, "count": len(messages)}


@app.delete("/api/conversation/{session_id}")
async def clear_conversation(session_id: str):
    """Clear conversation history."""
    db = get_dynamodb_client()
    db.clear_conversation(session_id)
    return {"status": "ok", "session_id": session_id}


if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting InsightCard RAG Pro at http://{config.host}:{config.port}")
    uvicorn.run(app, host=config.host, port=config.port)
