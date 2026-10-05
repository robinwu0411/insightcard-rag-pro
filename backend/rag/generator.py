"""RAG generator — produces InsightCard recommendations via LLM or template fallback.

Two modes:
  1. OpenAI mode (if OPENAI_API_KEY set): streams real LLM tokens via GPT-4o-mini
  2. Bedrock mode (if LLM_PROVIDER=bedrock): uses AWS Bedrock (Claude)
  3. Template mode (default): extracts actions from retrieved chunks, streams word-by-word
"""
import re
import time
import logging
from typing import Generator

from shared.config import config
from backend.tools.metric_tools import MetricResult
from backend.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)


def _gap_pct(metric: MetricResult) -> float:
    gap = metric.value - metric.goal
    return abs((gap / metric.goal) * 100) if metric.goal else 0.0


def _status_label(metric: MetricResult) -> tuple:
    gap = _gap_pct(metric)
    if metric.goal_type == "higher_better":
        if metric.value >= metric.goal:
            return ("On Track", "green")
        elif gap <= 5:
            return ("At Risk", "yellow")
        elif gap <= 15:
            return ("Off Track", "orange")
        else:
            return ("Critical", "red")
    else:
        if metric.value <= metric.goal:
            return ("On Track", "green")
        elif gap <= 5:
            return ("At Risk", "yellow")
        elif gap <= 15:
            return ("Off Track", "orange")
        else:
            return ("Critical", "red")


def _alert_level(metric: MetricResult) -> str:
    gap = _gap_pct(metric)
    if gap <= 5:
        return "yellow"
    elif gap <= 15:
        return "orange"
    else:
        return "red"


# --- Template-based generator (no API key required) ---

def _extract_actions_from_chunks(chunks: list[RetrievedChunk], metric: MetricResult) -> list[str]:
    alert = _alert_level(metric)
    actions = []
    seen = set()
    numbered_action = re.compile(r"^\d+\.\s+(.+)", re.MULTILINE)
    action_keyword = re.compile(r"(?:Action:|Solution:|Tip:)\s*(.+)", re.IGNORECASE)

    for chunk in chunks:
        text = chunk.text
        section = chunk.metadata.get("section", "")
        alert_match = re.search(r"(\d+)-(\d+)%?\s*\((\w+)\s+Alert\)", section)
        if alert_match:
            low, high = int(alert_match.group(1)), int(alert_match.group(2))
            gap = _gap_pct(metric)
            if not (low <= gap <= high):
                continue

        for m in numbered_action.finditer(text):
            action_text = m.group(1).strip()
            if len(action_text) > 15 and action_text.lower() not in seen:
                seen.add(action_text.lower())
                actions.append(action_text)

        for m in action_keyword.finditer(text):
            action_text = m.group(1).strip()
            if len(action_text) > 10 and action_text.lower() not in seen:
                seen.add(action_text.lower())
                actions.append(action_text)

    return actions[:5]


def _extract_levers_from_chunks(chunks: list[RetrievedChunk], metric: MetricResult) -> list[str]:
    levers = []
    seen = set()
    metric_keywords = {
        "net_ppm": ["cost", "margin", "trade term", "pricing", "promotion", "damage", "mix"],
        "revenue": ["in-stock", "advertising", "conversion", "glance", "asin", "promotion"],
        "cr": ["price", "detail page", "review", "image", "buy box", "availability"],
        "in_stock_rate": ["forecast", "lead time", "safety stock", "po", "supply", "order"],
        "glance_views": ["advertising", "keyword", "search", "brand store", "traffic", "campaign"],
    }
    keywords = metric_keywords.get(metric.metric_name, [])

    for chunk in chunks:
        for line in chunk.text.split("\n"):
            line_lower = line.lower().strip()
            if any(kw in line_lower for kw in keywords) and 15 < len(line.strip()) < 120:
                clean = line.strip().lstrip("-*• ").rstrip()
                if clean.lower() not in seen and not clean.startswith("#"):
                    seen.add(clean.lower())
                    levers.append(clean)
    return levers[:4]


