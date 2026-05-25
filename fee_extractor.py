"""Fee information extraction using DeepSeek API."""

import json
import time
import logging
from pathlib import Path

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from config import DEEPSEEK_PRO_MODEL, DEEPSEEK_ENABLE_PRO_FALLBACK
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


def _is_found_fee(data: dict) -> bool:
    """Check if the extraction result contains a clear international tuition fee."""
    confidence = data.get("confidence", "")
    amount = data.get("amount", "")
    is_international = data.get("is_international", False)
    is_domestic = data.get("is_domestic", False)
    return (
        confidence in ("high", "medium")
        and bool(amount)
        and is_international is True
        and is_domestic is not True
    )


def _call_model(model: str, page_text: str, source_url: str) -> dict:
    """Call DeepSeek API with the specified model and return parsed result."""
    if not DEEPSEEK_API_KEY:
        return _fallback_result(source_url, "DeepSeek API key not configured")

    truncated = page_text[:MAX_PAGE_TEXT_LENGTH]
    system_prompt = _load_prompt()
    user_prompt = (
        f"Extract international tuition fee information from this webpage.\n"
        f"Webpage URL: {source_url}\n\n"
        f"Return ONLY a short valid JSON object. Do NOT use markdown, code blocks, or backticks.\n"
        f"Do NOT include any explanation text before or after the JSON.\n"
        f"Keep source_quote under 60 characters — a short quote, not the full paragraph.\n"
        f"Keep note under 80 characters. Do NOT copy the entire page text.\n\n"
        f"---PAGE CONTENT START---\n{truncated}\n---PAGE CONTENT END---"
    )

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client = _get_client()
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=3000,
            )
            content = resp.choices[0].message.content or ""
            finish_reason = resp.choices[0].finish_reason
            if finish_reason == "length":
                preview = content[:300]
                note = f"API response truncated, JSON may be incomplete. Raw response: {preview}"
                return _fallback_result(source_url, note)
            return _parse_response(content, source_url)

        except Exception as e:
            last_error = str(e)
            logger.warning("DeepSeek API attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    return _fallback_result(source_url, f"API error: {last_error}")


def extract_fee(page_text: str, source_url: str) -> dict:
    """Send webpage text to DeepSeek API and return parsed fee information dict.

    Step 1: Call DEEPSEEK_MODEL (deepseek-v4-flash).
    Step 2: If flash finds a clear international fee, return its result directly.
    Step 3: If flash does NOT find a clear fee and DEEPSEEK_ENABLE_PRO_FALLBACK
            is enabled, call DEEPSEEK_PRO_MODEL (deepseek-v4-pro) as fallback.
    Step 4: If pro fallback is disabled, return the flash result as-is.

    Link ranking (link_ranker.py) always uses only DEEPSEEK_MODEL — the pro
    fallback is exclusively for tuition fee extraction.
    """
    if not DEEPSEEK_API_KEY:
        return _fallback_result(source_url, "DeepSeek API key not configured")

    # Step 1: Call flash model
    flash_result = _call_model(DEEPSEEK_MODEL, page_text, source_url)

    # Step 2: Flash found a clear fee — return immediately
    if _is_found_fee(flash_result):
        return flash_result

    # Step 3: Flash didn't find a clear fee — try pro fallback if enabled
    if DEEPSEEK_ENABLE_PRO_FALLBACK:
        pro_result = _call_model(DEEPSEEK_PRO_MODEL, page_text, source_url)

        flash_note = (flash_result.get("note") or "").strip()
        pro_note = (pro_result.get("note") or "").strip()

        if _is_found_fee(pro_result):
            # Pro found a clear fee
            pro_result["note"] = (
                "Flash 未找到明确学费，已使用 deepseek-v4-pro 兜底"
                + (f"；{pro_note}" if pro_note else "")
            )
            return pro_result
        else:
            # Pro also didn't find a clear fee — return flash result with annotation
            flash_result["note"] = (
                "Flash 未找到明确学费，Pro 兜底也未找到"
                + (f"；{flash_note}" if flash_note else "")
            )
            return flash_result

    # Step 4: Pro fallback not enabled — return flash result as-is
    return flash_result


def _parse_response(content: str, source_url: str) -> dict:
    """Parse the API response. With JSON mode enabled, response should be valid JSON."""
    _ALLOWED = {"amount", "currency", "unit", "confidence",
                "is_international", "is_domestic", "source_quote", "note"}
    raw = content.strip()
    if raw:
        try:
            data = json.loads(raw)
            data = {k: v for k, v in data.items() if k in _ALLOWED}
            data.setdefault("source_url", source_url)
            return data
        except json.JSONDecodeError:
            pass
    note = "Failed to parse API response"
    if raw:
        preview = raw[:300]
        note += f": {preview}"
    return _fallback_result(source_url, note)


def _fallback_result(source_url: str, note: str) -> dict:
    return {
        "source_quote": "",
        "currency": "",
        "amount": "",
        "unit": "",
        "is_international": False,
        "is_domestic": False,
        "source_url": source_url,
        "confidence": "none",
        "note": note,
    }
