"""Compare extracted fee with original and determine verification status."""

import math
import re
import logging

logger = logging.getLogger(__name__)


def _append_review_suffix(tuition_str: str) -> str:
    """Append （需复核） marker to tuition string for manual review cases."""
    text = _safe_text(tuition_str)
    return text + "（需复核）" if text else "（需复核）"


def _safe_text(value) -> str:
    """Convert any value to a safe non-empty string for concatenation."""
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return str(int(value)) if value == int(value) else str(value)
    text = str(value).strip()
    if text.lower() in ("nan", "none", "nat"):
        return ""
    return text


def _parse_amount(amount_str: str | None) -> float | None:
    """Parse amount string to float. Returns None for ranges or unparseable formats.

    Rejects ranges: X–Y, X-Y, X to Y, X~Y.
    Handles European formats: 1.234,56 -> 1234.56, 1.234.567 -> 1234567, 20.000 -> 20000.
    """
    if not amount_str:
        return None
    text = str(amount_str).strip()
    if not text:
        return None

    # Remove currency symbols and whitespace for analysis
    cleaned = re.sub(r"[£€$¥￥\s]", "", text)
    if not cleaned:
        return None

    # Detect and reject ranges
    if re.match(
        r"^\d+[.,\d\s]*\s*(?:–|—|-|to|~|～)\s*\d+",
        cleaned, re.IGNORECASE
    ):
        return None

    has_comma = "," in cleaned
    has_period = "." in cleaned
    period_count = cleaned.count(".")

    # Detect European format (comma = decimal, period = thousands separator)
    is_european = False

    if has_comma:
        comma_idx = cleaned.rfind(",")
        after_comma = cleaned[comma_idx + 1:]
        if re.match(r"^\d{1,2}$", after_comma):
            is_european = True

    if not is_european and period_count > 1:
        is_european = True

    if not is_european and has_period and not has_comma:
        after_last_dot = cleaned.split(".")[-1]
        if len(after_last_dot) >= 3:
            is_european = True

    if is_european:
        parts = cleaned.split(",")
        if len(parts) == 2:
            integer_part = parts[0].replace(".", "")
            cleaned = integer_part + "." + parts[1]
        else:
            cleaned = cleaned.replace(",", "").replace(".", "")
    else:
        cleaned = cleaned.replace(",", "")

    # Final cleanup — keep only digits and at most one period
    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


def _review_verified(original_str: str, amount: str, currency: str, unit: str) -> str:
    """Build verified_tuition for manual review, preferring extracted data."""
    if amount:
        return _format_fee(amount, currency, unit) + "（需复核）"
    return _append_review_suffix(original_str)


