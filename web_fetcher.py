"""Web page fetching and link extraction."""

import logging
import re
import time
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

from config import REQUEST_TIMEOUT, MAX_RETRIES, REQUEST_DELAY

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
              "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "Referer": "https://www.google.com/",
    "Connection": "keep-alive",
}

ANTI_SCRAPE_PATTERNS = [
    "access denied",
    "forbidden",
    "enable javascript",
    "javascript is required",
    "please enable cookies",
]

SKIP_KEYWORDS = [
    "apply", "enquire", "contact", "visa", "accommodation",
    "scholarship", "scholarships", "news", "event", "events",
    "agent", "agents", "download", "brochure", "prospectus",
]

SKIP_DOMAINS = [
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "linkedin.com", "youtube.com", "tiktok.com",
]

FEE_KEYWORDS = [
    "fee", "fees", "tuition", "international fee", "international fees",
    "course fee", "course fees", "program fee", "program fees",
    "cost", "finance", "fee schedule", "fee structure",
]

FEE_CALCULATOR_KEYWORDS = ["fee calculator", "tuition calculator", "cost calculator"]

_good_suffixes = re.compile(
    r"/(fee|fees|tuition|international|finance|cost)(/|$|#)", re.IGNORECASE
)


def clean_url(raw_url: str | None) -> str | None:
    """Clean and extract a valid URL from potentially messy Excel cell content.

    Strips whitespace / newlines / tabs, extracts the first http(s) URL,
    or returns None when no valid URL is found.
    """
    if not raw_url:
        return None
    s = str(raw_url).strip()
    if not s:
        return None

    # Remove newlines, tabs, carriage returns that break URLs across cells
    s = s.replace("\n", "").replace("\r", "").replace("\t", "").strip()

    # Extract first http:// or https:// URL
    m = re.search(r"https?://[^\s<>\"'\\]+", s)
    if m:
        return m.group(0)

    # No protocol — check if it looks like a bare domain
    if "." in s and " " not in s and not s.startswith("/") and not s.startswith("#"):
        return "https://" + s

    return None


def fetch_page(url: str) -> tuple[str | None, str | None, str | None]:
    """Fetch a URL with retries and anti-scraping detection.

    Returns (html_text, 核验状态, 备注):
      (text,  None,          None)           — success
      (None,  "需人工复核",   "疑似反爬...")  — suspected anti-scraping
      (text,  "需人工复核",   "页面内容过短...") — content too short
      (None,  "页面失败",     "请求失败: ...") — network failure
    """
    last_exception = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)

            # Anti-scraping HTTP status codes
            if resp.status_code in (403, 401, 429):
                logger.warning(
                    "  [Attempt %d/%d] HTTP %d — suspected anti-scraping",
                    attempt, MAX_RETRIES, resp.status_code,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(2 * attempt)
                    continue
                return None, "需人工复核", "疑似反爬或 JS 动态页面，需人工打开官网核验"

            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            text = resp.text

            # Anti-scraping keywords in page body
            text_lower = text.lower()
            found_anti = any(p in text_lower for p in ANTI_SCRAPE_PATTERNS)

            if found_anti:
                logger.warning(
                    "  [Attempt %d/%d] Anti-scrape keyword detected",
                    attempt, MAX_RETRIES,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(2 * attempt)
                    continue
                return None, "需人工复核", "疑似反爬或 JS 动态页面，需人工打开官网核验"

            # Content too short
            if len(text.strip()) < 500:
                logger.warning(
                    "  [Attempt %d/%d] Page content too short (%d chars)",
                    attempt, MAX_RETRIES, len(text.strip()),
                )
                return text, "需人工复核", "页面内容过短，可能未抓取到真实页面"

            # Success
            return text, None, None

        except requests.RequestException as e:
            last_exception = e
            logger.warning(
                "  [Attempt %d/%d] Request failed: %s", attempt, MAX_RETRIES, e,
            )
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)

    return None, "页面失败", f"请求失败: {last_exception}"


def extract_text(soup: BeautifulSoup) -> str:
    """Extract readable text from a page, stripping scripts/styles/nav."""
    for tag in soup.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def extract_links(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """Extract all anchor links from a page, returning (url, text) dicts."""
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if not parsed.scheme.startswith("http"):
            continue
        clean_url_str = parsed._replace(fragment="").geturl()
        if clean_url_str in seen:
            continue
        seen.add(clean_url_str)
        text = a.get_text(strip=True)
        links.append({"url": clean_url_str, "text": text})
    return links


def get_school_domain(url: str) -> str:
    """Extract the school's main domain from a URL."""
    return urlparse(url).netloc.lower()


def _main_registered_domain(netloc: str) -> str:
    """Extract the main registered domain (e.g., 'anu.edu.au' from 'programsandcourses.anu.edu.au')."""
    parts = netloc.lower().split(".")
    if len(parts) < 2:
        return netloc
    # For country-specific TLDs like .com.au, .edu.au, .ac.uk, use last 3 parts
    # Otherwise use last 2 parts
    if len(parts) >= 3 and parts[-1] in ("au", "uk", "nz", "cn", "jp", "kr", "de", "fr", "sg", "my", "th", "in"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_same_school_domain(domain1: str, domain2: str) -> bool:
    """Check if two domains belong to the same school (exact match or subdomain-of-same-root)."""
    if domain1 == domain2:
        return True
    return _main_registered_domain(domain1) == _main_registered_domain(domain2)


def filter_official_links(links: list[dict], original_url: str) -> list[dict]:
    """Filter links to only include those from the same school domain as original_url.

    Keeps only links where the domain is the same as or a subdomain of the
    original URL's main registered domain. All third-party links are discarded.
    """
    original_domain = get_school_domain(original_url)
    filtered = []
    for link in links:
        link_domain = get_school_domain(link["url"])
        if is_same_school_domain(link_domain, original_domain):
            filtered.append(link)
    return filtered


def is_same_domain(url1: str, url2: str) -> bool:
    return urlparse(url1).netloc == urlparse(url2).netloc


def is_skip_link(url: str, text: str) -> bool:
    """Check if a link should be skipped (apply/contact/visa etc)."""
    combined = f"{url} {text}".lower()
    for kw in SKIP_KEYWORDS:
        if kw in combined:
            return True
    for domain in SKIP_DOMAINS:
        if domain in url.lower():
            return True
    return False


def is_fee_calculator_link(url: str, text: str) -> bool:
    combined = f"{url} {text}".lower()
    return any(kw in combined for kw in FEE_CALCULATOR_KEYWORDS)


def score_fee_link(url: str, text: str) -> int:
    """Score a link by how fee/tuition-relevant it is (higher = more relevant)."""
    combined = f"{url} {text}".lower()
    score = 0
    for kw in FEE_KEYWORDS:
        if kw in combined:
            score += 10
    if _good_suffixes.search(url):
        score += 15
    if is_fee_calculator_link(url, text):
        score += 5
    return score


def _fetch_with_delay(
    url: str, last_fetch: float,
) -> tuple[str | None, BeautifulSoup | None, float, str | None, str | None]:
    """Fetch with rate-limiting delay. Returns (html, soup, time, status, notes)."""
    now = time.time()
    elapsed = now - last_fetch
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)

    html, status, notes = fetch_page(url)
    if html is None:
        return None, None, time.time(), status, notes

    soup = BeautifulSoup(html, "lxml")
    return html, soup, time.time(), status, notes
