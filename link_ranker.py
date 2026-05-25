"""Link ranking logic for finding fee-related pages."""

import json
import logging
import time
from pathlib import Path

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, MAX_RETRIES
from web_fetcher import score_fee_link, is_skip_link, is_fee_calculator_link

logger = logging.getLogger(__name__)

_RANK_PROMPT_CACHE: str | None = None


def _load_rank_prompt() -> str:
    global _RANK_PROMPT_CACHE
    if _RANK_PROMPT_CACHE is None:
        prompt_path = Path(__file__).parent / "prompts" / "rank_links_prompt.txt"
        _RANK_PROMPT_CACHE = prompt_path.read_text(encoding="utf-8")
    return _RANK_PROMPT_CACHE


def _get_deepseek_client() -> OpenAI:
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def rank_fee_links_with_deepseek(
    candidate_links: list[dict],
    original_url: str,
    page_text: str | None = None,
    max_links: int = 15,
) -> list[dict]:
    """Use DeepSeek to rank candidate fee links by relevance.

    DeepSeek can ONLY select from the provided candidate_links list.
    Returns a list of ranked link dicts (sorted by relevance), or empty list on failure.
    """
    if not DEEPSEEK_API_KEY or not candidate_links:
        return _fallback_rank(candidate_links)

    # Limit number of candidates to avoid token overflow
    candidates = candidate_links[:max_links]
    candidate_urls = {link["url"] for link in candidates}

    links_text = "\n".join(
        f"{i+1}. {link['url']}"
        + (f" — {link['text'][:120]}" if link.get("text") else "")
        for i, link in enumerate(candidates)
    )

    system_prompt = _load_rank_prompt()
    user_prompt = (
        f"Original page URL: {original_url}\n\n"
        f"Candidate links (rank these by relevance for international tuition fee info):\n{links_text}"
    )

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client = _get_deepseek_client()
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=2048,
            )
            content = resp.choices[0].message.content or ""
            ranked = _parse_rank_response(content, candidate_urls)
            if ranked:
                return ranked
            # If empty but no error, treat as "no relevant links"
            return []

        except Exception as e:
            last_error = str(e)
            logger.warning("DeepSeek rank attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    logger.warning("DeepSeek ranking failed after retries: %s", last_error)
    return _fallback_rank(candidates)


def _parse_rank_response(content: str, valid_urls: set) -> list[dict]:
    """Parse DeepSeek ranking response and validate URLs against valid_urls."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        start = 1 if content.startswith("```json") else 1
        end = -1 if lines[-1].strip() == "```" else len(lines)
        content = "\n".join(lines[start:end]).strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse rank response JSON")
        return []

    best_links = data.get("best_links", [])
    if not isinstance(best_links, list):
        return []

    validated = []
    for entry in best_links:
        url = entry.get("url", "")
        if url not in valid_urls:
            logger.warning("DeepSeek returned URL not in candidate list, ignoring: %s", url)
            continue
        validated.append({
            "url": url,
            "reason": entry.get("reason", ""),
            "confidence": entry.get("confidence", "low"),
        })

    return validated


def _fallback_rank(candidates: list[dict]) -> list[dict]:
    """Fallback: return candidates sorted by keyword score if DeepSeek is unavailable."""
    scored = sorted(candidates, key=lambda x: score_fee_link(x["url"], x.get("text", "")), reverse=True)
    return [
        {"url": link["url"], "reason": "fallback keyword ranking", "confidence": "medium"}
        for link in scored
    ]


def rank_links(links: list[dict]) -> list[dict]:
    """Rank links by fee relevance, filtering out skip links. Returns scored links sorted desc."""
    scored = []
    for link in links:
        if is_skip_link(link["url"], link["text"]):
            continue
        score = score_fee_link(link["url"], link["text"])
        if score > 0:
            scored.append({**link, "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def pick_fee_links(links: list[dict], max_count: int = 5) -> list[dict]:
    """Pick the top N most fee-relevant links from a page."""
    ranked = rank_links(links)
    return ranked[:max_count]


def has_calculator_only(links: list[dict]) -> bool:
    """Check if the only fee-related links are fee calculators."""
    ranked = rank_links(links)
    calculator_count = sum(1 for l in ranked if is_fee_calculator_link(l["url"], l["text"]))
    normal_count = len(ranked) - calculator_count
    return normal_count == 0 and calculator_count > 0
