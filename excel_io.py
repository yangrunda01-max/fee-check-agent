"""Excel reading with flexible column name mapping."""

import pandas as pd

COLUMN_MAPPINGS = {
    "school": ["school", "university", "institution", "college", "uni", "provider"],
    "program": ["program", "programme", "course", "course name", "program name",
                 "qualification", "degree", "专业", "专业名称", "major"],
    "url": ["url", "link", "website", "webpage", "page", "program url",
            "course url", "专业url", "专业链接", "apply url"],
    "tuition": ["tuition", "fee", "fees", "tuition fee", "tuition fees",
                "international fee", "course fee", "学费", "fee (annual)"],
}


def _find_column(columns: pd.Index, candidates: list[str]) -> str | None:
    cols_lower = {c.strip().lower() for c in columns}
    for col in columns:
        if col.strip().lower() in candidates:
            return col
    for cand in candidates:
        for col in columns:
            if cand in col.strip().lower():
                return col
    return None


def read_programs(filepath: str) -> list[dict]:
    """Read Excel file and return a list of program dicts with normalized keys."""
    df = pd.read_excel(filepath, dtype=str)
    df = df.where(pd.notna(df), None)

    col_map = {}
    for key, candidates in COLUMN_MAPPINGS.items():
        found = _find_column(df.columns, candidates)
        if found:
            col_map[found] = key

    if "url" not in col_map.values():
        raise ValueError("Cannot find a URL column in the spreadsheet. "
                         "Expected column names like: url, link, website, 专业url")
    if "tuition" not in col_map.values():
        raise ValueError("Cannot find a tuition/fee column in the spreadsheet.")

    records = []
    for _, row in df.iterrows():
        rec = {}
        for orig_col, std_key in col_map.items():
            rec[std_key] = row[orig_col]
        records.append(rec)

    return records
