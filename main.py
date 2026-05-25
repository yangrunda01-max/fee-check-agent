"""fee-check-agent: Verify international tuition fees against university websites."""

import argparse
import logging
import sys
from pathlib import Path

from web_fetcher import (
    clean_url, extract_text, extract_links,
    filter_official_links, _fetch_with_delay,
)
from link_ranker import pick_fee_links, has_calculator_only, rank_fee_links_with_deepseek
from fee_extractor import extract_fee
from fee_compare import compare, _append_review_suffix
from excel_io import read_programs
from excel_writer import write_outputs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fee-check")


def process_program(
    program: dict,
    index: int,
    total: int,
) -> dict:
    """Process a single program record through the fee-check pipeline."""
    school = program.get("school", "")
    prog_name = program.get("program", "")
    raw_url = program.get("url", "")
    tuition = program.get("tuition", "")

    # Clean the URL
    url = clean_url(raw_url)

    logger.info(
        "--- [%d/%d] %s | %s | %s",
        index, total, school, prog_name, url or "(无URL)"
    )

    result = {
        "school": school,
        "program": prog_name,
        "url": url or "",
        "tuition": tuition,
        "original_tuition": tuition,
        "verified_tuition": "",
        "status": "",
        "source_url": "",
        "notes": "",
    }

    if not url:
        result["status"] = "页面失败"
        result["notes"] = "无有效URL"
        logger.info("  -> 跳过: 无有效URL")
        return result

    last_fetch_time = 0.0

    # ========================================================================
    # Level 0: fetch the program page and try to extract fee from its text
    # ========================================================================
    html, soup, last_fetch_time, fetch_status, fetch_notes = _fetch_with_delay(url, last_fetch_time)
    if soup is None:
        result["verified_tuition"] = _append_review_suffix(result["original_tuition"])
        result["status"] = "需人工复核"
        result["notes"] = "疑似反爬或 JS 动态页面，官网核验学费已按原学费暂填，需人工打开官网核验"
        logger.info("  -> %s: %s", result["status"], result["notes"])
        return result

    page_text = extract_text(soup)
    fetch_warning = fetch_status
    fetch_warning_notes = fetch_notes
    if fetch_warning:
        logger.info("  [Level 0] 已获取页面(带警告): %s (%d chars) — %s",
                    url, len(page_text), fetch_notes)
    else:
        logger.info("  [Level 0] 已获取页面: %s (%d chars)", url, len(page_text))

    # Try to extract fee directly from the program page
    extracted = extract_fee(page_text, url)
    logger.info("  [Extract] confidence=%s, fee=%s %s",
                extracted.get("confidence"), extracted.get("currency"), extracted.get("amount"))

    # Save the extraction note (e.g., Pro fallback info) for later use
    extraction_note = extracted.get("note", "")

    if extracted.get("confidence") in ("high", "medium"):
        result.update(_apply_extraction(result, extracted))
        logger.info("  -> %s: %s", result["status"], result.get("notes", ""))
        return result

    # ========================================================================
    # Extract links from the page; keep only official (same-school-domain) links
    # ========================================================================
    all_links = extract_links(soup, url)
    official_links = filter_official_links(all_links, url)
    logger.info("  [Links] all=%d, official=%d", len(all_links), len(official_links))

    # Pick fee-relevant links from the official subset
    fee_links = pick_fee_links(official_links, max_count=15)
    logger.info("  [Fee Links] %d official fee-related links found", len(fee_links))

    # --- No official fee-relevant links at all ---
    if not fee_links:
        if has_calculator_only(official_links):
            result["verified_tuition"] = _append_review_suffix(result["original_tuition"])
            result["status"] = "需人工复核"
            result["notes"] = "官网需通过 fee calculator 查询，官网核验学费已按原学费暂填，需人工复核"
            logger.info("  -> 仅找到 fee calculator")
        elif fetch_warning:
            result["verified_tuition"] = _append_review_suffix(result["original_tuition"])
            result["status"] = "需人工复核"
            result["notes"] = "疑似反爬或 JS 动态页面，官网核验学费已按原学费暂填，需人工打开官网核验"
            logger.info("  -> %s: %s", fetch_warning, fetch_warning_notes)
        else:
            result["verified_tuition"] = _append_review_suffix(result["original_tuition"])
            result["status"] = "未找到"
            result["notes"] = "官网页面及官网候选链接未找到明确 international tuition fee，官网核验学费已按原学费暂填，需人工复核"
            logger.info("  -> 无学费相关链接")
        if extraction_note:
            result["notes"] = (result["notes"] + "；" + extraction_note) if result["notes"] else extraction_note
        return result

    # ========================================================================
    # Level 1: Rank candidate links with DeepSeek (only from official list)
    # ========================================================================
    ranked_links = rank_fee_links_with_deepseek(fee_links, url, page_text)
    logger.info("  [Rank] DeepSeek ranked %d links", len(ranked_links))

    # DeepSeek found no relevant links among candidates
    if not ranked_links:
        _apply_not_found(result, official_links, fetch_warning, fetch_warning_notes, extraction_note)
        logger.info("  -> %s: %s", result["status"], result.get("notes", ""))
        return result

    # Try each ranked link in order
    for link_entry in ranked_links:
        link_url = link_entry["url"]
        link_reason = link_entry.get("reason", "")
        logger.info("  [Level 1] 打开链接: %s (%s)", link_url, link_reason)

        html2, soup2, last_fetch_time, _, _ = _fetch_with_delay(link_url, last_fetch_time)
        if soup2 is None:
            logger.info("    -> 打开失败，跳过")
            continue

        text2 = extract_text(soup2)
        extracted2 = extract_fee(text2, link_url)
        logger.info("    [Extract] confidence=%s, fee=%s %s",
                    extracted2.get("confidence"), extracted2.get("currency"), extracted2.get("amount"))

        if extracted2.get("confidence") in ("high", "medium"):
            result.update(_apply_extraction(result, extracted2))
            logger.info("  -> %s: %s", result["status"], result.get("notes", ""))
            return result

        # ====================================================================
        # Level 2: from a low-confidence Level-1 page, try inner official links
        # ====================================================================
        if extracted2.get("confidence") == "low":
            inner_links = extract_links(soup2, link_url)
            inner_official = filter_official_links(inner_links, link_url)
            inner_fee = pick_fee_links(inner_official, max_count=10)

            if inner_fee:
                inner_ranked = rank_fee_links_with_deepseek(inner_fee, link_url, text2)
                logger.info("    [Level 2] DeepSeek ranked %d inner links", len(inner_ranked))

                for inner_entry in inner_ranked:
                    il_url = inner_entry["url"]
                    logger.info("    [Level 2] 打开链接: %s", il_url)

                    html3, soup3, last_fetch_time, _, _ = _fetch_with_delay(il_url, last_fetch_time)
                    if soup3 is None:
                        continue

                    text3 = extract_text(soup3)
                    extracted3 = extract_fee(text3, il_url)
                    logger.info("      [Extract] confidence=%s, fee=%s %s",
                                extracted3.get("confidence"), extracted3.get("currency"),
                                extracted3.get("amount"))

                    if extracted3.get("confidence") in ("high", "medium"):
                        result.update(_apply_extraction(result, extracted3))
                        logger.info("  -> %s: %s", result["status"], result.get("notes", ""))
                        return result

    # ========================================================================
    # Nothing found after all traversals — apply fallback rules
    # ========================================================================
    _apply_not_found(result, official_links, fetch_warning, fetch_warning_notes, extraction_note)
    logger.info("  -> %s: %s", result["status"], result.get("notes", ""))
    return result


