#!/usr/bin/env python3
"""Persistent local job finder for Seattle-area infrastructure roles."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "jobs.sqlite3"
DEFAULT_SOURCES = ROOT / "sources.json"
DEFAULT_ENV = ROOT / ".env"
DEFAULT_USER_CONFIG = ROOT / "job_getter.toml"
DEFAULT_WATCHLIST = ROOT / "watchlist.json"
DEFAULT_DISCOVERED_SOURCES = ROOT / "discovered_sources.json"
DEFAULT_PROFILES = ROOT / "profiles.json"
DEFAULT_ANSWER_BANK = ROOT / "answer_bank.json"
DEFAULT_APPLICATIONS_DIR = ROOT / "applications"
DEFAULT_ALERTS_DIR = ROOT / "alerts"
USER_AGENT = "job-finder-local/1.0 (+personal job search)"
NEWGRAD_LANDING_URL = "https://www.newgrad-jobs.com/"
NEWGRAD_MINISITE_URL = "https://jobright.ai/minisites-jobs/newgrad/{path}?embed=true"

STATUSES = {"seen", "saved", "applied", "ignored", "rejected"}
MANUAL_STATUSES = {"saved", "applied", "ignored", "rejected"}

SEATTLE_AREA = (
    "seattle",
    "bellevue",
    "redmond",
    "kirkland",
    "renton",
    "bothell",
    "everett",
    "tacoma",
    "issaquah",
    "lynnwood",
    "seatac",
    "tukwila",
)

WESTERN_WA_COUNTIES = (
    "king county",
    "snohomish county",
    "pierce county",
)

REMOTE_LOCATION_TERMS = (
    "remote",
    "united states",
    "usa",
    "us",
)

PRIMARY_ROLE_TERMS = (
    "infrastructure engineer",
    "network automation engineer",
    "network engineer",
    "systems engineer",
    "system engineer",
    "technical operations engineer",
    "operations engineer",
    "noc engineer",
    "network operations",
    "data center engineer",
    "data center technician",
    "cloud support engineer",
    "cloud systems engineer",
    "platform engineer",
)

CUSTOMER_ENGINEERING_TERMS = (
    "solutions engineer",
    "solution engineer",
    "sales engineer",
    "technical account engineer",
    "customer engineer",
)

SECONDARY_ROLE_TERMS = (
    "technical support engineer",
    "cloud engineer",
    "systems analyst",
    "solutions architect",
)

POSITIVE_TERMS = (
    "linux",
    "networking",
    "network",
    "python",
    "cisco",
    "ccna",
    "routing",
    "switching",
    "tcp/ip",
    "dns",
    "firewall",
    "troubleshooting",
    "monitoring",
    "incident response",
    "aws",
    "azure",
    "gcp",
)

ENTRY_TERMS = (
    "entry-level",
    "entry level",
    "associate",
    "junior",
    "new grad",
    "new graduate",
    "early career",
    "0-2 years",
    "0 - 2 years",
    "0 to 2 years",
)

NEGATIVE_TERMS = (
    "senior",
    "sr.",
    "staff",
    "principal",
    "lead engineer",
    "lead software",
    "5+ years",
    "7+ years",
    "10+ years",
    "unpaid",
    "internship",
    "frontend",
    "front-end",
    "full stack",
    "full-stack",
    "software engineer",
    "software developer",
    "application developer",
    "applications programming",
    "java developer",
    "machine learning engineer",
    "ml engineer",
    "qa tester",
    "quality assurance",
    "security officer",
    "gsoc operator",
    "global security operations center",
    "guard",
    "surveillance",
    "facilities",
    "maintenance technician",
    "construction project engineer",
    "bim specialist",
    "ios engineer",
    "android engineer",
    "project coordinator",
    "program coordinator",
    "research assistant",
    "research associate",
    "policy analyst",
    "freelancer",
    "airport",
)

HEAVY_DEVOPS_TERMS = (
    "kubernetes",
    "terraform",
    "sre",
    "site reliability",
    "ci/cd",
)


@dataclass(frozen=True)
class Job:
    source: str
    source_job_id: str
    canonical_url: str
    title: str
    company: str
    location: str = ""
    salary: str = ""
    work_model: str = ""
    posted_at: str = ""
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScoredJob:
    score: int
    why_match: str
    role_bucket: str
    interview_difficulty: int
    resume_match: int
    keyword_match: int
    semantic_match: int
    missing_keywords: str
    best_resume_profile: str
    estimated_salary: int | None
    salary_score: int
    why_apply: str
    concerns: str


@dataclass(frozen=True)
class Experience:
    summary: str
    min_years: float | None
    max_years: float | None


@dataclass(frozen=True)
class SalaryEstimate:
    minimum: int | None
    maximum: int | None
    estimated: int | None
    score: int


@dataclass(frozen=True)
class ResumeProfile:
    name: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class LiveVerification:
    status: str
    url: str
    final_url: str
    page_title: str
    text: str
    text_hash: str
    score: int
    concerns: str


ROLE_BUCKETS = (
    "Infrastructure",
    "Network",
    "Systems",
    "Operations",
    "Solutions",
    "Sales Engineer",
    "Cloud",
    "DevOps/SRE",
    "SWE",
    "Security",
    "Other",
)

TARGET_ROLE_BUCKETS = (
    "Infrastructure",
    "Network",
    "Systems",
    "Operations",
    "Solutions",
    "Sales Engineer",
    "Cloud",
)

NON_TARGET_ROLE_BUCKETS = ("Security", "SWE", "DevOps/SRE", "Other")

NON_US_REMOTE_TERMS = (
    "india",
    "canada",
    "europe",
    "emea",
    "apac",
    "latam",
    "united kingdom",
    " uk",
    "germany",
    "berlin",
    "munich",
    "hamburg",
    "egypt",
    "kuwait",
    "bahrain",
    "nordics",
    "calgary",
    "toronto",
)

HARD_EXCLUDED_TITLE_TERMS = (
    "country director",
    "director",
    "head of",
    "leader",
    "manager",
    "senior manager",
    "business analyst",
    "project manager",
    "program manager",
    "coordinator",
    "account executive",
    "hardware systems engineer",
    "power system engineer",
    "quality systems engineer",
    "systems development engineer",
)

ARGOS_TERMS = (
    "linux",
    "ubuntu",
    "starlink",
    "wireless",
    "wireless networking",
    "telemetry",
    "monitoring",
    "grafana",
    "sensor",
    "sensors",
    "field systems",
    "environmental",
    "network troubleshooting",
    "python",
    "remote deployment",
    "remote site",
    "remote monitoring",
    "vpn",
    "zerotier",
    "mysql",
    "rest api",
    "logging",
    "alerting",
)

INFRA_RESUME_TERMS = (
    "linux",
    "ssh",
    "tcp/ip",
    "vpn",
    "zerotier",
    "starlink",
    "wireless networking",
    "network troubleshooting",
    "raid",
    "backup",
    "dns",
    "remote site operations",
    "grafana",
    "system monitoring",
    "log analysis",
    "alerting",
    "incident response",
    "python",
    "bash",
    "sql",
    "rest api",
    "cron",
    "docker",
    "mysql",
    "raspberry pi",
    "cloudflare",
    "vps",
    "nginx",
)

SOLUTIONS_RESUME_TERMS = (
    "requirements gathering",
    "technical demos",
    "client support",
    "documentation",
    "training",
    "workflow analysis",
    "linux",
    "tcp/ip",
    "vpn",
    "starlink",
    "wireless networking",
    "dns",
    "cloudflare",
    "monitoring",
    "grafana",
    "log analysis",
    "alerting",
    "incident response",
    "operational reporting",
    "python",
    "sql",
    "rest api",
    "automation",
    "docker",
    "mysql",
    "customer",
)

TIER_1_COMPANIES = ("f5", "cisco", "juniper", "cloudflare", "t-mobile", "tmobile")
TIER_2_COMPANIES = ("amazon", "microsoft", "expedia", "meta")
TIER_3_COMPANY_TERMS = ("msp", "telecom", "utility", "environmental", "monitoring")

GAP_TERMS = (
    "aws",
    "azure",
    "gcp",
    "ccna",
    "cisco",
    "routing",
    "switching",
    "tcp/ip",
    "dns",
    "firewall",
    "vpn",
    "linux",
    "python",
    "bash",
    "powershell",
    "terraform",
    "kubernetes",
    "docker",
    "monitoring",
    "grafana",
    "datadog",
    "splunk",
    "incident response",
    "troubleshooting",
    "data center",
    "itil",
    "customer demos",
    "technical demos",
    "documentation",
    "requirements gathering",
    "salesforce",
    "soc2",
)

DIRECT_ATS_TYPES = {
    "ashby",
    "bamboohr",
    "greenhouse",
    "lever",
    "recruitee",
    "smartrecruiters",
    "workable",
    "workday",
}

OFFICIAL_API_TYPES = {
    "adzuna",
    "careeronestop",
    "findwork",
    "serpapi_google_jobs",
    "usajobs",
}

BROAD_BOARD_TYPES = {
    "arbeitnow",
    "github_markdown",
    "jobicy",
    "jobspy",
    "newgrad",
    "remoteok",
    "remotive",
    "rss",
    "themuse",
}

AGGREGATOR_HOST_TERMS = (
    "jobright.ai",
    "newgrad-jobs.com",
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "google.com",
    "remotive.com",
    "arbeitnow.com",
    "jobicy.com",
    "adzuna.com",
    "builtin.com",
    "builtinseattle.com",
    "localjobs.com",
)

EXPIRED_PAGE_TERMS = (
    "job expired",
    "this job has expired",
    "no longer accepting applications",
    "no longer available",
    "position has been filled",
    "job is no longer available",
    "sorry, this job has expired",
)

CLEARANCE_TERMS = (
    "security clearance",
    "active clearance",
    "secret clearance",
    "top secret",
    "ts/sci",
)

COMPANY_SUFFIX_TOKENS = {
    "inc",
    "inc.",
    "llc",
    "l.l.c",
    "corp",
    "corporation",
    "co",
    "company",
    "ltd",
    "limited",
    "group",
    "networks",
    "technologies",
    "technology",
    "systems",
    "enterprises",
    "services",
    "us",
    "usa",
}

USER_CONFIG: dict[str, Any] = {}


def load_user_config(path: Path = DEFAULT_USER_CONFIG) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a TOML table: {path}")
    return data


def configure_user(path: Path = DEFAULT_USER_CONFIG) -> None:
    global USER_CONFIG
    USER_CONFIG = load_user_config(path)


def config_table(name: str) -> dict[str, Any]:
    value = USER_CONFIG.get(name, {})
    return value if isinstance(value, dict) else {}


def config_list(table: str, key: str, default: Iterable[str]) -> tuple[str, ...]:
    value = config_table(table).get(key)
    if not isinstance(value, list):
        return tuple(default)
    return tuple(str(item).strip().lower() for item in value if str(item).strip())


def config_str(table: str, key: str, default: str) -> str:
    value = config_table(table).get(key)
    return str(value).strip() if value not in (None, "") else default


def config_int(table: str, key: str, default: int) -> int:
    value = config_table(table).get(key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def config_float(table: str, key: str, default: float) -> float:
    value = config_table(table).get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def config_bool(table: str, key: str, default: bool) -> bool:
    value = config_table(table).get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def target_cities() -> tuple[str, ...]:
    return config_list("location", "target_cities", SEATTLE_AREA)


def target_region_terms() -> tuple[str, ...]:
    return config_list(
        "location",
        "target_region_terms",
        (*WESTERN_WA_COUNTIES, "wa", "washington", "washington state"),
    )


def excluded_location_terms() -> tuple[str, ...]:
    return config_list(
        "location",
        "excluded_location_terms",
        ("washington dc", "district of columbia", "us-dc washington", "dc office"),
    )


def remote_us_location_terms() -> tuple[str, ...]:
    return config_list("location", "remote_us_terms", REMOTE_LOCATION_TERMS)


def non_us_remote_terms() -> tuple[str, ...]:
    return config_list("location", "non_us_remote_terms", NON_US_REMOTE_TERMS)


def target_metro_label() -> str:
    return config_str("location", "target_metro_label", "Seattle area")


def target_region_label() -> str:
    return config_str("location", "target_region_label", "Washington")


def valid_role_bucket(value: str) -> str | None:
    lookup = {bucket.lower().replace("/", "_").replace(" ", "_").replace("-", "_"): bucket for bucket in ROLE_BUCKETS}
    return lookup.get(value.lower().replace("/", "_").replace(" ", "_").replace("-", "_"))


def target_role_buckets() -> tuple[str, ...]:
    configured = config_table("roles").get("target_buckets")
    if not isinstance(configured, list):
        return TARGET_ROLE_BUCKETS
    buckets = tuple(
        bucket
        for item in configured
        if (bucket := valid_role_bucket(str(item))) is not None
    )
    return buckets or TARGET_ROLE_BUCKETS


def non_target_role_buckets() -> tuple[str, ...]:
    targets = set(target_role_buckets())
    return tuple(bucket for bucket in ROLE_BUCKETS if bucket not in targets)


def positive_terms() -> tuple[str, ...]:
    return config_list("scoring", "positive_terms", POSITIVE_TERMS)


def negative_terms() -> tuple[str, ...]:
    return config_list("scoring", "negative_terms", NEGATIVE_TERMS)


def user_strength_terms() -> tuple[str, ...]:
    return config_list("scoring", "user_strength_terms", ARGOS_TERMS)


def primary_role_terms() -> tuple[str, ...]:
    return config_list("scoring", "primary_role_terms", PRIMARY_ROLE_TERMS)


def customer_engineering_terms() -> tuple[str, ...]:
    return config_list("scoring", "customer_engineering_terms", CUSTOMER_ENGINEERING_TERMS)


def secondary_role_terms() -> tuple[str, ...]:
    return config_list("scoring", "secondary_role_terms", SECONDARY_ROLE_TERMS)


def heavy_devops_terms() -> tuple[str, ...]:
    return config_list("scoring", "heavy_devops_terms", HEAVY_DEVOPS_TERMS)


def hard_excluded_title_terms() -> tuple[str, ...]:
    return config_list("filters", "hard_excluded_title_terms", HARD_EXCLUDED_TITLE_TERMS)


def senior_title_terms() -> tuple[str, ...]:
    return config_list("filters", "senior_title_terms", ("senior", "sr.", "sr", "staff", "principal"))


def customer_engineering_exception_terms() -> tuple[str, ...]:
    return config_list(
        "filters",
        "customer_engineering_exception_terms",
        ("sales engineer", "solutions engineer", "solution engineer"),
    )


def suspicious_location_terms() -> tuple[str, ...]:
    return config_list("location", "suspicious_location_terms", ("international",))


def role_term_config_key(role_bucket: str) -> str:
    return role_bucket.lower().replace("/", "_").replace(" ", "_").replace("-", "_")


def configured_role_terms(role_bucket: str, default: Iterable[str]) -> tuple[str, ...]:
    return config_list("role_terms", role_term_config_key(role_bucket), default)


def infrastructure_context_terms() -> tuple[str, ...]:
    return config_list(
        "role_context",
        "infrastructure_context_terms",
        (
            "infrastructure",
            "network",
            "systems",
            "system",
            "platform",
            "cloud",
            "linux",
            "data center",
            "datacenter",
            "operations",
            "support",
            "security operations",
            "incident",
            "monitoring",
        ),
    )


def preferred_tier_1_companies() -> tuple[str, ...]:
    return config_list("companies", "tier_1", TIER_1_COMPANIES)


def preferred_tier_2_companies() -> tuple[str, ...]:
    return config_list("companies", "tier_2", TIER_2_COMPANIES)


def preferred_company_terms() -> tuple[str, ...]:
    return config_list("companies", "interesting_terms", TIER_3_COMPANY_TERMS)


def apply_min_resume_match() -> int:
    return config_int("apply", "min_resume_match", 3)


def apply_min_score() -> int:
    return config_int("apply", "min_score", 55)


def apply_max_difficulty() -> int:
    return config_int("apply", "max_interview_difficulty", 6)

def apply_max_years() -> float:
    return config_float("apply", "max_years", 3.0)


def apply_require_live_verification() -> bool:
    return config_bool("apply", "require_live_verification", False)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_datetime(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 100000000000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat()
    text = str(value).strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{13}", text):
        return parse_datetime(int(text))
    if re.fullmatch(r"\d{10}", text):
        return parse_datetime(int(text))
    month_day = re.fullmatch(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})",
        text,
        flags=re.IGNORECASE,
    )
    if month_day:
        month_lookup = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "sept": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        month = month_lookup[month_day.group(1)[:4].lower() if month_day.group(1).lower().startswith("sept") else month_day.group(1)[:3].lower()]
        parsed = datetime(datetime.now(timezone.utc).year, month, int(month_day.group(2)), tzinfo=timezone.utc)
        return parsed.replace(microsecond=0).isoformat()
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def freshness_date(row: sqlite3.Row | dict[str, Any]) -> datetime:
    value = row["posted_at"] or row["first_seen_at"]
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_env_file(path: Path = DEFAULT_ENV, *, override: bool = False) -> int:
    """Load KEY=VALUE pairs from a local .env file without requiring python-dotenv."""
    if not path.exists():
        return 0
    loaded = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def fetch_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    retries: int = 0,
    retry_sleep: float = 1.0,
) -> Any:
    return json.loads(fetch_text(url, headers=headers, timeout=timeout, retries=retries, retry_sleep=retry_sleep))


def fetch_json_post(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    retries: int = 0,
    retry_sleep: float = 1.0,
) -> Any:
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
        **(headers or {}),
    }
    encoded = json.dumps(payload).encode("utf-8")
    for attempt in range(retries + 1):
        req = Request(url, data=encoded, headers=request_headers, method="POST")
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(raw.decode(charset, errors="replace"))
        except HTTPError as exc:
            if attempt >= retries or exc.code not in RETRYABLE_HTTP_STATUS_CODES:
                raise
            sleep_for_retry(exc, retry_sleep, attempt)
    raise RuntimeError("unreachable retry loop")


RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}


def sleep_for_retry(exc: HTTPError, retry_sleep: float, attempt: int) -> None:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    try:
        delay = float(retry_after) if retry_after else retry_sleep * (attempt + 1)
    except ValueError:
        delay = retry_sleep * (attempt + 1)
    time.sleep(max(0.0, delay))


def fetch_text(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    retries: int = 0,
    retry_sleep: float = 1.0,
) -> str:
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    for attempt in range(retries + 1):
        req = Request(url, headers=request_headers)
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except HTTPError as exc:
            if attempt >= retries or exc.code not in RETRYABLE_HTTP_STATUS_CODES:
                raise
            sleep_for_retry(exc, retry_sleep, attempt)
    raise RuntimeError("unreachable retry loop")


class PlainTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = data.strip()
        if text:
            self.parts.append(text)


def html_to_plain_text(raw_html: str) -> str:
    stripped = re.sub(
        r"<(script|style|noscript|svg)\b.*?</\1>",
        " ",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    parser = PlainTextExtractor()
    parser.feed(stripped)
    text = html.unescape(" ".join(parser.parts))
    return re.sub(r"\s+", " ", text).strip()


def html_page_title(raw_html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))).strip()


def fetch_page_for_verification(url: str, timeout: int = 18) -> tuple[int, str, str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as response:
        raw = response.read(2_500_000)
        charset = response.headers.get_content_charset() or "utf-8"
        raw_html = raw.decode(charset, errors="replace")
        return response.status, response.geturl(), html_page_title(raw_html), html_to_plain_text(raw_html)


def auth_header_value(token: str, scheme: str) -> str:
    token = token.strip()
    if token.lower().startswith((scheme.lower() + " ")):
        return token
    return f"{scheme} {token}"


def source_kind_from_job(job: Job) -> str:
    if isinstance(job.raw, dict):
        value = job.raw.get("_source_type")
        if value:
            return str(value)
    source_lower = job.source.lower()
    for kind in DIRECT_ATS_TYPES | OFFICIAL_API_TYPES | BROAD_BOARD_TYPES:
        if kind in source_lower:
            return kind
    return "unknown"


def source_confidence_for_kind(kind: str) -> str:
    kind_lower = kind.lower()
    if kind_lower in DIRECT_ATS_TYPES:
        return "high"
    if kind_lower in OFFICIAL_API_TYPES:
        return "medium"
    if kind_lower in BROAD_BOARD_TYPES:
        return "low"
    return "unknown"


def source_confidence_for_job(job: Job) -> str:
    if isinstance(job.raw, dict) and job.raw.get("_source_confidence"):
        return str(job.raw["_source_confidence"])
    return source_confidence_for_kind(source_kind_from_job(job))


def source_type_for_job(job: Job) -> str:
    return source_kind_from_job(job)


def is_aggregator_url(url: str) -> bool:
    url_lower = url.lower()
    return any(host in url_lower for host in AGGREGATOR_HOST_TERMS)


def candidate_apply_urls_for_job(job: Job) -> list[str]:
    urls: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.startswith("http") and value not in urls:
            urls.append(value)

    if isinstance(job.raw, dict):
        for key in (
            "canonical_employer_url",
            "employer_url",
            "apply_url",
            "absolute_url",
            "hostedUrl",
            "jobUrl",
            "externalUrl",
            "canonical_url",
            "link",
        ):
            add(job.raw.get(key))
        for option in job.raw.get("apply_options") or []:
            if isinstance(option, dict):
                add(option.get("link"))
    add(job.canonical_url)
    return urls


def canonical_employer_url_for_job(job: Job) -> str:
    if isinstance(job.raw, dict):
        for key in ("canonical_employer_url", "employer_url", "apply_url", "absolute_url", "hostedUrl", "jobUrl"):
            value = job.raw.get(key)
            if isinstance(value, str) and value.startswith("http") and not is_aggregator_url(value):
                return value
    if job.canonical_url.startswith("http") and not is_aggregator_url(job.canonical_url):
        return job.canonical_url
    return ""


def verified_on_company_site_for_job(job: Job) -> bool:
    if isinstance(job.raw, dict) and job.raw.get("verified_on_company_site") is not None:
        return bool(job.raw["verified_on_company_site"])
    return bool(canonical_employer_url_for_job(job)) and source_kind_from_job(job) in DIRECT_ATS_TYPES


def enrich_raw_with_source(source: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    kind = str(source.get("type") or "unknown")
    confidence = str(source.get("source_confidence") or source_confidence_for_kind(kind))
    return {
        **raw,
        "_source_name": source.get("name", ""),
        "_source_type": kind,
        "_source_confidence": confidence,
    }


def content_hash(job: Job) -> str:
    payload = {
        "canonical_url": job.canonical_url,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "salary": job.salary,
        "work_model": job.work_model,
        "posted_at": job.posted_at,
        "raw": job.raw or {},
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_source_job_id(*parts: Any) -> str:
    text = "|".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def normalize_salary(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return ""
    if isinstance(value, dict):
        low = value.get("minValue") or value.get("minimum") or value.get("min")
        high = value.get("maxValue") or value.get("maximum") or value.get("max")
        unit = value.get("unitText") or value.get("unit") or ""
        if low and high:
            return f"${low}-${high} {unit}".strip()
        if low:
            return f"${low}+ {unit}".strip()
        return ""
    return str(value)


def extract_salary_estimate(job: Job) -> SalaryEstimate:
    text = job.salary
    if not text:
        return SalaryEstimate(None, None, None, 0)
    normalized = text.lower().replace(",", "")
    if "predicted" in normalized or "estimated by" in normalized:
        return SalaryEstimate(None, None, None, 0)
    hourly = "/hr" in normalized or "per hour" in normalized or "hourly" in normalized
    numbers: list[float] = []

    for match in re.finditer(r"\$\s*(\d+(?:\.\d+)?)\s*k\b", normalized):
        numbers.append(float(match.group(1)) * 1000)
    for match in re.finditer(r"\$\s*(\d{2,6}(?:\.\d+)?)", normalized):
        value = float(match.group(1))
        if value < 300 and hourly:
            value *= 2080
        numbers.append(value)

    if not numbers:
        return SalaryEstimate(None, None, None, 0)

    sensible = [int(value) for value in numbers if 10000 <= value <= 400000]
    if not sensible:
        return SalaryEstimate(None, None, None, 0)
    low = min(sensible)
    high = max(sensible)
    estimated = int((low + high) / 2)
    if estimated >= 150000:
        salary_score = 20
    elif estimated >= 130000:
        salary_score = 16
    elif estimated >= 110000:
        salary_score = 12
    elif estimated >= 90000:
        salary_score = 8
    elif estimated >= 75000:
        salary_score = 4
    elif estimated < 60000:
        salary_score = -8
    else:
        salary_score = 0
    return SalaryEstimate(low, high, estimated, salary_score)


def text_blob(job: Job | sqlite3.Row | dict[str, Any]) -> str:
    if isinstance(job, Job):
        parts = [job.title, job.company, job.location, job.work_model, job.salary]
        raw = job.raw or {}
        if isinstance(raw, dict):
            parts.append(json.dumps(raw, ensure_ascii=True))
    else:
        keys = job.keys() if hasattr(job, "keys") else job.keys()
        live_text = str(job["live_page_text"]) if "live_page_text" in keys else ""
        parts = [
            str(job["title"]),
            str(job["company"]),
            str(job["location"]),
            str(job["work_model"]),
            str(job["salary"]),
            live_text,
            str(job["raw_json"]),
        ]
    return " ".join(part for part in parts if part).lower()


def clean_experience_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" .;:-\n\t")).strip()


def console_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "")
    if limit is not None:
        text = text[:limit]
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def extract_experience(job: Job) -> Experience:
    raw_text = ""
    if isinstance(job.raw, dict):
        raw_text = json.dumps(job.raw, ensure_ascii=False)
    source_text = " ".join([job.title, raw_text])
    normalized = re.sub(r"\\n|\\r", " ", source_text)
    normalized = html.unescape(normalized)
    lowered = normalized.lower()

    no_exp_patterns = (
        r"\bno (?:prior |professional |relevant )?experience required\b",
        r"\b0 years? of (?:professional |relevant )?experience\b",
        r"\b0 years? relevant experience\b",
    )
    for pattern in no_exp_patterns:
        match = re.search(pattern, lowered)
        if match:
            return Experience(clean_experience_text(match.group(0)), 0.0, 0.0)

    range_pattern = re.compile(
        r"(?P<min>\d+(?:\.\d+)?)\s*(?:-|\u2013|\u2014|to)\s*(?P<max>\d+(?:\.\d+)?)\+?"
        r"\s+years?(?:\s+of)?(?:\s+(?:professional|relevant|related))?\s+experience"
    )
    match = range_pattern.search(lowered)
    if match:
        return Experience(
            clean_experience_text(match.group(0)),
            float(match.group("min")),
            float(match.group("max")),
        )

    alt_range_pattern = re.compile(
        r"(?P<min>\d+(?:\.\d+)?)\s*(?:-|\u2013|\u2014|to)\s*(?P<max>\d+(?:\.\d+)?)\+?"
        r"\s+years?"
    )
    match = alt_range_pattern.search(lowered)
    if match and nearby_experience_word(lowered, match.start(), match.end()):
        return Experience(
            clean_experience_text(match.group(0)),
            float(match.group("min")),
            float(match.group("max")),
        )

    minimum_pattern = re.compile(
        r"(?:at least|minimum(?: of)?|requires?|required|need|needs|must have|"
        r"prefer(?:red|ably)?|preferred:?)\s+(?P<min>\d+(?:\.\d+)?)\+?"
        r"\s+years?(?:\s+of)?(?:\s+(?:professional|relevant|related))?\s+experience"
    )
    match = minimum_pattern.search(lowered)
    if match:
        return Experience(clean_experience_text(match.group(0)), float(match.group("min")), None)

    plus_pattern = re.compile(
        r"(?P<min>\d+(?:\.\d+)?)\s*\+\s+years?(?:\s+of)?"
        r"(?:\s+(?:professional|relevant|related))?\s+experience"
    )
    match = plus_pattern.search(lowered)
    if match:
        return Experience(clean_experience_text(match.group(0)), float(match.group("min")), None)

    simple_pattern = re.compile(
        r"(?P<years>\d+(?:\.\d+)?)\s+years?(?:\s+of)?"
        r"(?:\s+(?:professional|relevant|related))?\s+experience"
    )
    match = simple_pattern.search(lowered)
    if match:
        years = float(match.group("years"))
        return Experience(clean_experience_text(match.group(0)), years, years)

    return Experience("unknown", None, None)


def nearby_experience_word(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 80) : min(len(text), end + 100)]
    return "experience" in window or "experienced" in window


def is_entryish_experience(exp: Experience, max_years: float = 3.0) -> bool:
    if exp.min_years is None and exp.max_years is None:
        return True
    if exp.max_years is not None:
        return exp.max_years <= max_years
    if exp.min_years is not None:
        return exp.min_years <= max_years
    return True


def is_seattle_area_location(location: str) -> bool:
    location_lower = location.lower()
    if not location_lower:
        return False
    if not is_washington_state_location(location):
        return False
    return any(re.search(rf"\b{re.escape(city)}\b", location_lower) for city in target_cities())


def is_washington_dc_location(location: str) -> bool:
    location_lower = location.lower()
    if any(term in location_lower for term in excluded_location_terms()):
        return True
    return bool(
        re.search(r"\bwashington\s*,?\s*d\.?c\.?\b", location_lower)
        or re.search(r"\bus-dc[-,\s]*washington\b", location_lower)
        or "district of columbia" in location_lower
        or "dc office" in location_lower
    )


def is_washington_state_location(location: str) -> bool:
    location_lower = location.lower()
    if not location_lower or is_washington_dc_location(location):
        return False
    for term in target_region_terms():
        if len(term) <= 3:
            if re.search(rf"\b{re.escape(term)}\b", location_lower):
                return True
        elif term == "washington":
            if re.search(r",\s*washington\b", location_lower) or re.search(r"\bwashington\s+state\b", location_lower):
                return True
        elif term in location_lower:
            return True
    return False


def is_remote_location(location: str, work_model: str) -> bool:
    location_lower = location.lower().strip()
    work_model_lower = work_model.lower()
    if is_non_us_remote_location(location, work_model):
        return False
    if "remote" in work_model_lower:
        return (
            not location_lower
            or "remote" in location_lower
            or location_lower in set(remote_us_location_terms())
            or location_lower.startswith("usa-")
            or location_lower.startswith("us-")
        )
    if "remote" not in location_lower:
        return False
    if "remote sensing" in location_lower:
        return False
    return True


def is_non_us_remote_location(location: str, work_model: str) -> bool:
    location_lower = location.lower().strip()
    work_model_lower = work_model.lower()
    if "remote" not in location_lower and "remote" not in work_model_lower:
        return False
    if is_seattle_area_location(location) or is_washington_state_location(location):
        return False
    return any(term in f" {location_lower} " for term in non_us_remote_terms())


def is_target_location(location: str, work_model: str) -> bool:
    location_lower = location.lower()
    if (
        any(term in location_lower for term in suspicious_location_terms())
        and not is_seattle_area_location(location)
        and not is_remote_location(location, work_model)
    ):
        return False
    return (
        is_seattle_area_location(location)
        or is_washington_state_location(location)
        or is_remote_location(location, work_model)
    )


def has_hard_excluded_title(title: str) -> bool:
    title_lower = title.lower()
    if any(term in title_lower for term in customer_engineering_exception_terms()):
        if (
            "manager" not in title_lower
            and "leader" not in title_lower
            and "head of" not in title_lower
            and not re.search(r"\b(sr|senior|staff|principal|lead)\b", title_lower)
        ):
            return False
    if any(re.search(rf"\b{re.escape(term)}\b", title_lower) for term in senior_title_terms() if term.isalnum()):
        return True
    if any(term in title_lower for term in senior_title_terms() if not term.isalnum()):
        return True
    return any(term in title_lower for term in hard_excluded_title_terms())


def has_invalid_company(company: str) -> bool:
    cleaned = clean_markdown_cell(company)
    return not cleaned or cleaned in {"?", "\u21b3"} or len(cleaned) <= 1


def has_infrastructure_context(blob: str) -> bool:
    return any(term in blob for term in infrastructure_context_terms())


def count_term_hits(blob: str, terms: Iterable[str]) -> list[str]:
    return [term for term in terms if term in blob]


def token_set(value: str) -> set[str]:
    stop = {
        "and",
        "are",
        "for",
        "from",
        "have",
        "our",
        "the",
        "this",
        "that",
        "with",
        "you",
        "your",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9+#.]+", value.lower())
        if len(token) > 2 and token not in stop
    }


def normalized_text_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2
    }


def company_match_tokens(company: str) -> set[str]:
    tokens = normalized_text_tokens(company)
    return {token for token in tokens if token not in COMPANY_SUFFIX_TOKENS}


def title_match_tokens(title: str) -> set[str]:
    tokens = normalized_text_tokens(re.sub(r"\([^)]*\)", " ", title))
    return {
        token
        for token in tokens
        if token
        not in {
            "engineer",
            "engineering",
            "level",
            "remote",
            "with",
            "role",
            "job",
            "hiring",
        }
    }


def has_expired_signal(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in EXPIRED_PAGE_TERMS)


def has_clearance_signal(job: Job) -> bool:
    lowered = text_blob(job)
    return any(term in lowered for term in CLEARANCE_TERMS)


def live_status_for_job(job: Job) -> str:
    if isinstance(job.raw, dict):
        return str(job.raw.get("_live_verification_status") or "")
    return ""


def live_concerns_for_job(job: Job) -> str:
    if isinstance(job.raw, dict):
        return str(job.raw.get("_live_verification_concerns") or "")
    return ""


def live_page_is_bad(job: Job) -> bool:
    return live_status_for_job(job) in {"expired", "mismatch", "blocked", "no_text", "failed"}


def verification_quality(job: Job, page_text: str, page_title: str) -> tuple[str, int, list[str]]:
    combined = f"{page_title} {page_text}".lower()
    concerns: list[str] = []
    score = 0

    if has_expired_signal(combined):
        return "expired", -100, ["Expired job page"]

    company_tokens = company_match_tokens(job.company)
    title_tokens = title_match_tokens(job.title)
    combined_tokens = normalized_text_tokens(combined[:12000])
    title_area_tokens = normalized_text_tokens(f"{page_title} {page_text[:2500]}")

    company_hits = company_tokens & combined_tokens
    title_hits = title_tokens & title_area_tokens
    title_ratio = len(title_hits) / max(1, len(title_tokens))

    if company_hits:
        score += 35
    elif company_tokens:
        score -= 25
        concerns.append("Company not found on live page")

    if title_ratio >= 0.5 or len(title_hits) >= min(3, len(title_tokens)):
        score += 35
    elif title_tokens:
        score -= 30
        concerns.append("Title looks different on live page")

    if job.location and any(token in combined for token in normalized_text_tokens(job.location)):
        score += 8
    if len(page_text) < 1200:
        score -= 25
        concerns.append("Live page text too short")

    if score >= 40:
        status = "verified"
    elif score <= -15 and ("Company not found on live page" in concerns or "Title looks different on live page" in concerns):
        status = "mismatch"
    else:
        status = "unverified"
    return status, score, concerns


def keyword_match_percent(job: Job, profile: ResumeProfile) -> int:
    blob = text_blob(job)
    hits = count_term_hits(blob, profile.terms)
    return min(100, round((len(hits) / len(profile.terms)) * 100)) if profile.terms else 0


def semantic_match_percent(job: Job, profile: ResumeProfile) -> int:
    job_tokens = token_set(text_blob(job))
    profile_tokens = token_set(" ".join(profile.terms))
    if not job_tokens or not profile_tokens:
        return 0
    overlap = len(job_tokens & profile_tokens)
    coverage = overlap / max(1, len(profile_tokens))
    density = overlap / max(1, min(len(job_tokens), len(profile_tokens)))
    return min(100, round((coverage * 0.65 + density * 0.35) * 100))


def missing_keywords_for_profile(job: Job, profile: ResumeProfile, limit: int = 8) -> list[str]:
    blob = text_blob(job)
    profile_blob = " ".join(profile.terms).lower()
    return [
        term
        for term in GAP_TERMS
        if term in blob and term not in profile_blob
    ][:limit]


def classify_role(job: Job) -> str:
    title = job.title.lower()
    if any(term in title for term in configured_role_terms("Network", ("network engineer", "network automation", "network operations", "noc"))):
        return "Network"
    if any(term in title for term in configured_role_terms("DevOps/SRE", ("sre", "site reliability", "devops"))):
        return "DevOps/SRE"
    if any(term in title for term in configured_role_terms("Infrastructure", ("infrastructure", "data center", "datacenter"))):
        return "Infrastructure"
    if (
        any(title.startswith(term) for term in configured_role_terms("SWE", ("software engineer", "software developer")))
        or "systems development engineer" in title
    ) and "infrastructure" not in title:
        return "SWE"
    if any(term in title for term in configured_role_terms("Systems", ("systems engineer", "system engineer", "systems administrator", "sysadmin"))):
        return "Systems"
    if any(term in title for term in configured_role_terms("Operations", ("operations engineer", "technical operations", "operations technician", "support engineer"))):
        return "Operations"
    if any(term in title for term in configured_role_terms("Sales Engineer", ("sales engineer", "pre-sales", "presales"))):
        return "Sales Engineer"
    if any(term in title for term in configured_role_terms("Solutions", ("solutions engineer", "solution engineer", "customer engineer", "technical account"))):
        return "Solutions"
    if any(term in title for term in configured_role_terms("Cloud", ("cloud engineer", "cloud support", "cloud systems"))):
        return "Cloud"
    if any(term in title for term in configured_role_terms("SWE", ("software engineer", "software developer", "application developer", "java developer", "frontend", "backend", "full stack"))):
        return "SWE"
    if any(term in title for term in configured_role_terms("Security", ("security", "cyber"))):
        return "Security"
    return "Other"


def resume_profile_for_role(role_bucket: str) -> ResumeProfile:
    if role_bucket in {"Solutions", "Sales Engineer"}:
        return ResumeProfile(
            "general_technical_solutions",
            config_list("resume_profiles", "solutions_terms", SOLUTIONS_RESUME_TERMS),
        )
    if role_bucket == "SWE":
        return ResumeProfile(
            "software_engineering",
            config_list("resume_profiles", "swe_terms", INFRA_RESUME_TERMS),
        )
    if role_bucket == "Security":
        return ResumeProfile(
            "security",
            config_list("resume_profiles", "security_terms", INFRA_RESUME_TERMS),
        )
    return ResumeProfile(
        "infrastructure_network",
        config_list("resume_profiles", "infrastructure_network_terms", INFRA_RESUME_TERMS),
    )


def resume_match_percent(job: Job, role_bucket: str) -> int:
    profile = resume_profile_for_role(role_bucket)
    return keyword_match_percent(job, profile)


def preferred_company_bonus(company: str, blob: str) -> tuple[int, str]:
    company_lower = company.lower()
    if any(term in company_lower for term in preferred_tier_1_companies()):
        return 18, "tier 1 company"
    if any(term in company_lower for term in preferred_tier_2_companies()):
        return 12, "tier 2 company"
    if any(term in blob for term in preferred_company_terms()):
        return 5, "interesting company domain"
    return 0, ""


def freshness_score(job: Job) -> int:
    value = job.posted_at
    if not value:
        return 0
    try:
        posted = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - posted.astimezone(timezone.utc)).days
    if age_days <= 2:
        return 8
    if age_days <= 7:
        return 6
    if age_days <= 14:
        return 3
    if age_days <= 30:
        return 1
    return -5


def interview_difficulty(job: Job, role_bucket: str, experience: Experience) -> int:
    blob = text_blob(job)
    base_by_role = {
        "Network": 3,
        "Infrastructure": 4,
        "Systems": 4,
        "Operations": 3,
        "Solutions": 5,
        "Sales Engineer": 5,
        "Cloud": 6,
        "Security": 6,
        "DevOps/SRE": 8,
        "SWE": 8,
        "Other": 5,
    }
    difficulty = base_by_role.get(role_bucket, 5)
    easy_hits = count_term_hits(blob, ("network", "infrastructure", "operations", "support", "systems"))
    hard_hits = count_term_hits(
        blob,
        (
            "leetcode",
            "algorithm",
            "algorithms",
            "distributed systems",
            "kubernetes",
            "terraform",
            "sre",
            "site reliability",
            "microservices",
        ),
    )
    if easy_hits:
        difficulty -= 1
    difficulty += min(3, len(hard_hits))
    if experience.min_years is not None and experience.min_years >= 5:
        difficulty += 2
    return max(1, min(10, difficulty))


def build_concerns(job: Job, role_bucket: str, experience: Experience, resume_match: int, difficulty: int) -> str:
    concerns: list[str] = []
    source_confidence = source_confidence_for_job(job)
    if source_confidence == "low":
        concerns.append("Aggregator/list source")
    elif source_confidence == "unknown":
        concerns.append("Unknown source confidence")
    if not verified_on_company_site_for_job(job):
        concerns.append("Apply URL not verified on company site")
    if has_invalid_company(job.company):
        concerns.append("Unknown company")
    if role_bucket == "Security":
        concerns.append("Security-focused role")
    if role_bucket == "SWE":
        concerns.append("Potentially too SWE-heavy")
    if role_bucket == "DevOps/SRE":
        concerns.append("Heavy DevOps/SRE role")
    if role_bucket == "Other":
        concerns.append("Non-target role bucket")
    live_status = live_status_for_job(job)
    live_concerns = live_concerns_for_job(job)
    if live_status == "verified":
        pass
    elif live_status == "expired":
        concerns.append("Expired job page")
    elif live_status == "mismatch":
        concerns.append("Live page may be wrong job")
    elif live_status == "blocked":
        concerns.append("Live page blocked verification")
    elif live_status == "no_text":
        concerns.append("No live page text")
    elif live_status == "failed":
        concerns.append("Live page verification failed")
    elif live_status == "unverified":
        concerns.append("Live page not fully verified")
    if live_concerns:
        concerns.extend(part.strip() for part in live_concerns.split(";") if part.strip())
    if has_clearance_signal(job):
        concerns.append("Security clearance required or mentioned")
    if isinstance(job.raw, dict):
        live_title = str(job.raw.get("_live_page_title") or "").lower()
        if "software engineer" in live_title and "infrastructure" not in live_title and "SWE" not in target_role_buckets():
            concerns.append("Live page title is SWE-heavy")
    if is_non_us_remote_location(job.location, job.work_model):
        concerns.append("Non-US remote")
    if not is_target_location(job.location, job.work_model):
        concerns.append("Non-target location")
    if resume_match == 0:
        concerns.append("Low resume match")
    elif resume_match < 3:
        concerns.append("Weak resume match")
    if has_hard_excluded_title(job.title):
        title_lower = job.title.lower()
        if "project manager" in title_lower:
            concerns.append("Project manager, not engineering")
        elif "business analyst" in title_lower:
            concerns.append("Business analyst, not engineering")
        elif "director" in title_lower:
            concerns.append("Director-level role")
        elif "head of" in title_lower:
            concerns.append("Executive/head role")
        elif "coordinator" in title_lower:
            concerns.append("Coordinator, not engineering")
        elif "account executive" in title_lower:
            concerns.append("Account executive, not sales engineering")
        else:
            concerns.append("Non-engineering title")
    if any(term in job.title.lower() for term in ("senior", "sr.", "staff", "principal", "lead ")):
        concerns.append("Senior title")
    if experience.min_years is not None and experience.min_years >= 5:
        concerns.append("High experience requirement")
    elif experience.min_years is not None and experience.min_years > apply_max_years():
        concerns.append("Above target experience")
    if difficulty > 6:
        concerns.append("Hard interview")
    if not job.salary:
        concerns.append("Unknown salary")
    return "; ".join(dict.fromkeys(concerns))


def build_why_apply(job: Job, role_bucket: str, resume_match: int, salary: SalaryEstimate) -> str:
    blob = text_blob(job)
    hits = count_term_hits(blob, user_strength_terms())
    parts: list[str] = []
    if source_confidence_for_job(job) == "high":
        parts.append("direct company ATS")
    if hits:
        parts.extend(hits[:3])
    if role_bucket in target_role_buckets():
        parts.append(role_bucket.lower())
    if is_seattle_area_location(job.location):
        parts.append(target_metro_label())
    elif is_washington_state_location(job.location):
        parts.append(target_region_label())
    elif is_remote_location(job.location, job.work_model):
        parts.append("Remote US")
    if salary.estimated:
        parts.append(f"${round(salary.estimated / 1000)}k estimated salary")
    if resume_match >= 3:
        parts.append(f"{resume_match}% resume match")
    if not parts:
        return "Review manually: limited matching signal."
    label = "Strong fit" if role_bucket in target_role_buckets() and resume_match >= apply_min_resume_match() else "Good review"
    return f"{label}: " + " + ".join(parts[:6]) + "."


def flatten_raw_values(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from flatten_raw_values(item, next_prefix)
    elif isinstance(value, list):
        for index, item in enumerate(value[:50]):
            yield from flatten_raw_values(item, f"{prefix}.{index}" if prefix else str(index))
    else:
        yield prefix, value


def metadata_text_value(raw: dict[str, Any], key_terms: tuple[str, ...]) -> str:
    for key, value in flatten_raw_values(raw):
        key_lower = key.lower()
        if not any(term in key_lower for term in key_terms):
            continue
        if value in (None, ""):
            continue
        if isinstance(value, (str, int, float, bool)):
            text = clean_experience_text(str(value))
            if text and len(text) <= 300:
                return text
    return ""


def richer_board_metadata(job: Job) -> dict[str, str]:
    raw = job.raw if isinstance(job.raw, dict) else {}
    if not raw:
        return {
            "h1b_sponsorship": "",
            "company_size": "",
            "company_industry": "",
            "seniority": "",
            "qualifications": "",
            "applicant_count": "",
        }
    blob = json.dumps(raw, ensure_ascii=False).lower()
    h1b = metadata_text_value(raw, ("h1b", "sponsorship", "visa"))
    if not h1b:
        if "h1b" in blob or "sponsor" in blob:
            h1b = "mentioned"
    qualifications = metadata_text_value(raw, ("qualification", "requirements", "minimum", "preferred"))
    if not qualifications:
        exp = extract_experience(job)
        qualifications = exp.summary if exp.summary != "unknown" else ""
    return {
        "h1b_sponsorship": h1b,
        "company_size": metadata_text_value(raw, ("companysize", "company_size", "size")),
        "company_industry": metadata_text_value(raw, ("industry", "sector")),
        "seniority": metadata_text_value(raw, ("seniority", "level", "joblevel", "newgrad", "new_grad")),
        "qualifications": qualifications,
        "applicant_count": metadata_text_value(raw, ("applicant", "applications")),
    }


def normalized_duplicate_key(job: Job) -> str:
    employer_url = canonical_employer_url_for_job(job)
    if employer_url:
        return "url:" + employer_url.lower().split("?", 1)[0].rstrip("/")
    return "job:" + normalized_job_identity(job.company, job.title)


def normalized_job_identity(company: str, title: str) -> str:
    company_tokens = sorted(company_match_tokens(company))
    title_text = re.sub(r"\([^)]*\)", " ", title.lower())
    title_text = re.sub(r"\b(?:i|ii|iii|iv|v|1|2|3|4|5)\b", " ", title_text)
    title_text = re.sub(r"[^a-z0-9]+", " ", title_text)
    title_text = re.sub(r"\s+", " ", title_text).strip()
    return f"{' '.join(company_tokens)}|{title_text}"


def row_duplicate_group_key(row: sqlite3.Row) -> str:
    return normalized_job_identity(str(row["company"]), str(row["title"]))


def row_export_apply_url(row: sqlite3.Row) -> str:
    return str(row_value(row, "canonical_employer_url") or row_value(row, "live_page_final_url") or row["canonical_url"])


def row_live_verified(row: sqlite3.Row) -> bool:
    return row_live_status(row) == "verified"


def row_verification_rank(row: sqlite3.Row) -> tuple[int, int, int]:
    return (
        1 if row_live_verified(row) else 0,
        1 if bool(row_value(row, "verified_on_company_site", 0)) else 0,
        int(row_value(row, "score", 0) or 0),
    )


def legacy_location_duplicate_key(job: Job) -> str:
    parts = [
        re.sub(r"[^a-z0-9]+", " ", job.company.lower()).strip(),
        re.sub(r"[^a-z0-9]+", " ", job.title.lower()).strip(),
        re.sub(r"[^a-z0-9]+", " ", job.location.lower()).strip(),
    ]
    return "job:" + "|".join(parts)


def score_job(job: Job) -> ScoredJob:
    title = job.title.lower()
    blob = text_blob(job)
    experience = extract_experience(job)
    role_bucket = classify_role(job)
    salary = extract_salary_estimate(job)
    profile = resume_profile_for_role(role_bucket)
    keyword_match = keyword_match_percent(job, profile)
    semantic_match = semantic_match_percent(job, profile)
    resume_match = keyword_match
    missing_keywords = ", ".join(missing_keywords_for_profile(job, profile))
    difficulty = interview_difficulty(job, role_bucket, experience)
    score = 0
    reasons: list[str] = []

    if role_bucket != "Other":
        reasons.append(f"bucket: {role_bucket}")
    else:
        score -= 25
        reasons.append("not target role")

    primary_hits = [term for term in primary_role_terms() if term in title]
    if primary_hits:
        score += 50
        reasons.append(f"role: {primary_hits[0]}")

    customer_hits = [term for term in customer_engineering_terms() if term in title]
    if customer_hits:
        score += 38
        reasons.append(f"customer-facing role: {customer_hits[0]}")

    secondary_hits = [term for term in secondary_role_terms() if term in title]
    if secondary_hits:
        score += 20
        reasons.append(f"secondary role: {secondary_hits[0]}")

    entry_hits = [term for term in ENTRY_TERMS if term in blob]
    if entry_hits:
        score += min(18, 6 * len(entry_hits))
        reasons.append("entry-level signal")

    senior_title_hits = [
        term for term in ("senior", "sr.", "staff", "principal", "lead ") if term in title
    ]
    if senior_title_hits:
        score -= 75
        reasons.append("too senior title")

    if experience.summary != "unknown":
        if is_entryish_experience(experience, apply_max_years()):
            score += 12
            reasons.append(f"exp: {experience.summary}")
        elif experience.min_years is not None and experience.min_years >= 5:
            score -= 75
            reasons.append(f"too senior: {experience.summary}")
        else:
            score -= 35
            reasons.append(f"too senior: {experience.summary}")

    positive_hits = [term for term in positive_terms() if term in blob]
    if positive_hits:
        score += min(36, 4 * len(positive_hits))
        reasons.append("skills: " + ", ".join(positive_hits[:4]))

    argos_hits = count_term_hits(blob, user_strength_terms())
    if len(argos_hits) >= 5:
        score += 20
        reasons.append("profile match: " + ", ".join(argos_hits[:3]))
    elif len(argos_hits) >= 3:
        score += 10
        reasons.append("profile match: " + ", ".join(argos_hits[:3]))

    if resume_match >= 45:
        score += 12
        reasons.append(f"resume match: {resume_match}%")
    elif resume_match >= 25:
        score += 6
        reasons.append(f"resume match: {resume_match}%")
    elif resume_match == 0:
        score -= 18
        reasons.append("low resume match")
    elif resume_match < 3:
        score -= 8
        reasons.append("weak resume match")

    if semantic_match >= 30:
        score += 6
        reasons.append(f"semantic match: {semantic_match}%")
    elif semantic_match >= 15:
        score += 3
        reasons.append(f"semantic match: {semantic_match}%")

    company_bonus, company_reason = preferred_company_bonus(job.company, blob)
    if company_bonus:
        score += company_bonus
        reasons.append(company_reason)

    if salary.score:
        salary_bonus = salary.score
        if resume_match < 10:
            salary_bonus = min(salary_bonus, 6)
        score += salary_bonus
        reasons.append(f"salary score: {salary_bonus:+d}")

    source_confidence = source_confidence_for_job(job)
    if source_confidence == "high":
        score += 8
        reasons.append("high-confidence source")
    elif source_confidence == "medium":
        score += 3
        reasons.append("official API source")
    elif source_confidence == "low":
        score -= 3
        reasons.append("lower-confidence source")

    fresh_score = freshness_score(job)
    if fresh_score:
        score += fresh_score
        reasons.append(f"freshness: {fresh_score:+d}")

    location_lower = job.location.lower()
    work_model_lower = job.work_model.lower()
    if is_seattle_area_location(job.location):
        score += 28
        reasons.append(f"{target_metro_label()} location")
    elif is_remote_location(job.location, job.work_model):
        score += 20
        reasons.append("remote")
    elif is_washington_state_location(job.location):
        score += 10
        reasons.append(target_region_label())
    else:
        score -= 24
        reasons.append("non-target location")

    if "hybrid" in work_model_lower:
        score += 5
    if "on site" in work_model_lower or "onsite" in work_model_lower:
        score += 2

    negative_hits = [term for term in negative_terms() if term in blob]
    if negative_hits:
        score -= min(36, 9 * len(negative_hits))
        reasons.append("penalty: " + ", ".join(negative_hits[:3]))

    swe_title = any(
        term in title
        for term in (
            "software engineer",
            "software developer",
            "application developer",
            "java developer",
            "machine learning engineer",
            "ml engineer",
        )
    )
    if "SWE" not in target_role_buckets() and swe_title and not has_infrastructure_context(blob):
        score -= 35
        reasons.append("not target: SWE/app-dev")

    heavy_devops_hits = [term for term in heavy_devops_terms() if term in blob]
    if "DevOps/SRE" not in target_role_buckets() and heavy_devops_hits and not primary_hits:
        score -= min(20, 5 * len(heavy_devops_hits))
        reasons.append("heavy DevOps/SRE signal")

    if "SWE" not in target_role_buckets() and role_bucket == "SWE" and not has_infrastructure_context(blob):
        score -= 40
        reasons.append("bucket penalty: SWE")

    live_status = live_status_for_job(job)
    live_title = ""
    if isinstance(job.raw, dict):
        live_title = str(job.raw.get("_live_page_title") or "").lower()
    live_title_swe = "software engineer" in live_title and "infrastructure" not in live_title
    if "SWE" not in target_role_buckets() and live_title_swe:
        score -= 70
        reasons.append("live page title is SWE-heavy")

    if live_status == "verified":
        score += 8
        reasons.append("live page verified")
    elif live_status == "expired":
        score -= 140
        reasons.append("expired live page")
    elif live_status == "mismatch":
        score -= 100
        reasons.append("live page mismatch")
    elif live_status in {"blocked", "failed", "no_text"} and source_confidence_for_job(job) in {"low", "unknown"}:
        score -= 30
        reasons.append("unverified low-confidence page")
    elif live_status == "unverified":
        score -= 12
        reasons.append("live page weak match")

    if has_clearance_signal(job):
        score -= 35
        reasons.append("clearance concern")

    if has_hard_excluded_title(job.title):
        score -= 55
        reasons.append("hard title penalty")

    if has_invalid_company(job.company):
        score -= 40
        reasons.append("unknown company")

    if not reasons:
        reasons.append("general technical match")
    concerns = build_concerns(job, role_bucket, experience, resume_match, difficulty)
    return ScoredJob(
        score=score,
        why_match="; ".join(reasons[:6]),
        role_bucket=role_bucket,
        interview_difficulty=difficulty,
        resume_match=resume_match,
        keyword_match=keyword_match,
        semantic_match=semantic_match,
        missing_keywords=missing_keywords,
        best_resume_profile=profile.name,
        estimated_salary=salary.estimated,
        salary_score=salary.score,
        why_apply=build_why_apply(job, role_bucket, resume_match, salary),
        concerns=concerns,
    )


def fit_label(row: sqlite3.Row | dict[str, Any]) -> str:
    score = int(row["score"])
    why = str(row["why_match"]).lower()
    if score >= 55:
        return "strong"
    if score >= 35:
        return "maybe"
    if "not target" in why or "penalty" in why:
        return "off"
    return "low"


def display_key(row: sqlite3.Row) -> str:
    return "|".join(
        re.sub(r"\s+", " ", str(row[key]).lower()).strip()
        for key in ("title", "company", "location")
    )


def normalize_role_arg(role: str) -> str:
    role_lookup = {bucket.lower().replace("/", "_").replace(" ", "_"): bucket for bucket in ROLE_BUCKETS}
    normalized_role = role.lower().replace("/", "_").replace(" ", "_").replace("-", "_")
    bucket = role_lookup.get(normalized_role)
    if bucket is None and role:
        raise ValueError(f"Unsupported role bucket: {role}")
    return bucket or ""


def row_hard_excluded(row: sqlite3.Row) -> bool:
    return has_hard_excluded_title(str(row["title"]))


def row_target_location(row: sqlite3.Row) -> bool:
    return is_target_location(str(row["location"]), str(row["work_model"]))


def row_target_bucket(row: sqlite3.Row) -> bool:
    return str(row["role_bucket"]) in target_role_buckets()


def row_value(row: sqlite3.Row, key: str, default: Any = "") -> Any:
    return row[key] if key in row.keys() and row[key] is not None else default


def row_live_status(row: sqlite3.Row) -> str:
    return str(row_value(row, "live_verification_status", ""))


def row_passes_preset(
    row: sqlite3.Row,
    *,
    preset: str,
    explicit_role: str = "",
    include_security: bool = False,
    include_non_target: bool = False,
    min_resume_match: int | None = None,
    max_difficulty: int | None = None,
) -> bool:
    if preset == "custom":
        return True
    if preset not in {"apply", "review"}:
        raise ValueError(f"Unsupported preset: {preset}")

    role_bucket = str(row["role_bucket"])
    explicit_bucket = normalize_role_arg(explicit_role)
    role_explicitly_requested = bool(explicit_bucket and explicit_bucket == role_bucket)
    resume_match = int(row["resume_match"] or 0)
    estimated_salary = row["estimated_salary"]
    score = int(row["score"] or 0)
    difficulty = int(row["interview_difficulty"] or 10)
    target_location = row_target_location(row)
    live_status = row_live_status(row)
    source_confidence = str(row_value(row, "source_confidence", "unknown"))
    verified = bool(row_value(row, "verified_on_company_site", 0))
    min_years = row_value(row, "experience_min_years", None)
    max_years = row_value(row, "experience_max_years", None)
    concerns = str(row_value(row, "concerns", "")).lower()

    role_is_config_target = role_bucket in target_role_buckets()
    if role_bucket == "Security" and not (role_is_config_target or include_security or role_explicitly_requested):
        return False
    if role_bucket in {"SWE", "DevOps/SRE"} and not (role_is_config_target or include_non_target or role_explicitly_requested):
        return False
    if role_bucket == "Other" and not (role_is_config_target or include_non_target or role_explicitly_requested):
        return False
    if has_invalid_company(str(row["company"])) and not include_non_target:
        return False
    if not include_non_target and row_hard_excluded(row) and explicit_bucket != "Other":
        return False
    if not include_non_target and not target_location:
        return False
    if not include_non_target and live_status in {"expired", "mismatch"}:
        return False

    if preset == "apply":
        required_resume = apply_min_resume_match() if min_resume_match is None else min_resume_match
        required_difficulty = apply_max_difficulty() if max_difficulty is None else max_difficulty
        if role_explicitly_requested and role_bucket in non_target_role_buckets():
            return True
        if not role_target_or_explicit(role_bucket, role_explicitly_requested, include_non_target):
            return False
        if apply_require_live_verification() and not verified and live_status not in {"verified"}:
            return False
        if live_status in {"blocked", "failed", "no_text"} and not verified:
            return False
        if "security clearance required or mentioned" in concerns:
            return False
        if "live page title is swe-heavy" in concerns:
            return False
        if min_years is not None and float(min_years) > apply_max_years():
            return False
        if max_years is not None and float(max_years) > apply_max_years():
            return False
        return resume_match >= required_resume and score >= apply_min_score() and difficulty <= required_difficulty

    required_resume = 0 if min_resume_match is None else min_resume_match
    if resume_match >= max(1, required_resume):
        return True
    strong_location_or_salary = is_seattle_area_location(str(row["location"])) or (
        estimated_salary is not None and int(estimated_salary) >= 100000
    )
    return row_target_bucket(row) and target_location and strong_location_or_salary


def role_target_or_explicit(role_bucket: str, role_explicitly_requested: bool, include_non_target: bool) -> bool:
    return role_bucket in target_role_buckets() or role_explicitly_requested or include_non_target


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_job_id TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT DEFAULT '',
            salary TEXT DEFAULT '',
            work_model TEXT DEFAULT '',
            posted_at TEXT DEFAULT '',
            source_type TEXT DEFAULT '',
            source_confidence TEXT DEFAULT 'unknown',
            verified_on_company_site INTEGER NOT NULL DEFAULT 0,
            canonical_employer_url TEXT DEFAULT '',
            experience_summary TEXT DEFAULT 'unknown',
            experience_min_years REAL,
            experience_max_years REAL,
            role_bucket TEXT DEFAULT 'Other',
            estimated_salary INTEGER,
            salary_score INTEGER NOT NULL DEFAULT 0,
            interview_difficulty INTEGER NOT NULL DEFAULT 5,
            resume_match INTEGER NOT NULL DEFAULT 0,
            keyword_match INTEGER NOT NULL DEFAULT 0,
            semantic_match INTEGER NOT NULL DEFAULT 0,
            missing_keywords TEXT DEFAULT '',
            best_resume_profile TEXT DEFAULT '',
            duplicate_key TEXT DEFAULT '',
            h1b_sponsorship TEXT DEFAULT '',
            company_size TEXT DEFAULT '',
            company_industry TEXT DEFAULT '',
            seniority TEXT DEFAULT '',
            qualifications TEXT DEFAULT '',
            applicant_count TEXT DEFAULT '',
            status_reason TEXT DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_changed_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'seen',
            active INTEGER NOT NULL DEFAULT 1,
            missing_count INTEGER NOT NULL DEFAULT 0,
            score INTEGER NOT NULL DEFAULT 0,
            why_match TEXT DEFAULT '',
            why_apply TEXT DEFAULT '',
            concerns TEXT DEFAULT '',
            live_verified_at TEXT DEFAULT '',
            live_verification_status TEXT DEFAULT '',
            live_verification_score INTEGER NOT NULL DEFAULT 0,
            live_verification_concerns TEXT DEFAULT '',
            live_page_url TEXT DEFAULT '',
            live_page_final_url TEXT DEFAULT '',
            live_page_title TEXT DEFAULT '',
            live_page_hash TEXT DEFAULT '',
            live_page_text TEXT DEFAULT '',
            content_hash TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            UNIQUE(source, source_job_id)
        )
        """
    )
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
    }
    migrations = {
        "experience_summary": "ALTER TABLE jobs ADD COLUMN experience_summary TEXT DEFAULT 'unknown'",
        "experience_min_years": "ALTER TABLE jobs ADD COLUMN experience_min_years REAL",
        "experience_max_years": "ALTER TABLE jobs ADD COLUMN experience_max_years REAL",
        "role_bucket": "ALTER TABLE jobs ADD COLUMN role_bucket TEXT DEFAULT 'Other'",
        "estimated_salary": "ALTER TABLE jobs ADD COLUMN estimated_salary INTEGER",
        "salary_score": "ALTER TABLE jobs ADD COLUMN salary_score INTEGER NOT NULL DEFAULT 0",
        "interview_difficulty": "ALTER TABLE jobs ADD COLUMN interview_difficulty INTEGER NOT NULL DEFAULT 5",
        "resume_match": "ALTER TABLE jobs ADD COLUMN resume_match INTEGER NOT NULL DEFAULT 0",
        "why_apply": "ALTER TABLE jobs ADD COLUMN why_apply TEXT DEFAULT ''",
        "concerns": "ALTER TABLE jobs ADD COLUMN concerns TEXT DEFAULT ''",
        "source_type": "ALTER TABLE jobs ADD COLUMN source_type TEXT DEFAULT ''",
        "source_confidence": "ALTER TABLE jobs ADD COLUMN source_confidence TEXT DEFAULT 'unknown'",
        "verified_on_company_site": "ALTER TABLE jobs ADD COLUMN verified_on_company_site INTEGER NOT NULL DEFAULT 0",
        "canonical_employer_url": "ALTER TABLE jobs ADD COLUMN canonical_employer_url TEXT DEFAULT ''",
        "keyword_match": "ALTER TABLE jobs ADD COLUMN keyword_match INTEGER NOT NULL DEFAULT 0",
        "semantic_match": "ALTER TABLE jobs ADD COLUMN semantic_match INTEGER NOT NULL DEFAULT 0",
        "missing_keywords": "ALTER TABLE jobs ADD COLUMN missing_keywords TEXT DEFAULT ''",
        "best_resume_profile": "ALTER TABLE jobs ADD COLUMN best_resume_profile TEXT DEFAULT ''",
        "duplicate_key": "ALTER TABLE jobs ADD COLUMN duplicate_key TEXT DEFAULT ''",
        "h1b_sponsorship": "ALTER TABLE jobs ADD COLUMN h1b_sponsorship TEXT DEFAULT ''",
        "company_size": "ALTER TABLE jobs ADD COLUMN company_size TEXT DEFAULT ''",
        "company_industry": "ALTER TABLE jobs ADD COLUMN company_industry TEXT DEFAULT ''",
        "seniority": "ALTER TABLE jobs ADD COLUMN seniority TEXT DEFAULT ''",
        "qualifications": "ALTER TABLE jobs ADD COLUMN qualifications TEXT DEFAULT ''",
        "applicant_count": "ALTER TABLE jobs ADD COLUMN applicant_count TEXT DEFAULT ''",
        "status_reason": "ALTER TABLE jobs ADD COLUMN status_reason TEXT DEFAULT ''",
        "live_verified_at": "ALTER TABLE jobs ADD COLUMN live_verified_at TEXT DEFAULT ''",
        "live_verification_status": "ALTER TABLE jobs ADD COLUMN live_verification_status TEXT DEFAULT ''",
        "live_verification_score": "ALTER TABLE jobs ADD COLUMN live_verification_score INTEGER NOT NULL DEFAULT 0",
        "live_verification_concerns": "ALTER TABLE jobs ADD COLUMN live_verification_concerns TEXT DEFAULT ''",
        "live_page_url": "ALTER TABLE jobs ADD COLUMN live_page_url TEXT DEFAULT ''",
        "live_page_final_url": "ALTER TABLE jobs ADD COLUMN live_page_final_url TEXT DEFAULT ''",
        "live_page_title": "ALTER TABLE jobs ADD COLUMN live_page_title TEXT DEFAULT ''",
        "live_page_hash": "ALTER TABLE jobs ADD COLUMN live_page_hash TEXT DEFAULT ''",
        "live_page_text": "ALTER TABLE jobs ADD COLUMN live_page_text TEXT DEFAULT ''",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            conn.execute(statement)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_active_score ON jobs(active, score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_seen ON jobs(last_seen_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_duplicate_key ON jobs(duplicate_key)")
    conn.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def upsert_job(conn: sqlite3.Connection, job: Job, seen_at: str) -> str:
    score = score_job(job)
    experience = extract_experience(job)
    hash_value = content_hash(job)
    raw_json = json.dumps(job.raw or {}, sort_keys=True, ensure_ascii=True)
    source_type = source_type_for_job(job)
    source_confidence = source_confidence_for_job(job)
    verified_on_company_site = 1 if verified_on_company_site_for_job(job) else 0
    canonical_employer_url = canonical_employer_url_for_job(job)
    metadata = richer_board_metadata(job)
    duplicate_key = normalized_duplicate_key(job)
    existing = conn.execute(
        "SELECT * FROM jobs WHERE source = ? AND source_job_id = ?",
        (job.source, job.source_job_id),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO jobs (
                source, source_job_id, canonical_url, title, company, location,
                salary, work_model, posted_at, source_type, source_confidence,
                verified_on_company_site, canonical_employer_url, experience_summary,
                experience_min_years, experience_max_years, role_bucket, estimated_salary,
                salary_score, interview_difficulty, resume_match, keyword_match, semantic_match,
                missing_keywords, best_resume_profile, duplicate_key, h1b_sponsorship,
                company_size, company_industry, seniority, qualifications, applicant_count,
                first_seen_at, last_seen_at,
                last_changed_at, status, active, missing_count, score,
                why_match, why_apply, concerns, content_hash, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'seen', 1, 0, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.source,
                job.source_job_id,
                job.canonical_url,
                job.title,
                job.company,
                job.location,
                job.salary,
                job.work_model,
                job.posted_at,
                source_type,
                source_confidence,
                verified_on_company_site,
                canonical_employer_url,
                experience.summary,
                experience.min_years,
                experience.max_years,
                score.role_bucket,
                score.estimated_salary,
                score.salary_score,
                score.interview_difficulty,
                score.resume_match,
                score.keyword_match,
                score.semantic_match,
                score.missing_keywords,
                score.best_resume_profile,
                duplicate_key,
                metadata["h1b_sponsorship"],
                metadata["company_size"],
                metadata["company_industry"],
                metadata["seniority"],
                metadata["qualifications"],
                metadata["applicant_count"],
                seen_at,
                seen_at,
                seen_at,
                score.score,
                score.why_match,
                score.why_apply,
                score.concerns,
                hash_value,
                raw_json,
            ),
        )
        return "new"

    changed = existing["content_hash"] != hash_value
    conn.execute(
        """
        UPDATE jobs
        SET canonical_url = ?, title = ?, company = ?, location = ?, salary = ?,
            work_model = ?, posted_at = ?, source_type = ?, source_confidence = ?,
            verified_on_company_site = ?, canonical_employer_url = ?, experience_summary = ?,
            experience_min_years = ?, experience_max_years = ?, role_bucket = ?,
            estimated_salary = ?, salary_score = ?, interview_difficulty = ?,
            resume_match = ?, keyword_match = ?, semantic_match = ?,
            missing_keywords = ?, best_resume_profile = ?, duplicate_key = ?,
            h1b_sponsorship = ?, company_size = ?, company_industry = ?,
            seniority = ?, qualifications = ?, applicant_count = ?, last_seen_at = ?,
            last_changed_at = CASE WHEN ? THEN ? ELSE last_changed_at END,
            active = 1, missing_count = 0, score = ?, why_match = ?,
            why_apply = ?, concerns = ?, content_hash = ?, raw_json = ?
        WHERE source = ? AND source_job_id = ?
        """,
        (
            job.canonical_url,
            job.title,
            job.company,
            job.location,
            job.salary,
            job.work_model,
            job.posted_at,
            source_type,
            source_confidence,
            verified_on_company_site,
            canonical_employer_url,
            experience.summary,
            experience.min_years,
            experience.max_years,
            score.role_bucket,
            score.estimated_salary,
            score.salary_score,
            score.interview_difficulty,
            score.resume_match,
            score.keyword_match,
            score.semantic_match,
            score.missing_keywords,
            score.best_resume_profile,
            duplicate_key,
            metadata["h1b_sponsorship"],
            metadata["company_size"],
            metadata["company_industry"],
            metadata["seniority"],
            metadata["qualifications"],
            metadata["applicant_count"],
            seen_at,
            1 if changed else 0,
            seen_at,
            score.score,
            score.why_match,
            score.why_apply,
            score.concerns,
            hash_value,
            raw_json,
            job.source,
            job.source_job_id,
        ),
    )
    return "changed" if changed else "unchanged"


def row_to_job(row: sqlite3.Row) -> Job:
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except json.JSONDecodeError:
        raw = {}
    keys = row.keys()
    live_text = row["live_page_text"] if "live_page_text" in keys else ""
    if any(key in keys for key in ("live_page_text", "live_verification_status", "live_verification_concerns")):
        raw["_live_page_text"] = live_text
        raw["_live_page_title"] = row["live_page_title"] if "live_page_title" in keys else ""
        raw["_live_page_url"] = row["live_page_url"] if "live_page_url" in keys else ""
        raw["_live_verification_status"] = row["live_verification_status"] if "live_verification_status" in keys else ""
        raw["_live_verification_concerns"] = row["live_verification_concerns"] if "live_verification_concerns" in keys else ""
        raw["_live_verification_score"] = row["live_verification_score"] if "live_verification_score" in keys else 0
    return Job(
        source=row["source"],
        source_job_id=row["source_job_id"],
        canonical_url=row["canonical_url"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        salary=row["salary"],
        work_model=row["work_model"],
        posted_at=row["posted_at"],
        raw=raw,
    )


def rescore_jobs(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    for row in rows:
        update_scored_row(conn, row)
    conn.commit()
    return len(rows)


def update_scored_row(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    job = row_to_job(row)
    scored = score_job(job)
    experience = extract_experience(job)
    source_type = source_type_for_job(job)
    source_confidence = source_confidence_for_job(job)
    verified_on_company_site = 1 if verified_on_company_site_for_job(job) else 0
    canonical_employer_url = canonical_employer_url_for_job(job)
    metadata = richer_board_metadata(job)
    duplicate_key = normalized_duplicate_key(job)
    conn.execute(
        """
        UPDATE jobs
        SET score = ?, why_match = ?, experience_summary = ?,
            experience_min_years = ?, experience_max_years = ?,
            role_bucket = ?, estimated_salary = ?, salary_score = ?,
            interview_difficulty = ?, resume_match = ?,
            keyword_match = ?, semantic_match = ?, missing_keywords = ?,
            best_resume_profile = ?, duplicate_key = ?, h1b_sponsorship = ?,
            company_size = ?, company_industry = ?, seniority = ?,
            qualifications = ?, applicant_count = ?,
            why_apply = ?, concerns = ?, source_type = ?,
            source_confidence = ?, verified_on_company_site = ?,
            canonical_employer_url = ?
        WHERE id = ?
        """,
        (
            scored.score,
            scored.why_match,
            experience.summary,
            experience.min_years,
            experience.max_years,
            scored.role_bucket,
            scored.estimated_salary,
            scored.salary_score,
            scored.interview_difficulty,
            scored.resume_match,
            scored.keyword_match,
            scored.semantic_match,
            scored.missing_keywords,
            scored.best_resume_profile,
            duplicate_key,
            metadata["h1b_sponsorship"],
            metadata["company_size"],
            metadata["company_industry"],
            metadata["seniority"],
            metadata["qualifications"],
            metadata["applicant_count"],
            scored.why_apply,
            scored.concerns,
            source_type,
            source_confidence,
            verified_on_company_site,
            canonical_employer_url,
            row["id"],
        ),
    )


def verify_job(row: sqlite3.Row, *, max_urls: int = 8) -> LiveVerification:
    job = row_to_job(row)
    candidates = candidate_apply_urls_for_job(job)
    best: tuple[str, str, str, str, int] | None = None
    errors: list[str] = []
    for url in candidates[:max_urls]:
        try:
            status, final_url, page_title, text = fetch_page_for_verification(url)
        except HTTPError as exc:
            errors.append(f"{url}: HTTP {exc.code}")
            continue
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f"{url}: {type(exc).__name__}")
            continue
        if best is None or len(text) > len(best[3]):
            best = (url, final_url, page_title, text, status)
        if len(text) >= 4500:
            break

    if best is None:
        status = "blocked" if errors else "no_url"
        concerns = "; ".join(errors[:3]) if errors else "No candidate apply URL"
        return LiveVerification(status, "", "", "", "", "", -50, concerns)

    url, final_url, page_title, text, http_status = best
    if not text:
        return LiveVerification("no_text", url, final_url, page_title, "", "", -50, "No extracted page text")
    quality_status, quality_score, concerns = verification_quality(job, text, page_title)
    text_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    if http_status >= 400 and quality_status == "unverified":
        quality_status = "blocked"
        concerns.append(f"HTTP {http_status}")
    return LiveVerification(
        quality_status,
        url,
        final_url,
        page_title,
        text[:60000],
        text_hash,
        quality_score,
        "; ".join(dict.fromkeys(concerns)),
    )


def verify_jobs(conn: sqlite3.Connection, rows: Iterable[sqlite3.Row]) -> dict[str, int]:
    totals: dict[str, int] = {}
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for row in rows:
        result = verify_job(row)
        totals[result.status] = totals.get(result.status, 0) + 1
        conn.execute(
            """
            UPDATE jobs
            SET live_verified_at = ?, live_verification_status = ?,
                live_verification_score = ?, live_verification_concerns = ?,
                live_page_url = ?, live_page_final_url = ?, live_page_title = ?,
                live_page_hash = ?, live_page_text = ?
            WHERE id = ?
            """,
            (
                verified_at,
                result.status,
                result.score,
                result.concerns,
                result.url,
                result.final_url,
                result.page_title,
                result.text_hash,
                result.text,
                row["id"],
            ),
        )
        refreshed = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        if refreshed is not None:
            update_scored_row(conn, refreshed)
    conn.commit()
    return totals


def mark_missing(conn: sqlite3.Connection, source: str, seen_ids: set[str]) -> int:
    rows = conn.execute(
        "SELECT source_job_id, missing_count FROM jobs WHERE source = ? AND active = 1",
        (source,),
    ).fetchall()
    updated = 0
    for row in rows:
        if row["source_job_id"] in seen_ids:
            continue
        missing_count = int(row["missing_count"]) + 1
        conn.execute(
            "UPDATE jobs SET missing_count = ?, active = ? WHERE source = ? AND source_job_id = ?",
            (missing_count, 0 if missing_count >= 2 else 1, source, row["source_job_id"]),
        )
        updated += 1
    return updated


def extract_next_data(html_text: str) -> dict[str, Any]:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html_text,
        re.DOTALL,
    )
    if not match:
        raise ValueError("Could not find __NEXT_DATA__")
    return json.loads(html.unescape(match.group(1)))


def extract_newgrad_categories(html_text: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r'<h2[^>]*data-job-path="([^"]+)"[^>]*'
        r'airtable-link="([^"]+)"[^>]*short-link="([^"]+)"[^>]*>'
        r"(.*?)</h2>",
        re.DOTALL,
    )
    categories: list[dict[str, str]] = []
    for match in pattern.finditer(html_text):
        path, airtable, short, raw_name = match.groups()
        name = html.unescape(re.sub(r"<[^>]+>", "", raw_name)).strip()
        categories.append(
            {
                "path": path.strip("/"),
                "airtable": html.unescape(airtable),
                "short": short,
                "name": name,
            }
        )
    return categories


def collect_newgrad(source: dict[str, Any]) -> list[Job]:
    html_text = fetch_text(NEWGRAD_LANDING_URL)
    categories = extract_newgrad_categories(html_text)
    allowed = set(source.get("categories") or [])
    jobs: list[Job] = []
    for category in categories:
        if allowed and category["path"] not in allowed:
            continue
        path = category["path"]
        page = fetch_text(NEWGRAD_MINISITE_URL.format(path=path))
        data = extract_next_data(page)
        props = data.get("props", {}).get("pageProps", {})
        for raw in props.get("initialJobs") or []:
            source_job_id = str(raw.get("id") or raw.get("jobId") or "")
            if not source_job_id:
                source_job_id = stable_source_job_id(raw.get("title"), raw.get("company"), raw.get("location"))
            raw_with_category = enrich_raw_with_source(
                source,
                {**raw, "category_path": path, "category_name": category["name"]},
            )
            jobs.append(
                Job(
                    source=source["name"],
                    source_job_id=source_job_id,
                    canonical_url=raw.get("applyUrl") or f"https://jobright.ai/jobs/info/{source_job_id}",
                    title=raw.get("title") or "",
                    company=raw.get("company") or "",
                    location=raw.get("location") or "",
                    salary=normalize_salary(raw.get("salary")),
                    work_model=raw.get("workModel") or "",
                    posted_at=parse_datetime(raw.get("postedDate")),
                    raw=raw_with_category,
                )
            )
        time.sleep(float(source.get("sleep_seconds", 0.4)))
    return jobs


def collect_greenhouse(source: dict[str, Any]) -> list[Job]:
    board = source["board"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    payload = fetch_json(url)
    jobs: list[Job] = []
    for raw in payload.get("jobs", []):
        location = greenhouse_metadata_location(raw.get("metadata")) or (raw.get("location") or {}).get("name", "")
        source_job_id = str(raw.get("id") or stable_source_job_id(raw.get("title"), source.get("company"), location))
        jobs.append(
            Job(
                source=source["name"],
                source_job_id=source_job_id,
                canonical_url=raw.get("absolute_url") or "",
                title=raw.get("title") or "",
                company=source.get("company") or board,
                location=location,
                salary=normalize_salary(raw.get("metadata")),
                work_model=infer_work_model(location, raw.get("content") or ""),
                posted_at=parse_datetime(raw.get("updated_at")),
                raw=enrich_raw_with_source(source, raw),
            )
        )
    return jobs


def greenhouse_metadata_location(metadata: Any) -> str:
    if not isinstance(metadata, list):
        return ""
    locations: list[str] = []
    for item in metadata:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        if "location" not in name:
            continue
        value = item.get("value")
        if isinstance(value, list):
            locations.extend(str(part) for part in value if part)
        elif value:
            locations.append(str(value))
    return "; ".join(locations)


def collect_lever(source: dict[str, Any]) -> list[Job]:
    company = source["company_slug"]
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    payload = fetch_json(url)
    jobs: list[Job] = []
    for raw in payload:
        categories = raw.get("categories") or {}
        location = categories.get("location") or raw.get("workplaceType") or ""
        source_job_id = str(raw.get("id") or stable_source_job_id(raw.get("text"), company, location))
        jobs.append(
            Job(
                source=source["name"],
                source_job_id=source_job_id,
                canonical_url=raw.get("hostedUrl") or raw.get("applyUrl") or "",
                title=raw.get("text") or "",
                company=source.get("company") or company,
                location=location,
                salary=normalize_salary(raw.get("salaryRange")),
                work_model=raw.get("workplaceType") or "",
                posted_at=parse_datetime(raw.get("createdAt")),
                raw=enrich_raw_with_source(source, raw),
            )
        )
    return jobs


def collect_ashby(source: dict[str, Any]) -> list[Job]:
    org = source["organization"]
    payload = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
    raw_jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    jobs: list[Job] = []
    for raw in raw_jobs or []:
        location = raw.get("locationName") or raw.get("location") or ""
        source_job_id = str(raw.get("id") or stable_source_job_id(raw.get("title"), org, location))
        jobs.append(
            Job(
                source=source["name"],
                source_job_id=source_job_id,
                canonical_url=raw.get("jobUrl") or raw.get("applyUrl") or "",
                title=raw.get("title") or "",
                company=source.get("company") or org,
                location=location,
                salary=normalize_salary(raw.get("compensation")),
                work_model=infer_work_model(location, json.dumps(raw, ensure_ascii=True)),
                posted_at=parse_datetime(raw.get("publishedAt") or raw.get("updatedAt")),
                raw=enrich_raw_with_source(source, raw),
            )
        )
    return jobs


def collect_smartrecruiters(source: dict[str, Any]) -> list[Job]:
    company = source["company_slug"]
    url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100"
    payload = fetch_json(url)
    raw_jobs = payload.get("content") or []
    jobs: list[Job] = []
    for raw in raw_jobs:
        location = raw.get("location", {})
        location_text = ", ".join(
            part for part in (location.get("city"), location.get("region"), location.get("country")) if part
        )
        source_job_id = str(raw.get("id") or stable_source_job_id(raw.get("name"), company, location_text))
        jobs.append(
            Job(
                source=source["name"],
                source_job_id=source_job_id,
                canonical_url=raw.get("ref") or raw.get("postingUrl") or "",
                title=raw.get("name") or "",
                company=source.get("company") or company,
                location=location_text,
                salary="",
                work_model=infer_work_model(location_text, json.dumps(raw, ensure_ascii=True)),
                posted_at=parse_datetime(raw.get("releasedDate") or raw.get("updatedOn")),
                raw=enrich_raw_with_source(source, raw),
            )
        )
    return jobs


def collect_usajobs(source: dict[str, Any]) -> list[Job]:
    api_key = source.get("api_key") or os.environ.get("USAJOBS_API_KEY")
    email = source.get("email") or os.environ.get("USAJOBS_EMAIL")
    if not api_key or not email:
        raise ValueError("USAJOBS requires USAJOBS_API_KEY and USAJOBS_EMAIL or source config values")
    jobs: list[Job] = []
    queries = source.get("queries") or [source.get("keyword", "network engineer")]
    locations = source.get("locations") or [source.get("location", "Seattle, Washington")]
    for keyword in queries:
        for location in locations:
            query = {
                "Keyword": keyword,
                "LocationName": location,
                "ResultsPerPage": source.get("results_per_page", 50),
            }
            url = "https://data.usajobs.gov/api/search?" + urlencode(query)
            payload = fetch_json(
                url,
                headers={
                    "Host": "data.usajobs.gov",
                    "User-Agent": email,
                    "Authorization-Key": api_key,
                },
            )
            items = payload.get("SearchResult", {}).get("SearchResultItems", [])
            for item in items:
                raw = item.get("MatchedObjectDescriptor", {})
                locations_raw = raw.get("PositionLocation") or []
                location_text = "; ".join(loc.get("LocationName", "") for loc in locations_raw if loc.get("LocationName"))
                salary = ""
                remuneration = raw.get("PositionRemuneration") or []
                if remuneration:
                    salary = normalize_salary(
                        {
                            "min": remuneration[0].get("MinimumRange"),
                            "max": remuneration[0].get("MaximumRange"),
                            "unit": remuneration[0].get("RateIntervalCode"),
                        }
                    )
                source_job_id = str(raw.get("PositionID") or raw.get("PositionURI") or raw.get("ApplicationCloseDate"))
                jobs.append(
                    Job(
                        source=source["name"],
                        source_job_id=source_job_id,
                        canonical_url=raw.get("PositionURI") or "",
                        title=raw.get("PositionTitle") or "",
                        company=(raw.get("OrganizationName") or source.get("company") or "USAJOBS"),
                        location=location_text,
                        salary=salary,
                        work_model=infer_work_model(location_text, json.dumps(raw, ensure_ascii=True)),
                        posted_at=parse_datetime(raw.get("PublicationStartDate")),
                        raw=enrich_raw_with_source(source, {**raw, "query": keyword, "configured_location": location}),
                    )
                )
            time.sleep(float(source.get("sleep_seconds", 0)))
    return jobs


def collect_remotive(source: dict[str, Any]) -> list[Job]:
    limit = int(source.get("limit", 100))
    queries = source.get("queries") or [""]
    keywords = [term.lower() for term in source.get("keywords", [])]
    jobs: list[Job] = []
    for query in queries:
        params: dict[str, Any] = {"limit": limit}
        if query:
            params["search"] = query
        url = "https://remotive.com/api/remote-jobs?" + urlencode(params)
        payload = fetch_json(url)
        for raw in payload.get("jobs", []):
            blob = " ".join(
                str(part or "")
                for part in (
                    raw.get("title"),
                    raw.get("category"),
                    raw.get("tags"),
                    raw.get("description"),
                )
            ).lower()
            if keywords and not any(keyword in blob for keyword in keywords):
                continue
            location = raw.get("candidate_required_location") or "Remote"
            source_job_id = str(raw.get("id") or stable_source_job_id(raw.get("title"), raw.get("company_name"), location))
            raw_with_query = enrich_raw_with_source(source, {**raw, "query": query})
            jobs.append(
                Job(
                    source=source["name"],
                    source_job_id=source_job_id,
                    canonical_url=raw.get("url") or "",
                    title=raw.get("title") or "",
                    company=raw.get("company_name") or source.get("company") or "Remotive",
                    location=location,
                    salary=normalize_salary(raw.get("salary")),
                    work_model="Remote",
                    posted_at=parse_datetime(raw.get("publication_date")),
                    raw=raw_with_query,
                )
            )
        time.sleep(float(source.get("sleep_seconds", 0)))
    return jobs


def collect_arbeitnow(source: dict[str, Any]) -> list[Job]:
    pages = int(source.get("pages", 1))
    keywords = [term.lower() for term in source.get("keywords", [])]
    remote_required = bool(source.get("remote_required", True))
    jobs: list[Job] = []
    for page in range(1, pages + 1):
        url = "https://www.arbeitnow.com/api/job-board-api"
        if page > 1:
            url += "?" + urlencode({"page": page})
        payload = fetch_json(url)
        for raw in payload.get("data", []):
            location = raw.get("location") or ""
            is_remote = bool(raw.get("remote"))
            if remote_required and not is_remote and not is_target_location(location, ""):
                continue
            blob = " ".join(
                str(part or "")
                for part in (
                    raw.get("title"),
                    raw.get("tags"),
                    raw.get("description"),
                )
            ).lower()
            if keywords and not any(keyword in blob for keyword in keywords):
                continue
            source_job_id = str(raw.get("slug") or stable_source_job_id(raw.get("title"), raw.get("company_name"), location))
            jobs.append(
                Job(
                    source=source["name"],
                    source_job_id=source_job_id,
                    canonical_url=raw.get("url") or "",
                    title=raw.get("title") or "",
                    company=raw.get("company_name") or source.get("company") or "Arbeitnow",
                    location=location or ("Remote" if is_remote else ""),
                    salary=normalize_salary(raw.get("salary")),
                    work_model="Remote" if is_remote else infer_work_model(location, json.dumps(raw, ensure_ascii=True)),
                    posted_at=parse_datetime(raw.get("created_at")),
                    raw=enrich_raw_with_source(source, raw),
                )
            )
        time.sleep(float(source.get("sleep_seconds", 0)))
    return jobs


def collect_adzuna(source: dict[str, Any]) -> list[Job]:
    app_id = source.get("app_id") or os.environ.get("ADZUNA_APP_ID")
    app_key = source.get("app_key") or os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise ValueError("Adzuna requires ADZUNA_APP_ID and ADZUNA_APP_KEY or source config values")
    country = source.get("country", "us")
    queries = source.get("queries") or [source.get("query", "network engineer")]
    locations = source.get("locations") or [source.get("location", "Seattle, WA")]
    pages = int(source.get("pages", 1))
    results_per_page = int(source.get("results_per_page", 50))
    jobs: list[Job] = []
    for query in queries:
        for location in locations:
            for page in range(1, pages + 1):
                params: dict[str, Any] = {
                    "app_id": app_id,
                    "app_key": app_key,
                    "what": query,
                    "where": location,
                    "results_per_page": results_per_page,
                    "content-type": "application/json",
                }
                if source.get("max_days_old"):
                    params["max_days_old"] = source["max_days_old"]
                url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}?" + urlencode(params)
                payload = fetch_json(url)
                for raw in payload.get("results", []):
                    raw_location = raw.get("location") or {}
                    location_text = raw_location.get("display_name") or location
                    company = raw.get("company") or {}
                    source_job_id = str(raw.get("id") or stable_source_job_id(raw.get("title"), company.get("display_name"), location_text))
                    salary = normalize_salary(
                        {
                            "min": raw.get("salary_min"),
                            "max": raw.get("salary_max"),
                            "unit": raw.get("salary_is_predicted") and "predicted",
                        }
                    )
                    raw_with_query = enrich_raw_with_source(
                        source,
                        {**raw, "query": query, "configured_location": location},
                    )
                    jobs.append(
                        Job(
                            source=source["name"],
                            source_job_id=source_job_id,
                            canonical_url=raw.get("redirect_url") or "",
                            title=raw.get("title") or "",
                            company=company.get("display_name") or source.get("company") or "Adzuna",
                            location=location_text,
                            salary=salary,
                            work_model=infer_work_model(location_text, raw.get("description") or ""),
                            posted_at=parse_datetime(raw.get("created")),
                            raw=raw_with_query,
                        )
                    )
                time.sleep(float(source.get("sleep_seconds", 0)))
    return jobs


def collect_jobicy(source: dict[str, Any]) -> list[Job]:
    params: dict[str, Any] = {
        "count": int(source.get("count", 100)),
        "geo": source.get("geo", "usa"),
    }
    if source.get("industry"):
        params["industry"] = source["industry"]
    if source.get("tag"):
        params["tag"] = source["tag"]
    keywords = [term.lower() for term in source.get("keywords", [])]
    payload = fetch_json("https://jobicy.com/api/v2/remote-jobs?" + urlencode(params))
    jobs: list[Job] = []
    for raw in payload.get("jobs", []):
        blob = " ".join(
            str(part or "")
            for part in (
                raw.get("jobTitle"),
                raw.get("jobIndustry"),
                raw.get("jobLevel"),
                raw.get("jobExcerpt"),
                raw.get("jobDescription"),
            )
        ).lower()
        if keywords and not any(keyword in blob for keyword in keywords):
            continue
        location = raw.get("jobGeo") or "Remote"
        source_job_id = str(raw.get("id") or stable_source_job_id(raw.get("jobTitle"), raw.get("companyName"), location))
        salary = normalize_salary(
            {
                "min": raw.get("salaryMin"),
                "max": raw.get("salaryMax"),
                "unit": raw.get("salaryPeriod") or raw.get("salaryCurrency"),
            }
        )
        jobs.append(
            Job(
                source=source["name"],
                source_job_id=source_job_id,
                canonical_url=raw.get("url") or "",
                title=raw.get("jobTitle") or "",
                company=raw.get("companyName") or source.get("company") or "Jobicy",
                location=location,
                salary=salary,
                work_model="Remote",
                posted_at=parse_datetime(raw.get("pubDate")),
                raw=enrich_raw_with_source(source, raw),
            )
        )
    return jobs


def github_raw_url(url: str) -> str:
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)$", url)
    if match:
        owner, repo, branch, path = match.groups()
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    return url


def split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [html.unescape(re.sub(r"<[^>]+>", "", part).strip()) for part in stripped.strip("|").split("|")]


def markdown_link_url(value: str) -> str:
    match = re.search(r"\[[^\]]+\]\((https?://[^)]+)\)", value)
    return match.group(1) if match else ""


def clean_markdown_cell(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\*\*|__", "", value)
    value = value.replace("`", "")
    value = html.unescape(value)
    if value.strip() in {"↳", "?", "-", "--", "n/a", "N/A"}:
        return ""
    return clean_experience_text(value)


def find_column(headers: list[str], *names: str) -> int | None:
    normalized = [re.sub(r"[^a-z0-9]+", "", header.lower()) for header in headers]
    for name in names:
        target = re.sub(r"[^a-z0-9]+", "", name.lower())
        for index, header in enumerate(normalized):
            if target == header or target in header:
                return index
    return None


def collect_github_markdown(source: dict[str, Any]) -> list[Job]:
    text = fetch_text(github_raw_url(source["url"]))
    company_idx = role_idx = location_idx = apply_idx = date_idx = salary_idx = None
    headers: list[str] = []
    jobs: list[Job] = []
    previous_company = ""
    for line in text.splitlines():
        cells = split_markdown_row(line)
        if not cells:
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells):
            continue
        lowered = [cell.lower() for cell in cells]
        if not headers and any("company" in cell for cell in lowered) and any(
            term in " ".join(lowered) for term in ("role", "title", "position")
        ):
            headers = cells
            company_idx = find_column(headers, "company")
            role_idx = find_column(headers, "role", "title", "position")
            location_idx = find_column(headers, "location")
            apply_idx = find_column(headers, "application", "apply", "link")
            date_idx = find_column(headers, "date", "posted")
            salary_idx = find_column(headers, "salary", "compensation")
            continue
        if not headers or company_idx is None or role_idx is None:
            continue
        if len(cells) <= max(company_idx, role_idx):
            continue
        company = clean_markdown_cell(cells[company_idx]) or previous_company
        title = clean_markdown_cell(cells[role_idx])
        if not company or not title:
            continue
        previous_company = company
        location = clean_markdown_cell(cells[location_idx]) if location_idx is not None and location_idx < len(cells) else ""
        salary = clean_markdown_cell(cells[salary_idx]) if salary_idx is not None and salary_idx < len(cells) else ""
        apply_cell = cells[apply_idx] if apply_idx is not None and apply_idx < len(cells) else ""
        title_cell = cells[role_idx] if role_idx is not None and role_idx < len(cells) else ""
        canonical_url = markdown_link_url(apply_cell) or markdown_link_url(title_cell) or source.get("fallback_url", source["url"])
        posted = clean_markdown_cell(cells[date_idx]) if date_idx is not None and date_idx < len(cells) else ""
        raw = enrich_raw_with_source(
            source,
            {
                "source_url": source["url"],
                "row": cells,
                "headers": headers,
                "apply_cell": apply_cell,
            },
        )
        jobs.append(
            Job(
                source=source["name"],
                source_job_id=stable_source_job_id(source["name"], canonical_url, title, location),
                canonical_url=canonical_url,
                title=title,
                company=company,
                location=location,
                salary=salary,
                work_model=infer_work_model(location, " ".join(cells)),
                posted_at=parse_datetime(posted),
                raw=raw,
            )
        )
    return jobs


def collect_workable(source: dict[str, Any]) -> list[Job]:
    account = source["account"]
    url = f"https://apply.workable.com/api/v3/accounts/{account}/jobs"
    payload = fetch_json_post(url, {"query": source.get("query", ""), "location": [], "department": []})
    raw_jobs = payload.get("results") or payload.get("jobs") or []
    jobs: list[Job] = []
    for raw in raw_jobs:
        location_data = raw.get("location") or {}
        location = ", ".join(str(part) for part in (
            location_data.get("city"),
            location_data.get("region"),
            location_data.get("country"),
        ) if part)
        source_job_id = str(raw.get("id") or raw.get("shortcode") or stable_source_job_id(raw.get("title"), account, location))
        canonical_url = raw.get("url") or f"https://apply.workable.com/{account}/j/{raw.get('shortcode')}/"
        jobs.append(
            Job(
                source=source["name"],
                source_job_id=source_job_id,
                canonical_url=canonical_url,
                title=raw.get("title") or "",
                company=source.get("company") or account,
                location=location or raw.get("location_str") or "",
                salary=normalize_salary(raw.get("salary")),
                work_model=infer_work_model(location, json.dumps(raw, ensure_ascii=True)),
                posted_at=parse_datetime(raw.get("published_on") or raw.get("created_at")),
                raw=enrich_raw_with_source(source, raw),
            )
        )
    return jobs


def collect_recruitee(source: dict[str, Any]) -> list[Job]:
    company = source["company_slug"]
    payload = fetch_json(f"https://{company}.recruitee.com/api/offers/")
    raw_jobs = payload.get("offers") or payload.get("jobs") or []
    jobs: list[Job] = []
    for raw in raw_jobs:
        locations = raw.get("locations") or []
        location_parts: list[str] = []
        for loc in locations:
            if isinstance(loc, dict):
                location_parts.append(", ".join(str(part) for part in (loc.get("city"), loc.get("state"), loc.get("country")) if part))
            elif loc:
                location_parts.append(str(loc))
        location = "; ".join(part for part in location_parts if part)
        source_job_id = str(raw.get("id") or raw.get("slug") or stable_source_job_id(raw.get("title"), company, location))
        jobs.append(
            Job(
                source=source["name"],
                source_job_id=source_job_id,
                canonical_url=raw.get("careers_url") or raw.get("url") or "",
                title=raw.get("title") or "",
                company=source.get("company") or company,
                location=location,
                salary=normalize_salary(raw.get("salary")),
                work_model=infer_work_model(location, json.dumps(raw, ensure_ascii=True)),
                posted_at=parse_datetime(raw.get("published_at") or raw.get("created_at")),
                raw=enrich_raw_with_source(source, raw),
            )
        )
    return jobs


def collect_bamboohr(source: dict[str, Any]) -> list[Job]:
    subdomain = source["subdomain"]
    payload = fetch_json(f"https://{subdomain}.bamboohr.com/careers/list")
    raw_jobs = payload.get("result") if isinstance(payload, dict) else payload
    jobs: list[Job] = []
    for raw in raw_jobs or []:
        location = raw.get("location") or ""
        source_job_id = str(raw.get("id") or stable_source_job_id(raw.get("jobOpeningName"), subdomain, location))
        jobs.append(
            Job(
                source=source["name"],
                source_job_id=source_job_id,
                canonical_url=raw.get("url") or f"https://{subdomain}.bamboohr.com/careers/{source_job_id}",
                title=raw.get("jobOpeningName") or raw.get("title") or "",
                company=source.get("company") or subdomain,
                location=location,
                salary=normalize_salary(raw.get("salary")),
                work_model=infer_work_model(location, json.dumps(raw, ensure_ascii=True)),
                posted_at=parse_datetime(raw.get("datePosted") or raw.get("postedDate")),
                raw=enrich_raw_with_source(source, raw),
            )
        )
    return jobs


def collect_remoteok(source: dict[str, Any]) -> list[Job]:
    keywords = [term.lower() for term in source.get("keywords", [])]
    payload = fetch_json("https://remoteok.com/api", headers={"Accept": "application/json"})
    jobs: list[Job] = []
    for raw in payload:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        blob = " ".join(
            str(part or "")
            for part in (
                raw.get("position"),
                raw.get("description"),
                raw.get("tags"),
                raw.get("location"),
            )
        ).lower()
        if keywords and not any(keyword in blob for keyword in keywords):
            continue
        salary = normalize_salary({"min": raw.get("salary_min"), "max": raw.get("salary_max"), "unit": "yr"})
        jobs.append(
            Job(
                source=source["name"],
                source_job_id=str(raw.get("id")),
                canonical_url=raw.get("url") or f"https://remoteok.com/remote-jobs/{raw.get('id')}",
                title=raw.get("position") or "",
                company=raw.get("company") or source.get("company") or "Remote OK",
                location=raw.get("location") or "Remote",
                salary=salary,
                work_model="Remote",
                posted_at=parse_datetime(raw.get("date")),
                raw=enrich_raw_with_source(source, raw),
            )
        )
    return jobs


def collect_rss(source: dict[str, Any]) -> list[Job]:
    text = fetch_text(source["url"])
    root = ET.fromstring(text)
    keywords = [term.lower() for term in source.get("keywords", [])]
    jobs: list[Job] = []
    for item in root.findall(".//item"):
        raw = {child.tag: child.text or "" for child in list(item)}
        title = raw.get("title", "")
        description = raw.get("description", "")
        blob = f"{title} {description}".lower()
        if keywords and not any(keyword in blob for keyword in keywords):
            continue
        link = raw.get("link", "")
        company = source.get("company") or ""
        if " at " in title:
            title_part, company_part = title.rsplit(" at ", 1)
            title = title_part.strip()
            company = company_part.strip()
        jobs.append(
            Job(
                source=source["name"],
                source_job_id=stable_source_job_id(source["name"], link, title),
                canonical_url=link,
                title=title,
                company=company or source.get("default_company") or "RSS",
                location=source.get("location", "Remote"),
                salary="",
                work_model=source.get("work_model", "Remote"),
                posted_at=parse_datetime(raw.get("pubDate", "")),
                raw=enrich_raw_with_source(source, raw),
            )
        )
    return jobs


def collect_themuse(source: dict[str, Any]) -> list[Job]:
    pages = int(source.get("pages", 1))
    keywords = source.get("queries") or [source.get("query", "network engineer")]
    locations = source.get("locations") or [source.get("location", "Seattle, WA")]
    jobs: list[Job] = []
    for query in keywords:
        for location in locations:
            for page in range(pages):
                params = {"page": page, "descending": "true", "q": query}
                if location:
                    params["location"] = location
                payload = fetch_json("https://www.themuse.com/api/public/jobs?" + urlencode(params))
                for raw in payload.get("results", []):
                    locations_raw = raw.get("locations") or []
                    location_text = "; ".join(loc.get("name", "") for loc in locations_raw if isinstance(loc, dict) and loc.get("name")) or location
                    company_raw = raw.get("company") or {}
                    jobs.append(
                        Job(
                            source=source["name"],
                            source_job_id=str(raw.get("id") or stable_source_job_id(raw.get("name"), company_raw.get("name"), location_text)),
                            canonical_url=raw.get("refs", {}).get("landing_page") or "",
                            title=raw.get("name") or "",
                            company=company_raw.get("name") or source.get("company") or "The Muse",
                            location=location_text,
                            salary="",
                            work_model=infer_work_model(location_text, json.dumps(raw, ensure_ascii=True)),
                            posted_at=parse_datetime(raw.get("publication_date")),
                            raw=enrich_raw_with_source(source, {**raw, "query": query, "configured_location": location}),
                        )
                    )
                time.sleep(float(source.get("sleep_seconds", 0)))
    return jobs


def collect_findwork(source: dict[str, Any]) -> list[Job]:
    api_key = source.get("api_key") or os.environ.get("FINDWORK_API_KEY")
    if not api_key:
        raise ValueError("FindWork requires FINDWORK_API_KEY or source api_key")
    retries = int(source.get("retries", 1))
    retry_sleep = float(source.get("retry_sleep_seconds", 10))
    queries = source.get("queries") or [source.get("query", "network engineer")]
    locations = source.get("locations") or [source.get("location", "Seattle, WA")]
    jobs: list[Job] = []
    for query in queries:
        for location in locations:
            params = {"search": query}
            if location:
                params["location"] = location
            url = "https://findwork.dev/api/jobs/?" + urlencode(params)
            payload = fetch_json(
                url,
                headers={"Authorization": auth_header_value(api_key, "Token")},
                retries=retries,
                retry_sleep=retry_sleep,
            )
            raw_jobs = payload.get("results") if isinstance(payload, dict) else payload
            for raw in raw_jobs or []:
                location_text = raw.get("location") or location
                company = raw.get("company_name") or raw.get("company") or {}
                company_name = company.get("name") if isinstance(company, dict) else str(company or "")
                jobs.append(
                    Job(
                        source=source["name"],
                        source_job_id=str(raw.get("id") or stable_source_job_id(raw.get("role"), company_name, location_text)),
                        canonical_url=raw.get("url") or "",
                        title=raw.get("role") or raw.get("title") or "",
                        company=company_name or source.get("company") or "FindWork",
                        location=location_text,
                        salary=normalize_salary(raw.get("salary")),
                        work_model=infer_work_model(location_text, json.dumps(raw, ensure_ascii=True)),
                        posted_at=parse_datetime(raw.get("date_posted") or raw.get("created_at")),
                        raw=enrich_raw_with_source(source, {**raw, "query": query, "configured_location": location}),
                    )
                )
            time.sleep(float(source.get("sleep_seconds", 0)))
    return jobs


def collect_careeronestop(source: dict[str, Any]) -> list[Job]:
    user_id = source.get("user_id") or os.environ.get("CAREERONESTOP_USER_ID")
    token = source.get("api_token") or os.environ.get("CAREERONESTOP_API_TOKEN")
    if not user_id or not token:
        raise ValueError("CareerOneStop requires CAREERONESTOP_USER_ID and CAREERONESTOP_API_TOKEN")
    retries = int(source.get("retries", 1))
    retry_sleep = float(source.get("retry_sleep_seconds", 5))
    auth_scheme = str(source.get("auth_scheme", "Bearer"))
    queries = source.get("queries") or [source.get("query", "network engineer")]
    locations = source.get("locations") or [source.get("location", "Seattle, WA")]
    radius = str(source.get("radius", 25))
    days = str(source.get("days", 30))
    page_size = int(source.get("page_size", 50))
    jobs: list[Job] = []
    for query in queries:
        for location in locations:
            path_parts = [
                "v2",
                "jobsearch",
                user_id,
                query,
                location,
                radius,
                "0",
                "0",
                "0",
                str(page_size),
                days,
            ]
            encoded_path = "/".join(quote(part, safe="") for part in path_parts)
            query_string = urlencode(
                {
                    "enableJobDescriptionSnippet": "true",
                    "enableMetaData": "false",
                }
            )
            url = "https://api.careeronestop.org/" + encoded_path + "?" + query_string
            payload = fetch_json(
                url,
                headers={
                    "Authorization": auth_header_value(token, auth_scheme),
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                retries=retries,
                retry_sleep=retry_sleep,
            )
            raw_jobs = (
                payload.get("Jobs")
                or payload.get("jobs")
                or payload.get("JobList")
                or payload.get("jobList")
                or payload.get("Results")
                or []
            )
            for raw in raw_jobs:
                location_text = raw.get("Location") or raw.get("location") or location
                company = raw.get("Company") or raw.get("company") or raw.get("CompanyName") or ""
                jobs.append(
                    Job(
                        source=source["name"],
                        source_job_id=str(raw.get("JvId") or raw.get("JobId") or raw.get("id") or stable_source_job_id(raw.get("JobTitle"), company, location_text)),
                        canonical_url=raw.get("URL") or raw.get("url") or raw.get("JobURL") or "",
                        title=raw.get("JobTitle") or raw.get("title") or "",
                        company=company or source.get("company") or "CareerOneStop",
                        location=location_text,
                        salary=normalize_salary(raw.get("Salary") or raw.get("salary")),
                        work_model=infer_work_model(location_text, json.dumps(raw, ensure_ascii=True)),
                        posted_at=parse_datetime(raw.get("AcquisitionDate") or raw.get("AccquisitionDate") or raw.get("PostedDate") or raw.get("posted_at")),
                        raw=enrich_raw_with_source(source, {**raw, "query": query, "configured_location": location}),
                    )
                )
            time.sleep(float(source.get("sleep_seconds", 0)))
    return jobs


def collect_serpapi_google_jobs(source: dict[str, Any]) -> list[Job]:
    api_key = source.get("api_key") or os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        raise ValueError("SerpAPI requires SERPAPI_API_KEY or source api_key")
    queries = source.get("queries") or [source.get("query", "network engineer jobs")]
    locations = source.get("locations") or [source.get("location", "Seattle, Washington, United States")]
    jobs: list[Job] = []
    for query in queries:
        for location in locations:
            params = {
                "engine": "google_jobs",
                "q": query,
                "location": location,
                "hl": "en",
                "gl": "us",
                "api_key": api_key,
            }
            payload = fetch_json("https://serpapi.com/search.json?" + urlencode(params))
            for raw in payload.get("jobs_results", []):
                location_text = raw.get("location") or location
                apply_options = raw.get("apply_options") or []
                apply_link = ""
                if apply_options and isinstance(apply_options[0], dict):
                    apply_link = apply_options[0].get("link") or ""
                related_links = raw.get("related_links") or []
                related_link = related_links[0].get("link", "") if related_links and isinstance(related_links[0], dict) else ""
                jobs.append(
                    Job(
                        source=source["name"],
                        source_job_id=str(raw.get("job_id") or stable_source_job_id(raw.get("title"), raw.get("company_name"), location_text)),
                        canonical_url=apply_link or related_link or raw.get("link", ""),
                        title=raw.get("title") or "",
                        company=raw.get("company_name") or source.get("company") or "Google Jobs",
                        location=location_text,
                        salary=normalize_salary(raw.get("salary")),
                        work_model=infer_work_model(location_text, json.dumps(raw, ensure_ascii=True)),
                        posted_at=parse_datetime(raw.get("detected_extensions", {}).get("posted_at") or raw.get("via")),
                        raw=enrich_raw_with_source(source, {**raw, "query": query, "configured_location": location}),
                    )
                )
            time.sleep(float(source.get("sleep_seconds", 0)))
    return jobs


def collect_workday(source: dict[str, Any]) -> list[Job]:
    api_url = source["api_url"].rstrip("/")
    detail_root = source.get("detail_root") or api_url.rsplit("/jobs", 1)[0]
    queries = source.get("queries") or [source.get("query", "")]
    limit = int(source.get("limit", 50))
    max_pages = int(source.get("max_pages", 1))
    fetch_details = bool(source.get("fetch_details", True))
    jobs: list[Job] = []
    for query in queries:
        for page in range(max_pages):
            payload = {
                "appliedFacets": source.get("applied_facets", {}),
                "limit": limit,
                "offset": page * limit,
                "searchText": query,
            }
            try:
                data = fetch_json_post(api_url, payload)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                print(f"Workday query failed for {source['name']} / {query!r}: {exc}", file=sys.stderr)
                continue
            postings = data.get("jobPostings") or data.get("jobs") or []
            if not postings:
                continue
            for posting in postings:
                raw = enrich_raw_with_source(source, {**posting, "query": query})
                external_path = posting.get("externalPath") or posting.get("externalUrl") or ""
                if fetch_details and external_path:
                    detail_url = detail_root + external_path
                    try:
                        detail = fetch_json(detail_url)
                        raw["detail"] = detail
                    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
                        pass
                detail_info = raw.get("detail", {}).get("jobPostingInfo", {}) if isinstance(raw.get("detail"), dict) else {}
                location = (
                    detail_info.get("location")
                    or detail_info.get("locationsText")
                    or posting.get("locationsText")
                    or posting.get("location")
                    or ""
                )
                title = detail_info.get("title") or posting.get("title") or ""
                bullet_fields = posting.get("bulletFields") or []
                source_job_id = str(
                    detail_info.get("jobReqId")
                    or (bullet_fields[0] if bullet_fields else "")
                    or external_path
                    or stable_source_job_id(title, source.get("company"), location)
                )
                canonical_url = detail_info.get("externalUrl") or (source.get("career_url", "") + external_path if external_path else "")
                salary = normalize_salary(detail_info.get("payRange") or detail_info.get("salary") or "")
                posted = detail_info.get("startDate") or posting.get("postedOn") or posting.get("postedOnTimestamp")
                jobs.append(
                    Job(
                        source=source["name"],
                        source_job_id=source_job_id,
                        canonical_url=canonical_url,
                        title=title,
                        company=source.get("company") or "Workday",
                        location=location,
                        salary=salary,
                        work_model=infer_work_model(location, json.dumps(raw, ensure_ascii=True)),
                        posted_at=parse_datetime(posted),
                        raw=raw,
                    )
                )
            time.sleep(float(source.get("sleep_seconds", 0)))
    return jobs


def infer_work_model(location: str, content: str = "") -> str:
    blob = f"{location} {content}".lower()
    if "remote" in blob:
        return "Remote"
    if "hybrid" in blob:
        return "Hybrid"
    if location:
        return "On Site"
    return ""


COLLECTORS = {
    "newgrad": collect_newgrad,
    "greenhouse": collect_greenhouse,
    "lever": collect_lever,
    "ashby": collect_ashby,
    "smartrecruiters": collect_smartrecruiters,
    "usajobs": collect_usajobs,
    "remotive": collect_remotive,
    "arbeitnow": collect_arbeitnow,
    "adzuna": collect_adzuna,
    "jobicy": collect_jobicy,
    "github_markdown": collect_github_markdown,
    "workable": collect_workable,
    "recruitee": collect_recruitee,
    "bamboohr": collect_bamboohr,
    "remoteok": collect_remoteok,
    "rss": collect_rss,
    "themuse": collect_themuse,
    "findwork": collect_findwork,
    "careeronestop": collect_careeronestop,
    "serpapi_google_jobs": collect_serpapi_google_jobs,
    "workday": collect_workday,
}


def load_sources(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing sources config: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("sources", [])


def sync_sources(
    conn: sqlite3.Connection,
    sources: list[dict[str, Any]],
    only: str | None = None,
    include_disabled: bool = False,
    verbose: bool = True,
) -> dict[str, int]:
    totals = {"new": 0, "changed": 0, "unchanged": 0, "missing": 0, "failed": 0}
    seen_at = utc_now()
    for source in sources:
        if not include_disabled and not source.get("enabled", True):
            continue
        if only and source.get("name") != only:
            continue
        kind = source.get("type")
        collector = COLLECTORS.get(kind)
        if collector is None:
            if verbose:
                print(f"Skipping {source.get('name')}: unsupported type {kind}", file=sys.stderr)
            totals["failed"] += 1
            continue
        source_name = source["name"]
        try:
            jobs = collector(source)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            if verbose:
                print(f"Failed {source_name}: {exc}", file=sys.stderr)
            totals["failed"] += 1
            continue

        seen_ids: set[str] = set()
        source_counts = {"new": 0, "changed": 0, "unchanged": 0}
        for job in jobs:
            if not job.title or not job.company:
                continue
            if job.source_job_id in seen_ids:
                continue
            result = upsert_job(conn, job, seen_at)
            seen_ids.add(job.source_job_id)
            totals[result] += 1
            source_counts[result] += 1
        missing = mark_missing(conn, source_name, seen_ids) if source.get("mark_missing", True) else 0
        totals["missing"] += missing
        conn.commit()
        if verbose:
            print(
                f"{source_name}: {source_counts['new']} new, {source_counts['changed']} changed, "
                f"{source_counts['unchanged']} unchanged, {missing} missing"
            )
    return totals


def query_jobs(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    status: str = "active",
    include_stale: bool = False,
    stale_days: int = 30,
    limit: int = 50,
    offset: int = 0,
    min_score: int | None = None,
    target_only: bool = False,
    max_years: float | None = None,
    role: str = "",
    preset: str = "custom",
    include_security: bool = False,
    include_non_target: bool = False,
    min_resume_match: int | None = None,
    max_difficulty: int | None = None,
    source: str = "",
) -> list[sqlite3.Row]:
    clauses = []
    params: list[Any] = []
    if status == "active":
        clauses.append("active = 1")
        clauses.append("status NOT IN ('ignored', 'rejected')")
    elif status in STATUSES:
        clauses.append("status = ?")
        params.append(status)
    elif status == "all":
        pass
    else:
        raise ValueError(f"Unsupported status: {status}")

    if query:
        for term in query.lower().split():
            clauses.append(
                "(LOWER(title) LIKE ? OR LOWER(company) LIKE ? OR LOWER(location) LIKE ? OR LOWER(raw_json) LIKE ?)"
            )
            like = f"%{term}%"
            params.extend([like, like, like, like])

    if min_score is not None:
        clauses.append("score >= ?")
        params.append(min_score)

    if source:
        clauses.append("source = ?")
        params.append(source)

    if role:
        clauses.append("role_bucket = ?")
        params.append(normalize_role_arg(role))

    if max_years is not None:
        clauses.append(
            """
            (
                (experience_min_years IS NULL AND experience_max_years IS NULL)
                OR (experience_max_years IS NOT NULL AND experience_max_years <= ?)
                OR (experience_max_years IS NULL AND experience_min_years IS NOT NULL AND experience_min_years <= ?)
            )
            """
        )
        params.extend([max_years, max_years])

    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        f"""
        SELECT * FROM jobs
        {where}
        ORDER BY score DESC, COALESCE(NULLIF(posted_at, ''), first_seen_at) DESC
        LIMIT ?
        """,
        (*params, max((limit + offset) * 20, limit + offset, 200)),
    ).fetchall()

    if include_stale:
        filtered_rows = rows
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
        filtered_rows = [row for row in rows if freshness_date(row) >= cutoff]

    if target_only:
        filtered_rows = [
            row for row in filtered_rows if is_target_location(row["location"], row["work_model"])
        ]

    filtered_rows = [
        row
        for row in filtered_rows
        if row_passes_preset(
            row,
            preset=preset,
            explicit_role=role,
            include_security=include_security,
            include_non_target=include_non_target,
            min_resume_match=min_resume_match,
            max_difficulty=max_difficulty,
        )
    ]

    unique_rows: list[sqlite3.Row] = []
    by_key: dict[str, sqlite3.Row] = {}
    for row in filtered_rows:
        key = row_duplicate_group_key(row)
        existing = by_key.get(key)
        if existing is None or row_verification_rank(row) > row_verification_rank(existing):
            by_key[key] = row
    seen_keys: set[str] = set()
    for row in filtered_rows:
        key = row_duplicate_group_key(row)
        if key in seen_keys or by_key.get(key) is not row:
            continue
        seen_keys.add(key)
        unique_rows.append(row)
    return unique_rows[offset : offset + limit]


def query_jobs_for_args(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    profile = None
    profile_name = getattr(args, "profile", "")
    if profile_name:
        profiles = load_profiles(getattr(args, "profiles", DEFAULT_PROFILES))
        if profile_name not in profiles:
            raise ValueError(f"Unknown profile: {profile_name}")
        profile = profiles[profile_name]
    preset = profile.get("preset", args.preset) if profile else args.preset
    max_years_arg = getattr(args, "max_years", None)
    max_years = profile.get("max_years", max_years_arg if max_years_arg is not None else apply_max_years()) if profile else (
        max_years_arg if max_years_arg is not None else apply_max_years()
    )
    min_resume_match = profile.get("min_resume_match", args.min_resume_match) if profile else args.min_resume_match
    max_difficulty = profile.get("max_difficulty", args.max_difficulty) if profile else args.max_difficulty
    target_only = bool(profile.get("target_only", not getattr(args, "all_locations", False))) if profile else (
        getattr(args, "target_only", False) or not getattr(args, "all_locations", True)
    )
    role = args.role
    role_for_query = role
    if profile and len(profile.get("roles", [])) != 1:
        role_for_query = ""
    elif profile and len(profile.get("roles", [])) == 1:
        role_for_query = str(profile["roles"][0])
    rows = query_jobs(
        conn,
        query=getattr(args, "query", ""),
        status=getattr(args, "status", "active"),
        limit=args.limit,
        offset=getattr(args, "offset", 0),
        include_stale=getattr(args, "include_stale", False),
        stale_days=getattr(args, "stale_days", 30),
        min_score=getattr(args, "min_score", None),
        target_only=target_only,
        max_years=max_years,
        role=role_for_query,
        preset=preset,
        include_security=getattr(args, "include_security", False),
        include_non_target=getattr(args, "include_non_target", False),
        min_resume_match=min_resume_match,
        max_difficulty=max_difficulty,
        source=getattr(args, "source", ""),
    )
    if profile:
        rows = filter_rows_by_profile(rows, profile)
    return rows


def print_table(rows: Iterable[sqlite3.Row]) -> None:
    for row in rows:
        fresh = freshness_date(row).date().isoformat()
        print(
            f"{row['id']:>4} | {row['score']:>3} | {fresh:10} | "
            f"{console_text(row['role_bucket'], 12):12} | {row['resume_match']:>3}% | "
            f"I:{row['interview_difficulty']}/10 | {console_text(row['experience_summary'], 15):15} | "
            f"{console_text(row['title'], 38):38} | {console_text(row['company'], 20):20} | {console_text(row['why_match'], 70)}"
        )


def print_audit(rows: Iterable[sqlite3.Row]) -> None:
    for row in rows:
        fresh = freshness_date(row).date().isoformat()
        print(
            f"{row['id']:>4} | {fit_label(row):6} | {row['score']:>3} | {fresh:10} | "
            f"{console_text(row['role_bucket'], 12):12} | {row['resume_match']:>3}% | I:{row['interview_difficulty']}/10 | "
            f"{console_text(row['experience_summary'], 14):14} | {console_text(row['title'], 38):38} | "
            f"{console_text(row['company'], 18):18} | {console_text(row['location'], 24):24} | "
            f"{console_text(row['why_match'], 90)}"
        )


def show_job(conn: sqlite3.Connection, job_id: int) -> None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise ValueError(f"No job found with id {job_id}")
    print(f"ID: {row['id']}")
    print(f"Title: {console_text(row['title'])}")
    print(f"Company: {console_text(row['company'])}")
    print(f"Location: {console_text(row['location'])}")
    print(f"Salary: {console_text(row['salary'])}")
    print(f"Estimated salary: {row['estimated_salary'] or ''}")
    print(f"Salary score: {row['salary_score']}")
    print(f"Work model: {console_text(row['work_model'])}")
    print(f"Source type: {console_text(row['source_type'])}")
    print(f"Source confidence: {console_text(row['source_confidence'])}")
    print(f"Verified company site: {bool(row['verified_on_company_site'])}")
    print(f"Employer URL: {console_text(row['canonical_employer_url'])}")
    print(f"Role bucket: {console_text(row['role_bucket'])}")
    print(f"Interview Difficulty: {row['interview_difficulty']}/10")
    print(f"Resume Match: {row['resume_match']}%")
    print(f"Keyword Match: {row['keyword_match']}%")
    print(f"Semantic Match: {row['semantic_match']}%")
    print(f"Best Resume Profile: {console_text(row['best_resume_profile'])}")
    print(f"Missing Keywords: {console_text(row['missing_keywords'])}")
    print(f"H1B Sponsorship: {console_text(row['h1b_sponsorship'])}")
    print(f"Company Size: {console_text(row['company_size'])}")
    print(f"Company Industry: {console_text(row['company_industry'])}")
    print(f"Seniority: {console_text(row['seniority'])}")
    print(f"Qualifications: {console_text(row['qualifications'])}")
    print(f"Applicant Count: {console_text(row['applicant_count'])}")
    print(f"Duplicate Key: {console_text(row['duplicate_key'])}")
    print(f"Experience: {console_text(row['experience_summary'])}")
    print(f"Status: {console_text(row['status'])}")
    print(f"Score: {row['score']}")
    print(f"Why: {console_text(row['why_match'])}")
    print(f"Why apply: {console_text(row['why_apply'])}")
    print(f"Concerns: {console_text(row['concerns'])}")
    print(f"Posted: {console_text(row['posted_at'])}")
    print(f"First seen: {console_text(row['first_seen_at'])}")
    print(f"Last seen: {console_text(row['last_seen_at'])}")
    print(f"Apply: {console_text(row['canonical_url'])}")
    duplicates = conn.execute(
        """
        SELECT id, source, canonical_url, source_confidence, active
        FROM jobs
        WHERE duplicate_key = ? AND id != ?
        ORDER BY active DESC, source_confidence DESC, last_seen_at DESC
        LIMIT 10
        """,
        (row["duplicate_key"], job_id),
    ).fetchall()
    if duplicates:
        print("Other discovered sources:")
        for dupe in duplicates:
            print(
                f"  {dupe['id']} | {console_text(dupe['source'])} | "
                f"{console_text(dupe['source_confidence'])} | active={bool(dupe['active'])} | "
                f"{console_text(dupe['canonical_url'])}"
            )


def export_rows(rows: Iterable[sqlite3.Row], output: Path) -> None:
    fieldnames = [
        "id",
        "score",
        "status",
        "status_reason",
        "source",
        "source_job_id",
        "title",
        "company",
        "location",
        "salary",
        "estimated_salary",
        "salary_score",
        "work_model",
        "source_type",
        "source_confidence",
        "verified_on_company_site",
        "canonical_employer_url",
        "apply_url",
        "live_verified_at",
        "live_verification_status",
        "live_verification_score",
        "live_verification_concerns",
        "live_page_url",
        "live_page_final_url",
        "live_page_title",
        "role_bucket",
        "interview_difficulty",
        "resume_match",
        "keyword_match",
        "semantic_match",
        "missing_keywords",
        "best_resume_profile",
        "experience_summary",
        "experience_min_years",
        "experience_max_years",
        "h1b_sponsorship",
        "company_size",
        "company_industry",
        "seniority",
        "qualifications",
        "applicant_count",
        "duplicate_key",
        "posted_at",
        "first_seen_at",
        "last_seen_at",
        "canonical_url",
        "why_match",
        "why_apply",
        "concerns",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            record = {key: row[key] for key in row.keys() if key in fieldnames}
            record["apply_url"] = row_export_apply_url(row)
            writer.writerow({key: record.get(key, "") for key in fieldnames})


def export_html_report(rows: Iterable[sqlite3.Row], output: Path, title: str = "Job Finder Report") -> None:
    row_list = list(rows)
    keys = row_list[0].keys() if row_list else []

    def value(row: sqlite3.Row, key: str) -> Any:
        return row[key] if key in keys and row[key] is not None else ""

    def int_value(row: sqlite3.Row, key: str) -> int:
        raw = value(row, key)
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return 0

    def float_value(row: sqlite3.Row, key: str) -> float | None:
        raw = value(row, key)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    jobs_data: list[dict[str, Any]] = []
    for row in row_list:
        apply_url = value(row, "canonical_employer_url") or value(row, "live_page_final_url") or value(row, "canonical_url")
        jobs_data.append(
            {
                "id": int_value(row, "id"),
                "score": int_value(row, "score"),
                "title": value(row, "title"),
                "company": value(row, "company"),
                "location": value(row, "location") or "Unknown location",
                "salary": value(row, "salary"),
                "estimatedSalary": int_value(row, "estimated_salary"),
                "salaryScore": int_value(row, "salary_score"),
                "workModel": value(row, "work_model"),
                "roleBucket": value(row, "role_bucket"),
                "interviewDifficulty": int_value(row, "interview_difficulty"),
                "resumeMatch": int_value(row, "resume_match"),
                "keywordMatch": int_value(row, "keyword_match"),
                "semanticMatch": int_value(row, "semantic_match"),
                "missingKeywords": value(row, "missing_keywords"),
                "bestResumeProfile": value(row, "best_resume_profile"),
                "experienceSummary": value(row, "experience_summary") or "unknown exp",
                "experienceMinYears": float_value(row, "experience_min_years"),
                "experienceMaxYears": float_value(row, "experience_max_years"),
                "source": value(row, "source"),
                "sourceType": value(row, "source_type"),
                "sourceConfidence": value(row, "source_confidence") or "unknown",
                "verifiedOnCompanySite": bool(int_value(row, "verified_on_company_site")),
                "liveStatus": value(row, "live_verification_status"),
                "liveScore": int_value(row, "live_verification_score"),
                "liveConcerns": value(row, "live_verification_concerns"),
                "liveTitle": value(row, "live_page_title"),
                "postedAt": value(row, "posted_at"),
                "firstSeenAt": value(row, "first_seen_at"),
                "lastSeenAt": value(row, "last_seen_at"),
                "applyUrl": apply_url,
                "whyMatch": value(row, "why_match"),
                "whyApply": value(row, "why_apply") or value(row, "why_match"),
                "concerns": value(row, "concerns"),
                "qualifications": value(row, "qualifications"),
                "companyIndustry": value(row, "company_industry"),
                "applicantCount": value(row, "applicant_count"),
            }
        )

    generated = datetime.now().astimezone().replace(microsecond=0).isoformat()
    jobs_json = json.dumps(jobs_data, ensure_ascii=True).replace("</", "<\\/")
    unique_roles = sorted({str(job["roleBucket"]) for job in jobs_data if job["roleBucket"]})
    role_options = "\n".join(f'<option value="{html.escape(role)}">{html.escape(role)}</option>' for role in unique_roles)
    unique_sources = sorted({str(job["source"]) for job in jobs_data if job["source"]})
    source_options = "\n".join(f'<option value="{html.escape(source)}">{html.escape(source)}</option>' for source in unique_sources)
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17201b;
      --muted: #637064;
      --line: #d9dfd3;
      --paper: #fffffb;
      --wash: #eef2e8;
      --deep: #16352c;
      --accent: #1f7a5b;
      --accent-2: #b65f1f;
      --warn: #9a3412;
      --bad: #9f1d2e;
      --good: #176b4d;
      --shadow: 0 16px 42px rgba(22, 53, 44, 0.11);
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      font-family: "Aptos", "Segoe UI", "Helvetica Neue", sans-serif;
      background: var(--wash);
      color: var(--ink);
    }}
    header {{
      position: relative;
      padding: 28px 28px 18px;
      border-bottom: 1px solid var(--line);
      background:
        linear-gradient(90deg, rgba(22, 53, 44, 0.08) 1px, transparent 1px),
        linear-gradient(180deg, rgba(22, 53, 44, 0.07) 1px, transparent 1px),
        #f8faf4;
      background-size: 34px 34px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-family: Georgia, "Times New Roman", serif;
      font-size: clamp(28px, 4vw, 46px);
      font-weight: 700;
      letter-spacing: 0;
      color: var(--deep);
    }}
    .sub {{
      color: var(--muted);
      margin: 0;
    }}
    .header-row {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 18px;
      max-width: 1480px;
      margin: 0 auto;
    }}
    .header-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: end;
    }}
    main {{
      max-width: 1480px;
      margin: 0 auto;
      padding: 16px 18px 28px;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(5, minmax(130px, 1fr));
      gap: 10px;
      margin: 14px 0;
    }}
    .stat {{
      border: 1px solid var(--line);
      background: rgba(255, 255, 251, 0.82);
      border-radius: 8px;
      padding: 12px;
      box-shadow: 0 4px 18px rgba(22, 53, 44, 0.05);
    }}
    .stat b {{
      display: block;
      font-size: 22px;
      color: var(--deep);
    }}
    .stat span {{
      color: var(--muted);
      font-size: 12px;
    }}
    .toolbar {{
      position: sticky;
      top: 0;
      z-index: 10;
      display: grid;
      grid-template-columns: minmax(240px, 1.5fr) repeat(5, minmax(120px, 1fr));
      gap: 8px;
      padding: 12px;
      margin: 0 0 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 251, 0.94);
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }}
    input, select, button {{
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--paper);
      color: var(--ink);
      font: inherit;
      letter-spacing: 0;
    }}
    input, select {{
      padding: 0 10px;
    }}
    button {{
      cursor: pointer;
      padding: 0 10px;
    }}
    button:hover, input:focus, select:focus {{
      border-color: var(--accent);
      outline: 2px solid rgba(31, 122, 91, 0.12);
    }}
    .togglebar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin: 4px 0 12px;
    }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
    }}
    .chip, .small-button {{
      width: auto;
      min-height: 32px;
      border-radius: 999px;
      background: #f7f5ea;
      color: var(--deep);
    }}
    .chip.active, .small-button.active {{
      background: var(--deep);
      color: #fffdf3;
      border-color: var(--deep);
    }}
    .count-line {{
      color: var(--muted);
      font-size: 13px;
    }}
    .board {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(430px, 1fr));
      gap: 12px;
      align-items: start;
    }}
    .job {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      box-shadow: 0 6px 22px rgba(22, 53, 44, 0.06);
    }}
    .job.hidden {{
      display: none;
    }}
    .job-head {{
      display: grid;
      grid-template-columns: 64px 1fr auto;
      gap: 12px;
      align-items: start;
    }}
    .score {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 56px;
      border-radius: 6px;
      background: #e7f1e8;
      color: var(--accent);
      font-weight: 800;
      font-size: 22px;
    }}
    .score span {{
      font-size: 10px;
      font-weight: 600;
      color: var(--muted);
    }}
    h2 {{
      margin: 0 0 6px;
      font-size: 18px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    a {{
      color: #145c4b;
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    .meta, p {{
      margin: 0 0 10px;
    }}
    .meta {{
      color: var(--muted);
    }}
    .why {{
      line-height: 1.42;
    }}
    .facts, .concerns {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .facts span, .concern, .ok, .pill {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      background: #fafbf5;
      white-space: nowrap;
    }}
    .concern {{
      border-color: #fed7aa;
      background: #fff7ed;
      color: var(--warn);
    }}
    .ok {{
      border-color: #bbf7d0;
      background: #f0fdf4;
      color: var(--accent);
    }}
    .danger {{
      color: var(--bad);
    }}
    .actions {{
      display: flex;
      gap: 6px;
      justify-content: end;
      flex-wrap: wrap;
    }}
    .actions a, .actions button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 32px;
      width: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 10px;
      background: var(--paper);
      font-size: 13px;
    }}
    .actions a.apply {{
      background: var(--deep);
      border-color: var(--deep);
      color: #fffdf3;
      font-weight: 700;
    }}
    details {{
      margin-top: 10px;
    }}
    summary {{
      cursor: pointer;
      color: var(--deep);
      font-weight: 700;
    }}
    .detail-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .table-wrap {{
      display: none;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--paper);
      box-shadow: var(--shadow);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 980px;
    }}
    th, td {{
      padding: 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 13px;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #f7f5ea;
      z-index: 2;
      color: var(--deep);
    }}
    th button {{
      min-height: auto;
      padding: 0;
      border: 0;
      background: transparent;
      text-align: left;
      font-weight: 800;
    }}
    tr:hover td {{
      background: #fbfaf3;
    }}
    body.table-mode .board {{
      display: none;
    }}
    body.table-mode .table-wrap {{
      display: block;
    }}
    .empty {{
      border: 1px dashed var(--line);
      background: var(--paper);
      border-radius: 8px;
      padding: 34px;
      color: var(--muted);
      text-align: center;
    }}
    @media (max-width: 1050px) {{
      .toolbar {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .stats {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}
    @media (max-width: 640px) {{
      header {{
        padding: 20px 14px 14px;
      }}
      main {{
        padding: 12px;
      }}
      .header-row, .togglebar {{
        align-items: stretch;
        flex-direction: column;
      }}
      .toolbar, .stats, .detail-grid {{
        grid-template-columns: 1fr;
      }}
      .board {{
        grid-template-columns: 1fr;
      }}
      .job-head {{
        grid-template-columns: 54px 1fr;
      }}
      .actions {{
        grid-column: 1 / -1;
        justify-content: start;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-row">
      <div>
        <h1>{html.escape(title)}</h1>
        <p class="sub">{len(row_list)} jobs loaded locally &middot; generated {html.escape(generated)}</p>
      </div>
      <div class="header-actions">
        <button class="small-button" id="cardView" type="button">Card view</button>
        <button class="small-button" id="tableView" type="button">Table view</button>
        <button class="small-button" id="savedOnly" type="button">Saved only</button>
      </div>
    </div>
  </header>
  <main>
    <section class="stats" id="stats" aria-label="Report metrics"></section>
    <section class="toolbar" aria-label="Job filters">
      <input id="search" type="search" placeholder="Search title, company, skills, location">
      <select id="roleFilter" aria-label="Role filter">
        <option value="">All roles</option>
        {role_options}
      </select>
      <select id="sourceFilter" aria-label="Source filter">
        <option value="">All sources</option>
        {source_options}
      </select>
      <select id="confidenceFilter" aria-label="Source confidence filter">
        <option value="">Any confidence</option>
        <option value="high">High source</option>
        <option value="medium">Medium source</option>
        <option value="low">Low source</option>
        <option value="unknown">Unknown source</option>
      </select>
      <select id="sortBy" aria-label="Sort jobs">
        <option value="scoreDesc">Score high to low</option>
        <option value="resumeDesc">Resume match high to low</option>
        <option value="difficultyAsc">Interview easiest first</option>
        <option value="salaryDesc">Estimated salary high to low</option>
        <option value="newest">Newest first</option>
        <option value="companyAsc">Company A to Z</option>
      </select>
      <select id="quickFilter" aria-label="Quick filter">
        <option value="">All matches</option>
        <option value="verified">Verified apply URL</option>
        <option value="salary">Known salary</option>
        <option value="lowDifficulty">Interview <= 4</option>
        <option value="noConcerns">No major concerns</option>
        <option value="remote">Remote or hybrid</option>
      </select>
    </section>
    <div class="togglebar">
      <div class="chips" id="roleChips"></div>
      <div class="count-line" id="countLine"></div>
    </div>
    <section class="board" id="cards" aria-label="Job cards"></section>
    <section class="table-wrap" aria-label="Job table">
      <table>
        <thead>
          <tr>
            <th><button type="button" data-sort="scoreDesc">Score</button></th>
            <th><button type="button" data-sort="resumeDesc">Match</button></th>
            <th><button type="button" data-sort="companyAsc">Job</button></th>
            <th>Role</th>
            <th>Location</th>
            <th><button type="button" data-sort="difficultyAsc">Difficulty</button></th>
            <th><button type="button" data-sort="salaryDesc">Salary</button></th>
            <th>Why</th>
            <th>Apply</th>
          </tr>
        </thead>
        <tbody id="tableBody"></tbody>
      </table>
    </section>
  </main>
  <script type="application/json" id="jobs-data">{jobs_json}</script>
  <script>
    const jobs = JSON.parse(document.getElementById("jobs-data").textContent);
    const savedKey = "jobFinderSavedIds";
    const state = {{
      query: "",
      role: "",
      source: "",
      confidence: "",
      sortBy: "scoreDesc",
      quick: "",
      savedOnly: false,
      saved: new Set(JSON.parse(localStorage.getItem(savedKey) || "[]"))
    }};

    const el = (id) => document.getElementById(id);
    const formatter = new Intl.NumberFormat("en-US", {{ maximumFractionDigits: 0 }});
    const money = new Intl.NumberFormat("en-US", {{ style: "currency", currency: "USD", maximumFractionDigits: 0 }});

    function esc(value) {{
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }}[char]));
    }}

    function compact(text, max = 120) {{
      const value = String(text || "");
      return value.length > max ? value.slice(0, max - 1) + "..." : value;
    }}

    function salaryLabel(job) {{
      if (job.salary) return job.salary;
      if (job.estimatedSalary) return money.format(job.estimatedSalary);
      return "Unknown";
    }}

    function searchable(job) {{
      return [
        job.title, job.company, job.location, job.roleBucket, job.source, job.workModel,
        job.whyApply, job.whyMatch, job.missingKeywords, job.concerns, job.qualifications
      ].join(" ").toLowerCase();
    }}

    function passesQuick(job) {{
      if (state.quick === "verified") return job.verifiedOnCompanySite;
      if (state.quick === "salary") return Boolean(job.salary || job.estimatedSalary);
      if (state.quick === "lowDifficulty") return job.interviewDifficulty <= 4;
      if (state.quick === "noConcerns") return !job.concerns;
      if (state.quick === "remote") return /remote|hybrid/i.test(`${{job.workModel}} ${{job.location}}`);
      return true;
    }}

    function filteredJobs(ignoreRole = false) {{
      const terms = state.query.toLowerCase().split(/\\s+/).filter(Boolean);
      return jobs.filter((job) => {{
        if (state.savedOnly && !state.saved.has(String(job.id))) return false;
        if (!ignoreRole && state.role && job.roleBucket !== state.role) return false;
        if (state.source && job.source !== state.source) return false;
        if (state.confidence && job.sourceConfidence !== state.confidence) return false;
        if (!passesQuick(job)) return false;
        const blob = searchable(job);
        return terms.every((term) => blob.includes(term));
      }});
    }}

    function sortJobs(list) {{
      const sorted = [...list];
      const byNewest = (job) => Date.parse(job.postedAt || job.firstSeenAt || 0) || 0;
      const sorts = {{
        scoreDesc: (a, b) => b.score - a.score,
        resumeDesc: (a, b) => b.resumeMatch - a.resumeMatch || b.score - a.score,
        difficultyAsc: (a, b) => a.interviewDifficulty - b.interviewDifficulty || b.score - a.score,
        salaryDesc: (a, b) => (b.estimatedSalary || 0) - (a.estimatedSalary || 0) || b.score - a.score,
        newest: (a, b) => byNewest(b) - byNewest(a) || b.score - a.score,
        companyAsc: (a, b) => a.company.localeCompare(b.company) || b.score - a.score
      }};
      sorted.sort(sorts[state.sortBy] || sorts.scoreDesc);
      return sorted;
    }}

    function saveState() {{
      localStorage.setItem(savedKey, JSON.stringify([...state.saved]));
    }}

    function toggleSaved(id) {{
      const key = String(id);
      if (state.saved.has(key)) state.saved.delete(key);
      else state.saved.add(key);
      saveState();
      render();
    }}

    function renderStats(list) {{
      const avgScore = list.length ? Math.round(list.reduce((sum, job) => sum + job.score, 0) / list.length) : 0;
      const avgMatch = list.length ? Math.round(list.reduce((sum, job) => sum + job.resumeMatch, 0) / list.length) : 0;
      const knownSalary = list.filter((job) => job.salary || job.estimatedSalary).length;
      const easy = list.filter((job) => job.interviewDifficulty <= 4).length;
      const verified = list.filter((job) => job.verifiedOnCompanySite).length;
      el("stats").innerHTML = [
        ["Visible jobs", formatter.format(list.length)],
        ["Average score", avgScore],
        ["Average resume match", `${{avgMatch}}%`],
        ["Known salary", `${{knownSalary}} / ${{list.length}}`],
        ["Easy interviews", `${{easy}} / ${{list.length}}`],
        ["Verified apply links", `${{verified}} / ${{list.length}}`]
      ].map(([label, value]) => `<div class="stat"><b>${{esc(value)}}</b><span>${{esc(label)}}</span></div>`).join("");
    }}

    function renderRoleChips(list) {{
      const counts = new Map();
      for (const job of list) counts.set(job.roleBucket, (counts.get(job.roleBucket) || 0) + 1);
      const chips = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([role, count]) => (
        `<button class="chip ${{state.role === role ? "active" : ""}}" type="button" data-role="${{esc(role)}}">${{esc(role)}} ${{count}}</button>`
      ));
      el("roleChips").innerHTML = `<button class="chip ${{state.role === "" ? "active" : ""}}" type="button" data-role="">All ${{list.length}}</button>` + chips.join("");
    }}

    function facts(job) {{
      return [
        `Resume ${{job.resumeMatch}}%`,
        `Interview ${{job.interviewDifficulty}}/10`,
        job.experienceSummary,
        `${{job.sourceConfidence}} source`,
        salaryLabel(job)
      ].map((fact) => `<span>${{esc(fact)}}</span>`).join("");
    }}

    function concerns(job) {{
      if (!job.concerns) return '<span class="ok">No major concerns</span>';
      return job.concerns.split(";").filter(Boolean).map((item) => `<span class="concern">${{esc(item.trim())}}</span>`).join("");
    }}

    function card(job) {{
      const saved = state.saved.has(String(job.id));
      const apply = job.applyUrl ? `<a class="apply" href="${{esc(job.applyUrl)}}" target="_blank" rel="noreferrer">Apply</a>` : "";
      return `
        <article class="job">
          <div class="job-head">
            <div class="score">${{job.score}}<span>score</span></div>
            <div>
              <h2><a href="${{esc(job.applyUrl || "#")}}" target="_blank" rel="noreferrer">${{esc(job.title)}}</a></h2>
              <p class="meta">${{esc(job.company)}} · ${{esc(job.location)}} · ${{esc(job.roleBucket)}}</p>
            </div>
            <div class="actions">
              <button type="button" data-save="${{job.id}}">${{saved ? "Saved" : "Save"}}</button>
              ${{apply}}
            </div>
          </div>
          <p class="why">${{esc(job.whyApply)}}</p>
          <div class="facts">${{facts(job)}}</div>
          <div class="concerns">${{concerns(job)}}</div>
          <details>
            <summary>Details</summary>
            <div class="detail-grid">
              <div><b>Missing:</b> ${{esc(job.missingKeywords || "none detected")}}</div>
              <div><b>Source:</b> ${{esc(job.source)}} · ${{esc(job.sourceType)}}</div>
              <div><b>Posted:</b> ${{esc(job.postedAt || "unknown")}}</div>
              <div><b>Best resume:</b> ${{esc(job.bestResumeProfile || "unknown")}}</div>
              <div><b>Qualification notes:</b> ${{esc(job.qualifications || "none captured")}}</div>
              <div><b>Raw match:</b> keyword ${{job.keywordMatch}}%, semantic ${{job.semanticMatch}}%</div>
            </div>
          </details>
        </article>
      `;
    }}

    function row(job) {{
      return `
        <tr>
          <td><b>${{job.score}}</b></td>
          <td>${{job.resumeMatch}}%</td>
          <td><a href="${{esc(job.applyUrl || "#")}}" target="_blank" rel="noreferrer">${{esc(job.title)}}</a><br><span class="meta">${{esc(job.company)}}</span></td>
          <td>${{esc(job.roleBucket)}}</td>
          <td>${{esc(job.location)}}</td>
          <td>${{job.interviewDifficulty}}/10</td>
          <td>${{esc(salaryLabel(job))}}</td>
          <td>${{esc(compact(job.whyApply, 120))}}</td>
          <td><button type="button" data-save="${{job.id}}">${{state.saved.has(String(job.id)) ? "Saved" : "Save"}}</button></td>
        </tr>
      `;
    }}

    function render() {{
      const filtered = filteredJobs();
      const sorted = sortJobs(filtered);
      renderStats(sorted);
      renderRoleChips(filteredJobs(true));
      el("countLine").textContent = `${{sorted.length}} of ${{jobs.length}} jobs shown`;
      el("cards").innerHTML = sorted.length ? sorted.map(card).join("") : '<div class="empty">No jobs match these filters.</div>';
      el("tableBody").innerHTML = sorted.map(row).join("");
      el("savedOnly").classList.toggle("active", state.savedOnly);
      el("cardView").classList.toggle("active", !document.body.classList.contains("table-mode"));
      el("tableView").classList.toggle("active", document.body.classList.contains("table-mode"));
    }}

    function setRole(role) {{
      state.role = role;
      el("roleFilter").value = role;
      render();
    }}

    ["search", "roleFilter", "sourceFilter", "confidenceFilter", "sortBy", "quickFilter"].forEach((id) => {{
      el(id).addEventListener("input", (event) => {{
        const value = event.target.value;
        if (id === "search") state.query = value;
        if (id === "roleFilter") state.role = value;
        if (id === "sourceFilter") state.source = value;
        if (id === "confidenceFilter") state.confidence = value;
        if (id === "sortBy") state.sortBy = value;
        if (id === "quickFilter") state.quick = value;
        render();
      }});
    }});

    document.addEventListener("click", (event) => {{
      const saveButton = event.target.closest("[data-save]");
      if (saveButton) {{
        toggleSaved(saveButton.dataset.save);
        return;
      }}
      const roleButton = event.target.closest("[data-role]");
      if (roleButton) {{
        setRole(roleButton.dataset.role);
        return;
      }}
      const sortButton = event.target.closest("[data-sort]");
      if (sortButton) {{
        state.sortBy = sortButton.dataset.sort;
        el("sortBy").value = state.sortBy;
        render();
      }}
    }});

    el("savedOnly").addEventListener("click", () => {{
      state.savedOnly = !state.savedOnly;
      render();
    }});
    el("cardView").addEventListener("click", () => {{
      document.body.classList.remove("table-mode");
      render();
    }});
    el("tableView").addEventListener("click", () => {{
      document.body.classList.add("table-mode");
      render();
    }});

    render();
  </script>
</body>
</html>
"""
    output.write_text(document, encoding="utf-8")


def mark_job(conn: sqlite3.Connection, job_id: int, status: str, notes: str = "", reason: str = "") -> None:
    if status not in MANUAL_STATUSES:
        raise ValueError(f"Unsupported status: {status}")
    row = conn.execute("SELECT id, raw_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise ValueError(f"No job found with id {job_id}")
    raw = json.loads(row["raw_json"] or "{}")
    if notes:
        raw["notes"] = notes
    if reason:
        raw["status_reason"] = reason
    conn.execute(
        "UPDATE jobs SET status = ?, status_reason = ?, raw_json = ? WHERE id = ?",
        (status, reason, json.dumps(raw, sort_keys=True, ensure_ascii=True), job_id),
    )
    conn.commit()


def slugify_company(value: str) -> str:
    lowered = value.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", lowered)


def load_watchlist(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return [
            {"company": "F5", "tier": 1, "workday_api_url": "https://ffive.wd5.myworkdayjobs.com/wday/cxs/ffive/f5jobs/jobs"},
            {"company": "Cisco", "tier": 1},
            {"company": "Juniper Networks", "tier": 1},
            {"company": "Cloudflare", "tier": 1, "greenhouse": "cloudflare"},
            {"company": "T-Mobile", "tier": 1},
            {"company": "Amazon", "tier": 2},
            {"company": "Microsoft", "tier": 2},
            {"company": "Expedia Group", "tier": 2},
            {"company": "Meta", "tier": 2},
        ]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item if isinstance(item, dict) else {"company": str(item)} for item in data]
    companies = data.get("companies", [])
    return [item if isinstance(item, dict) else {"company": str(item)} for item in companies]


def source_candidate(name: str, kind: str, company: str, verified: bool, **values: Any) -> dict[str, Any]:
    candidate = {
        "name": name,
        "type": kind,
        "enabled": False,
        "mark_missing": True,
        "company": company,
        "source_confidence": source_confidence_for_kind(kind),
        "verified": verified,
    }
    candidate.update({key: value for key, value in values.items() if value})
    return candidate


def endpoint_seems_live(kind: str, candidate: dict[str, Any]) -> bool:
    try:
        if kind == "greenhouse":
            fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{candidate['board']}/jobs?content=false", timeout=10)
        elif kind == "lever":
            fetch_json(f"https://api.lever.co/v0/postings/{candidate['company_slug']}?mode=json", timeout=10)
        elif kind == "ashby":
            fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{candidate['organization']}", timeout=10)
        elif kind == "smartrecruiters":
            fetch_json(f"https://api.smartrecruiters.com/v1/companies/{candidate['company_slug']}/postings?limit=1", timeout=10)
        elif kind == "recruitee":
            fetch_json(f"https://{candidate['company_slug']}.recruitee.com/api/offers/", timeout=10)
        elif kind == "bamboohr":
            fetch_json(f"https://{candidate['subdomain']}.bamboohr.com/careers/list", timeout=10)
        elif kind == "workday":
            fetch_json_post(candidate["api_url"], {"limit": 1, "offset": 0, "searchText": ""}, timeout=10)
        else:
            return False
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return False
    return True


def discover_source_candidates(
    watchlist: list[dict[str, Any]],
    *,
    verify: bool = False,
    verified_only: bool = False,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in watchlist:
        company = str(item.get("company") or "").strip()
        if not company:
            continue
        slug = str(item.get("slug") or slugify_company(company))
        base_name = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
        possible = [
            source_candidate(f"{base_name}-greenhouse", "greenhouse", company, False, board=item.get("greenhouse") or slug),
            source_candidate(f"{base_name}-lever", "lever", company, False, company_slug=item.get("lever") or slug),
            source_candidate(f"{base_name}-ashby", "ashby", company, False, organization=item.get("ashby") or slug),
            source_candidate(f"{base_name}-smartrecruiters", "smartrecruiters", company, False, company_slug=item.get("smartrecruiters") or slug),
            source_candidate(f"{base_name}-workable", "workable", company, False, account=item.get("workable") or slug),
            source_candidate(f"{base_name}-recruitee", "recruitee", company, False, company_slug=item.get("recruitee") or slug),
            source_candidate(f"{base_name}-bamboohr", "bamboohr", company, False, subdomain=item.get("bamboohr") or slug),
        ]
        if item.get("workday_api_url"):
            possible.append(
                source_candidate(
                    f"{base_name}-workday",
                    "workday",
                    company,
                    False,
                    api_url=item.get("workday_api_url"),
                    detail_root=item.get("workday_detail_root"),
                    career_url=item.get("career_url"),
                    limit=25,
                    max_pages=1,
                    fetch_details=True,
                )
            )
        for candidate in possible:
            if verify:
                candidate["verified"] = endpoint_seems_live(candidate["type"], candidate)
            if verified_only and not candidate["verified"]:
                continue
            candidates.append(candidate)
    return candidates


def write_discovered_sources(candidates: list[dict[str, Any]], output: Path) -> None:
    payload = {"sources": candidates}
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


DEFAULT_SEARCH_PROFILES = {
    "infra_seattle": {
        "preset": "apply",
        "roles": ["Infrastructure", "Systems", "Operations", "Network"],
        "target_only": True,
        "max_years": 3.0,
        "min_resume_match": 3,
        "max_difficulty": 6,
    },
    "network_remote_us": {
        "preset": "apply",
        "roles": ["Network", "Operations", "Cloud"],
        "target_only": True,
        "max_years": 3.0,
        "min_resume_match": 3,
        "max_difficulty": 6,
    },
    "solutions_seattle": {
        "preset": "apply",
        "roles": ["Solutions", "Sales Engineer"],
        "target_only": True,
        "max_years": 3.0,
        "min_resume_match": 3,
        "max_difficulty": 6,
    },
    "operations_remote_us": {
        "preset": "apply",
        "roles": ["Operations", "Systems", "Cloud"],
        "target_only": True,
        "max_years": 3.0,
        "min_resume_match": 3,
        "max_difficulty": 6,
    },
}


def load_profiles(path: Path = DEFAULT_PROFILES) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return DEFAULT_SEARCH_PROFILES
    data = json.loads(path.read_text(encoding="utf-8"))
    profiles = data.get("profiles", data) if isinstance(data, dict) else {}
    merged = dict(DEFAULT_SEARCH_PROFILES)
    for name, profile in profiles.items():
        if isinstance(profile, dict):
            merged[str(name)] = profile
    return merged


def save_default_profiles(path: Path = DEFAULT_PROFILES) -> None:
    if path.exists():
        return
    path.write_text(json.dumps({"profiles": DEFAULT_SEARCH_PROFILES}, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def filter_rows_by_profile(rows: list[sqlite3.Row], profile: dict[str, Any]) -> list[sqlite3.Row]:
    roles = {normalize_role_arg(role) if role in ROLE_BUCKETS else normalize_role_arg(str(role)) for role in profile.get("roles", [])}
    allowed_concerns = {str(item).lower() for item in profile.get("allowed_concerns", [])}
    min_salary = profile.get("min_estimated_salary")
    filtered: list[sqlite3.Row] = []
    for row in rows:
        if roles and row["role_bucket"] not in roles:
            continue
        if min_salary and (row["estimated_salary"] is None or int(row["estimated_salary"]) < int(min_salary)):
            continue
        if allowed_concerns:
            concerns = {part.strip().lower() for part in str(row["concerns"] or "").split(";") if part.strip()}
            if concerns - allowed_concerns:
                continue
        filtered.append(row)
    return filtered


def safe_slug(value: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or fallback


def load_answer_bank(path: Path = DEFAULT_ANSWER_BANK) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"answers": []}


def save_answer_bank(data: dict[str, Any], path: Path = DEFAULT_ANSWER_BANK) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def set_answer(path: Path, question: str, answer: str, category: str = "general") -> None:
    data = load_answer_bank(path)
    answers = data.setdefault("answers", [])
    now = utc_now()
    for item in answers:
        if str(item.get("question", "")).strip().lower() == question.strip().lower():
            item.update({"answer": answer, "category": category, "updated_at": now})
            save_answer_bank(data, path)
            return
    answers.append({"question": question, "answer": answer, "category": category, "updated_at": now, "last_used_at": ""})
    save_answer_bank(data, path)


def answer_bank_mark_used(path: Path) -> list[dict[str, Any]]:
    data = load_answer_bank(path)
    now = utc_now()
    answers = data.get("answers", [])
    for item in answers:
        item["last_used_at"] = now
    save_answer_bank(data, path)
    return answers


def matched_terms_for_row(row: sqlite3.Row) -> list[str]:
    job = row_to_job(row)
    return count_term_hits(text_blob(job), user_strength_terms() + positive_terms())[:10]


def application_packet(conn: sqlite3.Connection, job_id: int, output_dir: Path, answer_bank_path: Path = DEFAULT_ANSWER_BANK) -> Path:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise ValueError(f"No job found with id {job_id}")
    company_slug = safe_slug(row["company"], "company")
    job_slug = safe_slug(row["title"], f"job-{job_id}")
    packet_dir = output_dir / company_slug / f"{job_id}-{job_slug}"
    packet_dir.mkdir(parents=True, exist_ok=True)

    raw = json.loads(row["raw_json"] or "{}")
    (packet_dir / "posting.json").write_text(json.dumps(raw, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    matched = matched_terms_for_row(row)
    missing = [part.strip() for part in str(row["missing_keywords"] or "").split(",") if part.strip()]
    bullets = [
        f"Emphasize {term} from Argos/resume experience." for term in matched[:3]
    ] or ["Emphasize practical troubleshooting, systems ownership, and learning speed."]
    topics = missing[:5] or [row["role_bucket"], "troubleshooting", "networking basics"]
    answers = answer_bank_mark_used(answer_bank_path)
    answer_lines = [
        f"- {item.get('question', '')}: {item.get('answer', '')}"
        for item in answers
    ] or ["- Add reusable answers with `python .\\job_finder.py answer-bank --set \"Question\" --answer \"Answer\"`."]
    notes = f"""# Application Packet

## Job
- Title: {row['title']}
- Company: {row['company']}
- Location: {row['location']}
- Apply: {row['canonical_url']}
- Role bucket: {row['role_bucket']}
- Score: {row['score']}
- Resume profile: {row['best_resume_profile']}

## Why Apply
{row['why_apply'] or row['why_match']}

## Concerns
{row['concerns'] or 'No major concerns detected.'}

## Resume Angle
{chr(10).join('- ' + bullet for bullet in bullets)}

## Likely Interview Topics
{chr(10).join('- ' + topic for topic in topics)}

## Missing Keywords To Consider
{chr(10).join('- ' + item for item in missing) if missing else '- None detected'}

## Reusable Application Answers
{chr(10).join(answer_lines)}
"""
    (packet_dir / "notes.md").write_text(notes, encoding="utf-8")
    status = {
        "job_id": job_id,
        "status": row["status"],
        "created_at": utc_now(),
        "company": row["company"],
        "title": row["title"],
        "canonical_url": row["canonical_url"],
    }
    (packet_dir / "status.json").write_text(json.dumps(status, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return packet_dir


def write_daily_summary(conn: sqlite3.Connection, output: Path, days: int = 1) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    new_rows = conn.execute(
        "SELECT * FROM jobs WHERE first_seen_at >= ? AND active = 1 ORDER BY score DESC LIMIT 50",
        (cutoff,),
    ).fetchall()
    apply_rows = query_jobs(conn, status="active", limit=20, preset="apply", target_only=True)
    review_rows = query_jobs(conn, status="active", limit=20, preset="review", target_only=False)
    lines = [
        "# Daily Job Summary",
        "",
        f"Generated: {datetime.now().astimezone().replace(microsecond=0).isoformat()}",
        f"Window: last {days} day(s)",
        "",
        f"- New active jobs: {len(new_rows)}",
        f"- Apply-first jobs: {len(apply_rows)}",
        f"- Review jobs: {len(review_rows)}",
        "",
        "## Apply First",
    ]
    for row in apply_rows:
        lines.append(f"- [{row['company']} - {row['title']}]({row['canonical_url']}) | {row['location']} | score {row['score']} | {row['why_apply']}")
    lines.extend(["", "## New High-Scoring Jobs"])
    for row in new_rows[:20]:
        lines.append(f"- [{row['company']} - {row['title']}]({row['canonical_url']}) | {row['location']} | score {row['score']} | {row['role_bucket']}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--config", type=Path, default=DEFAULT_USER_CONFIG, help="TOML search customization file")


def add_profile_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", default="", help="Saved search profile name, e.g. infra_seattle")
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent local job finder.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="Fetch enabled sources and update the local DB")
    add_common_args(sync)
    sync.add_argument("--source", help="Sync only one configured source name")
    sync.add_argument("--include-disabled", action="store_true", help="Allow --source to sync a disabled source for testing")

    queue = subparsers.add_parser("queue", help="Show the ranked daily queue")
    add_common_args(queue)
    add_profile_args(queue)
    queue.add_argument("--limit", type=int, default=50)
    queue.add_argument("--min-score", type=int)
    queue.add_argument("--max-years", type=float)
    queue.add_argument("--role", default="", help="Filter by bucket: infrastructure, network, systems, operations, solutions, sales_engineer, cloud, devops_sre, swe, security")
    queue.add_argument("--preset", choices=("apply", "review", "custom"), default="apply")
    queue.add_argument("--include-security", action="store_true")
    queue.add_argument("--include-non-target", action="store_true")
    queue.add_argument("--min-resume-match", type=int)
    queue.add_argument("--max-difficulty", type=int)
    queue.add_argument("--all-locations", action="store_true")
    queue.add_argument("--include-stale", action="store_true")
    queue.add_argument("--stale-days", type=int, default=30)
    queue.add_argument("--source", default="", help="Filter by source name")

    audit = subparsers.add_parser("audit", help="Review ranked jobs in batches")
    add_common_args(audit)
    add_profile_args(audit)
    audit.add_argument("--limit", type=int, default=50)
    audit.add_argument("--offset", type=int, default=0)
    audit.add_argument("--min-score", type=int)
    audit.add_argument("--max-years", type=float)
    audit.add_argument("--role", default="")
    audit.add_argument("--preset", choices=("apply", "review", "custom"), default="review")
    audit.add_argument("--include-security", action="store_true")
    audit.add_argument("--include-non-target", action="store_true")
    audit.add_argument("--min-resume-match", type=int)
    audit.add_argument("--max-difficulty", type=int)
    audit.add_argument("--target-only", action="store_true")
    audit.add_argument("--include-stale", action="store_true")
    audit.add_argument("--status", default="active")
    audit.add_argument("--source", default="", help="Filter by source name")

    search = subparsers.add_parser("search", help="Search stored jobs")
    add_common_args(search)
    add_profile_args(search)
    search.add_argument("--query", "-q", required=True)
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--include-stale", action="store_true")
    search.add_argument("--status", default="active")
    search.add_argument("--max-years", type=float)
    search.add_argument("--role", default="")
    search.add_argument("--preset", choices=("apply", "review", "custom"), default="custom")
    search.add_argument("--include-security", action="store_true")
    search.add_argument("--include-non-target", action="store_true")
    search.add_argument("--min-resume-match", type=int)
    search.add_argument("--max-difficulty", type=int)
    search.add_argument("--source", default="", help="Filter by source name")

    show = subparsers.add_parser("show", help="Show one job with its apply URL")
    add_common_args(show)
    show.add_argument("job_id", type=int)

    verify = subparsers.add_parser("verify", help="Fetch public apply pages, store full text verification, and rescore")
    add_common_args(verify)
    add_profile_args(verify)
    verify.add_argument("--limit", type=int, default=50)
    verify.add_argument("--offset", type=int, default=0)
    verify.add_argument("--status", default="active")
    verify.add_argument("--role", default="")
    verify.add_argument("--source", default="", help="Filter by source name")
    verify.add_argument("--query", default="")
    verify.add_argument("--include-stale", action="store_true")
    verify.add_argument("--stale-days", type=int, default=30)
    verify.add_argument("--all-locations", action="store_true")

    mark = subparsers.add_parser("mark", help="Mark a job status")
    add_common_args(mark)
    mark.add_argument("job_id", type=int)
    mark.add_argument("--status", required=True, choices=sorted(MANUAL_STATUSES))
    mark.add_argument("--notes", default="")
    mark.add_argument("--reason", default="", help="Reason such as too_senior, security, bad_location, swe, low_match")

    export = subparsers.add_parser("export", help="Export stored jobs to CSV")
    add_common_args(export)
    add_profile_args(export)
    export.add_argument("--status", default="active")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--include-stale", action="store_true")
    export.add_argument("--limit", type=int, default=10000)
    export.add_argument("--min-score", type=int)
    export.add_argument("--max-years", type=float)
    export.add_argument("--role", default="")
    export.add_argument("--preset", choices=("apply", "review", "custom"), default="apply")
    export.add_argument("--include-security", action="store_true")
    export.add_argument("--include-non-target", action="store_true")
    export.add_argument("--min-resume-match", type=int)
    export.add_argument("--max-difficulty", type=int)
    export.add_argument("--all-locations", action="store_true")
    export.add_argument("--source", default="", help="Filter by source name")

    report = subparsers.add_parser("report", help="Export stored jobs to a clickable HTML report")
    add_common_args(report)
    add_profile_args(report)
    report.add_argument("--status", default="active")
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--title", default="Job Finder Report")
    report.add_argument("--include-stale", action="store_true")
    report.add_argument("--limit", type=int, default=200)
    report.add_argument("--min-score", type=int)
    report.add_argument("--max-years", type=float)
    report.add_argument("--role", default="")
    report.add_argument("--preset", choices=("apply", "review", "custom"), default="apply")
    report.add_argument("--include-security", action="store_true")
    report.add_argument("--include-non-target", action="store_true")
    report.add_argument("--min-resume-match", type=int)
    report.add_argument("--max-difficulty", type=int)
    report.add_argument("--all-locations", action="store_true")
    report.add_argument("--source", default="", help="Filter by source name")

    discover = subparsers.add_parser("discover-sources", help="Generate ATS source candidates from a company watchlist")
    add_common_args(discover)
    discover.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    discover.add_argument("--output", type=Path, default=DEFAULT_DISCOVERED_SOURCES)
    discover.add_argument("--verify", action="store_true", help="Probe candidate endpoints before writing output")
    discover.add_argument("--verified-only", action="store_true", help="Write only candidates that passed --verify")

    packet = subparsers.add_parser("packet", help="Create a local application packet for one job")
    add_common_args(packet)
    packet.add_argument("job_id", type=int)
    packet.add_argument("--output-dir", type=Path, default=DEFAULT_APPLICATIONS_DIR)
    packet.add_argument("--answer-bank", type=Path, default=DEFAULT_ANSWER_BANK)

    answer_bank = subparsers.add_parser("answer-bank", help="Store/list reusable application answers")
    add_common_args(answer_bank)
    answer_bank.add_argument("--path", type=Path, default=DEFAULT_ANSWER_BANK)
    answer_bank.add_argument("--list", action="store_true")
    answer_bank.add_argument("--set", dest="question", help="Question text to create/update")
    answer_bank.add_argument("--answer", default="")
    answer_bank.add_argument("--category", default="general")

    profiles_cmd = subparsers.add_parser("profiles", help="List or initialize saved search profiles")
    add_common_args(profiles_cmd)
    profiles_cmd.add_argument("--path", type=Path, default=DEFAULT_PROFILES)
    profiles_cmd.add_argument("--init", action="store_true")

    summary = subparsers.add_parser("summary", help="Write a daily markdown summary")
    add_common_args(summary)
    summary.add_argument("--output", type=Path, default=DEFAULT_ALERTS_DIR / "daily-summary.md")
    summary.add_argument("--days", type=int, default=1)

    rescore = subparsers.add_parser("rescore", help="Refresh stored job scores without syncing")
    add_common_args(rescore)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_user(getattr(args, "config", DEFAULT_USER_CONFIG))
    conn = connect(args.db)

    if args.command == "sync":
        sources = load_sources(args.sources)
        totals = sync_sources(conn, sources, only=args.source, include_disabled=args.include_disabled)
        print(
            f"Done: {totals['new']} new, {totals['changed']} changed, "
            f"{totals['unchanged']} unchanged, {totals['missing']} missing, {totals['failed']} failed"
        )
        return 0 if totals["failed"] == 0 else 2

    if args.command == "queue":
        rows = query_jobs_for_args(conn, args)
        print_table(rows)
        return 0

    if args.command == "audit":
        rows = query_jobs_for_args(conn, args)
        print_audit(rows)
        return 0

    if args.command == "search":
        rows = query_jobs_for_args(conn, args)
        print_table(rows)
        return 0

    if args.command == "show":
        show_job(conn, args.job_id)
        return 0

    if args.command == "verify":
        rows = query_jobs(
            conn,
            query=args.query,
            status=args.status,
            limit=args.limit,
            offset=args.offset,
            include_stale=args.include_stale,
            stale_days=args.stale_days,
            target_only=not args.all_locations,
            role=args.role,
            preset="custom",
            source=args.source,
        )
        totals = verify_jobs(conn, rows)
        summary = ", ".join(f"{key}={value}" for key, value in sorted(totals.items())) or "none"
        print(f"Verified {len(rows)} jobs: {summary}")
        return 0

    if args.command == "mark":
        mark_job(conn, args.job_id, args.status, args.notes, args.reason)
        print(f"Marked job {args.job_id} as {args.status}")
        return 0

    if args.command == "export":
        rows = query_jobs_for_args(conn, args)
        export_rows(rows, args.output)
        print(f"Wrote {len(rows)} jobs to {args.output}")
        return 0

    if args.command == "report":
        rows = query_jobs_for_args(conn, args)
        export_html_report(rows, args.output, title=args.title)
        print(f"Wrote {len(rows)} jobs to {args.output}")
        return 0

    if args.command == "discover-sources":
        watchlist = load_watchlist(args.watchlist)
        candidates = discover_source_candidates(
            watchlist,
            verify=args.verify,
            verified_only=args.verified_only,
        )
        write_discovered_sources(candidates, args.output)
        verified_count = sum(1 for candidate in candidates if candidate.get("verified"))
        print(f"Wrote {len(candidates)} source candidates to {args.output} ({verified_count} verified)")
        return 0

    if args.command == "packet":
        packet_dir = application_packet(conn, args.job_id, args.output_dir, args.answer_bank)
        print(f"Wrote application packet to {packet_dir}")
        return 0

    if args.command == "answer-bank":
        if args.question:
            if not args.answer:
                raise ValueError("--answer is required with --set")
            set_answer(args.path, args.question, args.answer, args.category)
            print(f"Saved answer to {args.path}")
            return 0
        data = load_answer_bank(args.path)
        for item in data.get("answers", []):
            print(f"{item.get('category', 'general')}: {console_text(item.get('question'))} -> {console_text(item.get('answer'))}")
        if not data.get("answers"):
            print(f"No answers stored yet. Add one with --set and --answer.")
        return 0

    if args.command == "profiles":
        if args.init:
            save_default_profiles(args.path)
            print(f"Wrote default profiles to {args.path}")
        profiles = load_profiles(args.path)
        for name, profile in profiles.items():
            roles = ", ".join(profile.get("roles", []))
            print(f"{name}: roles={roles}; preset={profile.get('preset', 'apply')}")
        return 0

    if args.command == "summary":
        write_daily_summary(conn, args.output, args.days)
        print(f"Wrote daily summary to {args.output}")
        return 0

    if args.command == "rescore":
        count = rescore_jobs(conn)
        print(f"Rescored {count} jobs")
        return 0

    parser.error(f"Unhandled command {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
