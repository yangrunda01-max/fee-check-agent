"""Write processed results to Excel output files."""

import math
from pathlib import Path
import pandas as pd

from config import OUTPUT_DIR

OUTPUT_COLUMNS = [
    "学校",
    "专业名称",
    "专业URL",
    "学费",
    "原学费",
    "官网核验学费",
    "核验状态",
    "学费来源链接",
    "官网原文引用",
    "备注",
]


def safe_text(value) -> str:
    """Convert any value to a safe non-empty string for concatenation.

    - None → ""
    - NaN → ""
    - float 40500.0 → "40500" (strips trailing .0)
    - "nan"/"none"/"NaT" → ""
    """
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


FALLBACK_NOTE = "官网核验学费已按原学费暂填，需人工复核"
_FALLBACK_DEDUP_PHRASE = "官网核验学费已按原学费暂填"
_FALLBACK_KEYWORDS = ("无有效URL", "页面失败", "反爬", "JS", "动态页面", "未找到明确官网学费", "需人工复核")


def _apply_final_fallback(records: list[dict]) -> None:
    """Final safety net before writing Excel.

    Conditions that trigger fallback:
      - status in (页面失败, 未找到, 需人工复核)
      - notes contains a fallback keyword
      - verified_tuition is empty

    Actions:
      - tuition ← original_tuition
      - verified_tuition: keep extracted data if non-empty, ensure (需复核) marker
      - Only fallback to original + (需复核) when verified_tuition is empty
      - 页面失败 → 需人工复核
      - append FALLBACK_NOTE to notes (if not already present)
    """
    for rec in records:
        status = rec.get("status", "")
        notes = rec.get("notes", "")
        verified = rec.get("verified_tuition", "")
        original = rec.get("original_tuition", "")

        conditions_met = (
            status in ("页面失败", "未找到", "需人工复核")
            or any(kw in (notes or "") for kw in _FALLBACK_KEYWORDS)
            or not verified
        )
        if not conditions_met:
            continue

        # Force tuition back to original
        rec["tuition"] = original

        # Preserve extracted verified_tuition if non-empty; only fallback to original when empty
        if not verified:
            original_text = safe_text(original)
            rec["verified_tuition"] = original_text + "（需复核）" if original_text else "（需复核）"
        elif "（需复核）" not in verified:
            rec["verified_tuition"] = verified + "（需复核）"

        # 页面失败 → 需人工复核
        if status == "页面失败":
            rec["status"] = "需人工复核"

        # Append fallback note if not redundant
        if notes and _FALLBACK_DEDUP_PHRASE not in notes:
            rec["notes"] = notes + "；" + FALLBACK_NOTE
        elif not notes:
            rec["notes"] = FALLBACK_NOTE

        # Auto-fill source_url with original program URL if empty (for manual review convenience)
        if not rec.get("source_url"):
            rec["source_url"] = rec.get("url", "")

    # Final blanket check: verified_tuition must never be blank
    for rec in records:
        verified = safe_text(rec.get("verified_tuition", ""))
        if verified:
            continue
        original_text = safe_text(rec.get("original_tuition", ""))
        rec["verified_tuition"] = original_text + "（需复核）" if original_text else "（需复核）"


def _map_col(rec: dict) -> dict:
    """Map internal normalized keys to Chinese output columns."""
    return {
        "学校": rec.get("school", ""),
        "专业名称": rec.get("program", ""),
        "专业URL": rec.get("url", ""),
        "学费": rec.get("tuition", ""),
        "原学费": rec.get("original_tuition", ""),
        "官网核验学费": rec.get("verified_tuition", ""),
        "核验状态": rec.get("status", ""),
        "学费来源链接": rec.get("source_url", ""),
        "官网原文引用": rec.get("source_quote", ""),
        "备注": rec.get("notes", ""),
    }


def write_outputs(records: list[dict]) -> tuple[str, str]:
    """Write two Excel files: all records and only updated records."""
    _apply_final_fallback(records)

    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    mapped = [_map_col(r) for r in records]
    df_all = pd.DataFrame(mapped, columns=OUTPUT_COLUMNS)

    checked_path = out_dir / "programs_fee_checked.xlsx"
    df_all.to_excel(checked_path, index=False, engine="openpyxl")

    updated = [r for r in records if r.get("status") == "已更新"]
    if updated:
        mapped_upd = [_map_col(r) for r in updated]
        df_upd = pd.DataFrame(mapped_upd, columns=OUTPUT_COLUMNS)
    else:
        df_upd = pd.DataFrame(columns=OUTPUT_COLUMNS)

    updated_path = out_dir / "programs_fee_updated.xlsx"
    df_upd.to_excel(updated_path, index=False, engine="openpyxl")

    return str(checked_path), str(updated_path)
