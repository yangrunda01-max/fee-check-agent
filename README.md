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

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DEEPSEEK_API_KEY` | — | DeepSeek API key (required) |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API base URL |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | Default model for fee extraction and link ranking |
| `DEEPSEEK_PRO_MODEL` | `deepseek-v4-pro` | Pro model used as optional fallback for fee extraction |
| `DEEPSEEK_ENABLE_PRO_FALLBACK` | `false` | Set to `true` to enable Pro fallback when Flash doesn't find a fee |

### Pro fallback behavior

Pro fallback only applies to **tuition fee extraction** (`fee_extractor.py`). Link ranking (`link_ranker.py`) always uses only `DEEPSEEK_MODEL`.

When enabled (`DEEPSEEK_ENABLE_PRO_FALLBACK=true`):
1. Flash model runs first. If it finds a clear international tuition fee → result returned immediately.
2. If Flash does **not** find a clear fee → Pro model runs as fallback.
3. If Pro finds a clear fee → Pro result is used with an annotation note.
4. If Pro also doesn't find a fee → Flash result is returned with an annotation note.

When disabled (default, `DEEPSEEK_ENABLE_PRO_FALLBACK=false`):
- Only Flash model runs. No fallback attempt. Simplest and most cost-effective.

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
