"""Fee information extraction using DeepSeek API."""

import json
import time
import logging
from pathlib import Path

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from config import MAX_RETRIES, MAX_PAGE_TEXT_LENGTH

logger = logging.getLogger(__name__)

_PROMPT_CACHE: str | None = None


def _load_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        prompt_path = Path(__file__).parent / "prompts" / "extract_fee_prompt.txt"
        _PROMPT_CACHE = prompt_path.read_text(encoding="utf-8")
    return _PROMPT_CACHE


def _get_client() -> OpenAI:
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def extract_fee(page_text: str, source_url: str) -> dict:
    """Send webpage text to DeepSeek API and return parsed fee information dict."""
    if not DEEPSEEK_API_KEY:
        return _fallback_result(source_url, "DeepSeek API key not configured")

    truncated = page_text[:MAX_PAGE_TEXT_LENGTH]
    system_prompt = _load_prompt()
    user_prompt = (
        f"Extract international tuition fee information from this webpage.\n"
        f"Webpage URL: {source_url}\n\n"
        f"---PAGE CONTENT START---\n{truncated}\n---PAGE CONTENT END---"
    )

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client = _get_client()
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=1024,
            )
            content = resp.choices[0].message.content or ""
            return _parse_response(content, source_url)

        except Exception as e:
            last_error = str(e)
            logger.warning("DeepSeek API attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    return _fallback_result(source_url, f"API error: {last_error}")


def _parse_response(content: str, source_url: str) -> dict:
    """Parse the API response, handling markdown code fences."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        start = 1 if content.startswith("```json") else 1
        end = -1 if lines[-1].strip() == "```" else len(lines)
        content = "\n".join(lines[start:end]).strip()

    try:
        data = json.loads(content)
        data.setdefault("source_url", source_url)
        return data
    except json.JSONDecodeError:
        return _fallback_result(source_url, f"Failed to parse API response: {content[:200]}")


def _fallback_result(source_url: str, note: str) -> dict:
    return {
        "fee_text": "",
        "currency": "",
        "amount": "",
        "unit": "",
        "is_international": False,
        "is_domestic": False,
        "fee_type": "",
        "year": "",
        "source_url": source_url,
        "confidence": "none",
        "note": note,
    }