def compare(original_tuition: str | None, extracted: dict | None) -> dict:
    """Compare original tuition with extracted fee info.

    Returns a dict with keys: tuition, original_tuition, verified_tuition,
    status, source_url, notes.
    """
    original_str = _safe_text(original_tuition)
    result = {
        "tuition": original_str,
        "original_tuition": original_str,
        "verified_tuition": "",
        "status": "",
        "source_url": "",
        "notes": "",
    }

    if not extracted:
        result["verified_tuition"] = _append_review_suffix(original_str)
        result["status"] = "未找到"
        result["notes"] = "未找到明确官网学费，官网核验学费已按原学费暂填，需人工复核"
        return result

    source_url = extracted.get("source_url", "")
    confidence = extracted.get("confidence", "none")
    amount = extracted.get("amount", "")
    currency = extracted.get("currency", "")
    unit = extracted.get("unit", "")
    is_international = extracted.get("is_international", False)
    error_type = extracted.get("error_type", "")
    note = extracted.get("note", "")
    source_quote = extracted.get("source_quote", "")
    result["source_quote"] = source_quote

    result["source_url"] = source_url
    result["verified_tuition"] = f"{currency} {amount}".strip() if amount else ""

    # --- Fee calculator check (conservative rule) ---
    if _is_fee_calculator_case(source_url, note):
        result["tuition"] = original_str
        result["verified_tuition"] = _review_verified(original_str, amount, currency, unit)
        result["status"] = "需人工复核"
        result["notes"] = "官网需通过 fee calculator 查询，官网核验学费已按原学费暂填，需人工复核"
        return result

    # --- API / config / parse / truncation failure ---
    if confidence == "none" and (
        error_type in ("NO_API_KEY", "API_ERROR", "PARSE_ERROR", "MODEL_TRUNCATED")
        or ("API" in note or "Failed to parse" in note or "configured" in note)
    ):
        result["verified_tuition"] = _review_verified(original_str, amount, currency, unit)
        result["status"] = "需人工复核"
        _err_msgs = {
            "NO_API_KEY": "DeepSeek API 密钥未配置",
            "API_ERROR": "DeepSeek API 调用异常",
            "PARSE_ERROR": "模型输出 JSON 解析失败",
            "MODEL_TRUNCATED": "模型输出不完整或被截断",
        }
        result["notes"] = _err_msgs.get(error_type, "疑似反爬或 JS 动态页面")
        result["notes"] += "，官网核验学费已按原学费暂填，需人工打开官网核验"
        if note:
            result["notes"] += f"；{note}"
        return result

    # --- No fee found ---
    if confidence == "none":
        result["status"] = "未找到"
        result["verified_tuition"] = _review_verified(original_str, amount, currency, unit)
        result["notes"] = "未找到明确官网学费，官网核验学费已按原学费暂填，需人工复核"
        if note:
            result["notes"] += f"；{note}"
        return result

    # --- Check if domestic ---
    if is_domestic(extracted):
        result["status"] = "需人工复核"
        result["notes"] = "页面仅显示 domestic/local 学费，未找到国际生学费"
        if note:
            result["notes"] += f"；{note}"
        return result

    # --- Build the verified tuition string ---
    verified_str = _format_fee(amount, currency, unit)
    result["verified_tuition"] = verified_str

    # --- High / medium confidence → check strict conditions for auto-update ---
    if confidence in ("high", "medium") and is_international:
        # Strict validation: must have amount, source_url, not domestic
        if not amount:
            result["verified_tuition"] = _append_review_suffix(original_str)
            result["status"] = "需人工复核"
            result["notes"] = "金额为空，无法自动更新"
            if note:
                result["notes"] += f"；{note}"
            return result

        if not source_url:
            result["verified_tuition"] = _review_verified(original_str, amount, currency, unit)
            result["status"] = "需人工复核"
            result["notes"] = "无来源链接，官网核验学费已按原学费暂填，需人工复核"
            if note:
                result["notes"] += f"；{note}"
            return result

        # --- P0-1: Only per_year unit qualifies for auto-update ---
        if unit != "per_year":
            desc = {"total": "全程总价(total)", "per_semester": "学期学费(per_semester)", "per_unit": "学分学费(per_unit)"}.get(unit, f"单位({unit})")
            result["verified_tuition"] = _review_verified(original_str, amount, currency, unit)
            result["status"] = "需人工复核"
            result["notes"] = f"官网{desc}，非年费，需人工复核"
            if note:
                result["notes"] += f"；{note}"
            return result

        # --- P0-2: Unparseable amount (range, invalid format) → manual review ---
        verified_amount = _parse_amount(amount)
        if verified_amount is None:
            result["verified_tuition"] = _review_verified(original_str, amount, currency, unit)
            result["status"] = "需人工复核"
            result["notes"] = "官网金额格式无法解析（如区间金额），需人工复核"
            if note:
                result["notes"] += f"；{note}"
            return result

        if not original_str:
            result["tuition"] = verified_str
            result["status"] = "已更新"
            result["notes"] = "原无学费，已填入官网数据"
            if note:
                result["notes"] += f"；{note}"
            return result

        original_amount = _parse_amount(original_str)
        verified_amount = _parse_amount(amount)

        if original_amount is not None and verified_amount is not None:
            if abs(original_amount - verified_amount) < 0.01:
                result["tuition"] = original_str
                result["status"] = "无需更新"
                result["notes"] = "官网学费与原始数据一致"
                if note:
                    result["notes"] += f"；{note}"
                return result

        # --- P0-2: Either amount unparseable → manual review ---
        if original_amount is None:
            result["verified_tuition"] = _review_verified(original_str, amount, currency, unit)
            result["status"] = "需人工复核"
            result["notes"] = "原始学费金额格式无法解析，需人工复核"
            if note:
                result["notes"] += f"；{note}"
            return result

        # Fee has changed — auto-update
        result["tuition"] = verified_str
        result["status"] = "已更新"
        result["notes"] = f"学费更新: {original_str} → {verified_str}"
        if note:
            result["notes"] += f"；{note}"
        return result

    # --- Low confidence → manual review ---
    result["status"] = "需人工复核"
    result["notes"] = f"置信度低({confidence})，需人工确认"
    if note:
        result["notes"] += f"；{note}"
    return result


def is_domestic(extracted: dict) -> bool:
    """Check if extracted fee is explicitly domestic."""
    if extracted.get("is_domestic"):
        return True
    fee_type = (extracted.get("fee_type") or "").lower()
    return fee_type == "domestic"


def _looks_annual(original: float, verified: float) -> bool:
    """Heuristic: if verified ≈ original * program_years, it's probably total vs annual."""
    ratio = verified / original if original > 0 else 0
    return 1.8 <= ratio <= 6.0


def _is_fee_calculator_case(source_url: str, note: str) -> bool:
    """Check if the source URL or extraction note indicates a fee calculator page.

    Fee calculators require user input (year, campus, student type, course code),
    making batch scraping unreliable — return True to trigger manual review.
    """
    _calculator_triggers = ("calculator", "fee-calculator", "fees-calculator")
    if source_url:
        url_lower = source_url.lower()
        for trigger in _calculator_triggers:
            if trigger in url_lower:
                return True
    if note and "fee calculator" in note.lower():
        return True
    return False


def _format_fee(amount: str, currency: str, unit: str) -> str:
    parts = [s for s in (currency, amount) if s]
    fee = " ".join(parts)
    if unit == "per_year":
        fee += " / 年"
    elif unit == "total":
        fee += " / 全程"
    elif unit == "per_semester":
        fee += " / 学期"
    elif unit == "per_unit":
        fee += " / 学分"
    elif not unit and fee:
        fee += "（单位需复核）"
    return fee