def _build_template_recommendation(metric: MetricResult, chunks: list[RetrievedChunk],
                                   vendor_name: str = "Toshiba HL Japan") -> str:
    status, level = _status_label(metric)
    gap = _gap_pct(metric)
    actions = _extract_actions_from_chunks(chunks, metric)
    levers = _extract_levers_from_chunks(chunks, metric)

    top_contribs = "\n".join(
        f"  - {c.name}: {c.value} {metric.unit} ({c.contribution_pct:+.1f}%)"
        for c in metric.contributors_top
    ) if metric.contributors_top else "  (no data)"

    bottom_contribs = "\n".join(
        f"  - {c.name}: {c.value} {metric.unit} ({c.contribution_pct:+.1f}%)"
        for c in metric.contributors_bottom
    ) if metric.contributors_bottom else "  (no data)"

    lines = []
    lines.append(f"## Performance Summary\n")
    lines.append(
        f"{vendor_name}'s {metric.display_name} is currently **{status}**. "
        f"The QTD value of {metric.value} {metric.unit} is {gap:.1f}% below the "
        f"JBP goal of {metric.goal} {metric.unit}, with a {metric.trend_direction} "
        f"trend of {abs(metric.trend_pct):.1f}% week-over-week.\n"
    )
    lines.append(f"## Key Findings\n")
    lines.append(f"**Top Contributors:**")
    lines.append(top_contribs)
    lines.append(f"\n**Underperforming Contributors:**")
    lines.append(bottom_contribs)
    lines.append("")
    lines.append(f"## Knowledge Base References\n")
    for i, chunk in enumerate(chunks[:3]):
        source = chunk.metadata.get("source", "unknown")
        section = chunk.metadata.get("section", "")
        lines.append(f"  [{i+1}] {source} > {section} (semantic: {chunk.semantic_score:.2f}, rerank: {chunk.rerank_score:.2f})")
    lines.append("")
    lines.append(f"## Recommended Actions\n")
    if actions:
        lines.append("Based on retrieved growth playbooks and remediation guides:\n")
        for i, action in enumerate(actions, 1):
            lines.append(f"  {i}. {action}")
    else:
        lines.append("  (No specific playbook actions matched current gap level — manual review recommended.)")
    lines.append("")
    if levers:
        lines.append(f"## Key Levers\n")
        for lever in levers:
            lines.append(f"  - {lever}")
        lines.append("")
    lines.append(f"## Next Steps\n")
    if level == "red":
        lines.append(f"  - **Immediate**: Submit formal recovery plan to BS within 48 hours")
        lines.append(f"  - **This week**: Schedule emergency joint review with BS and vendor")
        lines.append(f"  - **30-day**: Implement top 3 recommended actions with weekly tracking")
    elif level == "orange":
        lines.append(f"  - **This week**: Begin implementing top recommended actions")
        lines.append(f"  - **14-day**: Submit action plan to BS with specific commitments")
        lines.append(f"  - **30-day**: Track metric recovery and adjust strategy as needed")
    elif level == "yellow":
        lines.append(f"  - **This week**: Review contributing factors with internal team")
        lines.append(f"  - **30-day**: Implement preventive actions and monitor trend")
    else:
        lines.append(f"  - Maintain current performance trajectory")
        lines.append(f"  - Focus on sustaining above-goal performance")

    return "\n".join(lines)


# --- OpenAI generator ---

_SYSTEM_PROMPT = """You are a Vendor Performance Insight Agent for Amazon Japan.
You generate structured InsightCard recommendations for vendor performance metrics.

Given:
1. Current metric data (value, goal, gap, trend, contributors)
2. Retrieved knowledge from vendor growth playbooks

Generate a structured recommendation with:
- Performance Summary: concise status assessment
- Key Findings: what the data shows (contributors, trend)
- Recommended Actions: specific, actionable steps grounded in the retrieved playbooks
- Next Steps: timeline and ownership

Reference specific playbook sections when making recommendations.
Keep it concise (300-400 words). Use markdown formatting.
"""


