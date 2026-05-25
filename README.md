# fee-check-agent

Automatically verify international tuition fees against university program websites.

## How it works

1. Reads `input/programs.xlsx` containing program URLs and their current tuition data.
2. For each program, fetches the program page and uses DeepSeek API to extract the displayed international tuition fee.
3. If the program page has no fee, follows fee-related links (Fees / Tuition / International fees / Course fees / Cost) — up to 2 levels deep.
4. Compares the extracted fee with the original data and produces two output files.

## Usage

```bash
# Activate venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Copy and edit .env
cp .env.example .env
# Edit .env and set your DEEPSEEK_API_KEY

# Run
python main.py input/programs.xlsx

# Process only first 5 rows
python main.py input/programs.xlsx --limit 5
```

## Input format

An Excel file with columns for: school/university, program/course name, URL/link, and tuition/fee.
Column names are auto-detected (supports English and Chinese headers).

## Output

| File | Description |
|------|-------------|
| `output/programs_fee_checked.xlsx` | All programs with verification results |
| `output/programs_fee_updated.xlsx` | Only programs where the fee was auto-updated |

### Status values

- **已更新** — Fee automatically updated with verified website data
- **无需更新** — Website fee matches original data
- **需人工复核** — Needs manual review (low confidence, unit mismatch, or fee calculator)
- **未找到** — No international tuition fee found on any page
- **页面失败** — Page could not be loaded

## Project structure

```
├── main.py               # Entry point
├── config.py             # Environment configuration
├── excel_io.py           # Excel reading with flexible column mapping
├── web_fetcher.py        # HTTP requests and link extraction
├── link_ranker.py        # Fee-related link scoring
├── fee_extractor.py      # DeepSeek API fee extraction
├── fee_compare.py        # Fee comparison and status determination
├── excel_writer.py       # Output Excel file generation
├── prompts/
│   └── extract_fee_prompt.txt  # AI prompt for fee extraction
├── input/                # Place input Excel files here
├── output/               # Output files are written here
└── cache/                # Cache directory
```