def _apply_not_found(
    result: dict,
    official_links: list[dict],
    fetch_warning: str | None,
    fetch_warning_notes: str | None,
    extraction_note: str = "",
) -> None:
    """Apply the appropriate fallback status when no fee is found after all link traversals.

    Priority: fee calculator > anti-scrape warning > not found
    """
    if has_calculator_only(official_links):
        result["verified_tuition"] = _append_review_suffix(result["original_tuition"])
        result["status"] = "需人工复核"
        result["notes"] = "官网需通过 fee calculator 查询，官网核验学费已按原学费暂填，需人工复核"
    elif fetch_warning:
        result["verified_tuition"] = _append_review_suffix(result["original_tuition"])
        result["status"] = "需人工复核"
        result["notes"] = "疑似反爬或 JS 动态页面，官网核验学费已按原学费暂填，需人工打开官网核验"
    else:
        result["verified_tuition"] = _append_review_suffix(result["original_tuition"])
        result["status"] = "未找到"
        result["notes"] = "官网页面及官网候选链接未找到明确 international tuition fee，官网核验学费已按原学费暂填，需人工复核"
    if extraction_note:
        result["notes"] = (result["notes"] + "；" + extraction_note) if result["notes"] else extraction_note


def _apply_extraction(result: dict, extracted: dict) -> dict:
    """Apply extracted fee data to result record via comparison logic."""
    compared = compare(result.get("original_tuition", ""), extracted)
    return compared


def main():
    parser = argparse.ArgumentParser(
        description="fee-check-agent: 核验官网国际生学费"
    )
    parser.add_argument(
        "input_file",
        help="Excel 输入文件路径 (例如 input/programs.xlsx)",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="仅处理前 N 行 (默认: 全部处理)",
    )
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        logger.error("输入文件不存在: %s", input_path)
        sys.exit(1)

    logger.info("读取输入文件: %s", input_path)
    programs = read_programs(str(input_path))
    logger.info("共读取 %d 条记录", len(programs))

    if args.limit > 0:
        programs = programs[:args.limit]
        logger.info("限制处理前 %d 条", args.limit)

    results = []
    for i, prog in enumerate(programs, 1):
        rec = process_program(prog, i, len(programs))
        results.append(rec)

    checked_path, updated_path = write_outputs(results)

    updated_count = sum(1 for r in results if r.get("status") == "已更新")
    no_update_count = sum(1 for r in results if r.get("status") == "无需更新")
    manual_count = sum(1 for r in results if r.get("status") == "需人工复核")
    not_found_count = sum(1 for r in results if r.get("status") == "未找到")
    failed_count = sum(1 for r in results if r.get("status") == "页面失败")

    logger.info("=" * 50)
    logger.info("处理完成!")
    logger.info("  全部记录:  %s", str(checked_path))
    logger.info("  已更新:    %s", str(updated_path))
    logger.info("  统计: 已更新=%d, 无需更新=%d, 需人工复核=%d, 未找到=%d, 页面失败=%d",
                updated_count, no_update_count, manual_count, not_found_count, failed_count)


if __name__ == "__main__":
    main()