def _build_openai_prompt(metric: MetricResult, chunks: list[RetrievedChunk]) -> str:
    status, level = _status_label(metric)
    gap = _gap_pct(metric)
    metric_context = f"""METRIC: {metric.display_name}
VALUE: {metric.value} {metric.unit}
GOAL: {metric.goal} {metric.unit} ({metric.goal_type})
GAP: {gap:.1f}% below goal
TREND: {metric.trend_direction} {abs(metric.trend_pct):.1f}% WoW
PERIOD: {metric.period}

TOP CONTRIBUTORS:
{chr(10).join(f'- {c.name}: {c.value} {metric.unit} ({c.contribution_pct:+.1f}%)' for c in metric.contributors_top)}

UNDERPERFORMING CONTRIBUTORS:
{chr(10).join(f'- {c.name}: {c.value} {metric.unit} ({c.contribution_pct:+.1f}%)' for c in metric.contributors_bottom)}
"""
    context = "\n\n".join([
        f"[Source: {c.metadata.get('source', '')} > {c.metadata.get('section', '')}]\n{c.text}"
        for c in chunks
    ])
    return f"""{metric_context}

RETRIEVED KNOWLEDGE:
{context}

Generate a structured InsightCard recommendation for this vendor's {metric.display_name} performance.
Ground your recommendations in the retrieved playbook content above."""


def generate_with_openai(metric: MetricResult, chunks: list[RetrievedChunk]) -> Generator[str, None, None]:
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed, falling back to template")
        yield from generate_with_template(metric, chunks)
        return

    client = OpenAI(api_key=config.openai_api_key)
    prompt = _build_openai_prompt(metric, chunks)

    stream = client.chat.completions.create(
        model=config.llm_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        stream=True,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content


def generate_with_bedrock(metric: MetricResult, chunks: list[RetrievedChunk]) -> Generator[str, None, None]:
    """AWS Bedrock generator (Claude)."""
    try:
        import boto3
        import json
    except ImportError:
        yield from generate_with_template(metric, chunks)
        return

    client = boto3.client("bedrock-runtime", region_name=config.aws_region)
    prompt = _build_openai_prompt(metric, chunks)
    full_prompt = f"{_SYSTEM_PROMPT}\n\n{prompt}"

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": config.llm_max_tokens,
        "temperature": config.llm_temperature,
        "messages": [{"role": "user", "content": full_prompt}],
    })

    response = client.invoke_model_with_response_stream(
        modelId="anthropic.claude-3-haiku-20240307-v1:0",
        body=body,
    )

    for event in response.get("stream", []):
        if "chunk" in event:
            chunk_data = json.loads(event["chunk"]["bytes"])
            if chunk_data.get("type") == "content_block_delta":
                delta = chunk_data.get("delta", {})
                if delta.get("text"):
                    yield delta["text"]


def generate_with_template(metric: MetricResult, chunks: list[RetrievedChunk],
                           vendor_name: str = "Toshiba HL Japan") -> Generator[str, None, None]:
    full_text = _build_template_recommendation(metric, chunks, vendor_name)
    words = full_text.split(" ")
    i = 0
    while i < len(words):
        batch = words[i:i + 3]
        yield " ".join(batch) + (" " if i + 3 < len(words) else "")
        i += 3
        time.sleep(0.03)


def generate_recommendation_stream(metric: MetricResult, chunks: list[RetrievedChunk]) -> Generator[str, None, None]:
    """Route to the configured LLM provider or fall back to template."""
    if config.llm_provider == "bedrock" and config.aws_region:
        yield from generate_with_bedrock(metric, chunks)
    elif config.use_openai:
        yield from generate_with_openai(metric, chunks)
    else:
        yield from generate_with_template(metric, chunks)
