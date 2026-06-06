#!/usr/bin/env python3
"""Search the public Newgrad Jobs embedded listings.

This intentionally reads the server-rendered mini-site pages instead of
calling Jobright's internal pagination API.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


LANDING_URL = "https://www.newgrad-jobs.com/"
MINISITE_URL = "https://jobright.ai/minisites-jobs/newgrad/{path}?embed=true"
USER_AGENT = "job-finder-research/0.1 (+local personal job search)"


@dataclass(frozen=True)
class Category:
    path: str
    short: str
    name: str
    airtable: str


def fetch_text(url: str, timeout: int = 30) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def extract_categories(html_text: str) -> list[Category]:
    pattern = re.compile(
        r'<h2[^>]*data-job-path="([^"]+)"[^>]*'
        r'airtable-link="([^"]+)"[^>]*short-link="([^"]+)"[^>]*>'
        r"(.*?)</h2>",
        re.DOTALL,
    )
    categories: list[Category] = []
    for match in pattern.finditer(html_text):
        path, airtable, short, raw_name = match.groups()
        name = re.sub(r"<[^>]+>", "", raw_name)
        name = html.unescape(name).strip()
        categories.append(
            Category(
                path=path.strip("/"),
                short=short,
                name=name,
                airtable=html.unescape(airtable),
            )
        )
    return categories


def extract_next_data(html_text: str) -> dict:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html_text,
        re.DOTALL,
    )
    if not match:
        raise ValueError("Could not find __NEXT_DATA__ in embedded page")
    return json.loads(html.unescape(match.group(1)))


def normalize_job(raw: dict, category: Category, total: int | None) -> dict:
    posted_ms = raw.get("postedDate")
    posted_iso = ""
    if isinstance(posted_ms, (int, float)):
        posted_iso = datetime.fromtimestamp(posted_ms / 1000, tz=timezone.utc).isoformat()

    return {
        "id": raw.get("id", ""),
        "title": raw.get("title", ""),
        "company": raw.get("company", ""),
        "location": raw.get("location", ""),
        "salary": raw.get("salary", ""),
        "salary_min": salary_min(raw.get("salary", "")),
        "posted_ms": posted_ms or 0,
        "posted_date": posted_iso,
        "apply_url": raw.get("applyUrl", ""),
        "work_model": raw.get("workModel", ""),
        "h1b_sponsored": raw.get("h1bSponsored", ""),
        "is_new_grad": raw.get("isNewGrad"),
        "industry": "; ".join(raw.get("industry") or []),
        "qualifications": raw.get("qualifications", ""),
        "category_path": category.path,
        "category_name": category.name,
        "category_total": total or "",
    }


def salary_min(value: str) -> float:
    if not value:
        return 0.0
    numbers = re.findall(r"\$?([0-9][0-9,]*(?:\.[0-9]+)?)", value)
    if not numbers:
        return 0.0
    return float(numbers[0].replace(",", ""))


def load_category(category: Category) -> tuple[list[dict], int | None]:
    url = MINISITE_URL.format(path=quote(category.path, safe="/"))
    data = extract_next_data(fetch_text(url))
    page_props = data.get("props", {}).get("pageProps", {})
    total = page_props.get("initialTotal")
    raw_jobs = page_props.get("initialJobs") or []
    return [normalize_job(job, category, total) for job in raw_jobs], total


def matches(job: dict, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        str(job.get(key, ""))
        for key in (
            "title",
            "company",
            "location",
            "work_model",
            "h1b_sponsored",
            "industry",
            "qualifications",
            "category_name",
        )
    ).lower()
    return all(term in haystack for term in query.lower().split())


def sort_jobs(jobs: list[dict], sort_key: str, descending: bool) -> list[dict]:
    keys = {
        "posted": lambda job: job.get("posted_ms") or 0,
        "salary": lambda job: job.get("salary_min") or 0,
        "title": lambda job: str(job.get("title", "")).lower(),
        "company": lambda job: str(job.get("company", "")).lower(),
        "location": lambda job: str(job.get("location", "")).lower(),
    }
    return sorted(jobs, key=keys[sort_key], reverse=descending)


def dedupe(jobs: Iterable[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for job in jobs:
        key = str(job.get("id") or (job.get("title"), job.get("company"), job.get("location")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)
    return unique


def write_output(jobs: list[dict], output: Path) -> None:
    if output.suffix.lower() == ".json":
        output.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
        return

    fieldnames = [
        "title",
        "company",
        "location",
        "salary",
        "posted_date",
        "work_model",
        "h1b_sponsored",
        "is_new_grad",
        "industry",
        "category_name",
        "apply_url",
        "qualifications",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for job in jobs:
            writer.writerow({key: job.get(key, "") for key in fieldnames})


def print_rows(jobs: list[dict], limit: int) -> None:
    for job in jobs[:limit]:
        print(
            f"{job['posted_date'][:10]:10} | {job['salary'][:18]:18} | "
            f"{job['work_model'][:8]:8} | {job['title'][:54]:54} | "
            f"{job['company'][:28]:28} | {job['location'][:32]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Search public newgrad-jobs.com listings.")
    parser.add_argument("--country", choices=["us", "ca"], default="us")
    parser.add_argument("--category", help="Category slug, short code, or name fragment, e.g. swe")
    parser.add_argument("--query", "-q", default="", help="Search terms to match across job fields")
    parser.add_argument(
        "--sort",
        choices=["posted", "salary", "title", "company", "location"],
        default="posted",
    )
    parser.add_argument("--ascending", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--output", type=Path, help="Write results to .csv or .json")
    parser.add_argument("--sleep", type=float, default=0.4, help="Delay between category requests")
    args = parser.parse_args()

    try:
        categories = extract_categories(fetch_text(LANDING_URL))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        print(f"Failed to load category list: {exc}", file=sys.stderr)
        return 1

    selected = [cat for cat in categories if cat.path.startswith(f"{args.country}/")]
    if args.category:
        needle = args.category.lower()
        selected = [
            cat
            for cat in selected
            if needle in cat.path.lower()
            or needle == cat.short.lower()
            or needle in cat.name.lower()
        ]
    if not selected:
        print("No matching categories found.", file=sys.stderr)
        return 1

    all_jobs: list[dict] = []
    for index, category in enumerate(selected, start=1):
        try:
            jobs, total = load_category(category)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            print(f"Skipping {category.path}: {exc}", file=sys.stderr)
            continue
        print(f"Loaded {len(jobs):>3} / {total or '?':>5} from {category.path} ({category.name})", file=sys.stderr)
        all_jobs.extend(jobs)
        if index < len(selected):
            time.sleep(args.sleep)

    filtered = [job for job in dedupe(all_jobs) if matches(job, args.query)]
    sorted_results = sort_jobs(filtered, args.sort, not args.ascending)

    if args.output:
        write_output(sorted_results, args.output)
        print(f"Wrote {len(sorted_results)} jobs to {args.output}", file=sys.stderr)
    print_rows(sorted_results, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
