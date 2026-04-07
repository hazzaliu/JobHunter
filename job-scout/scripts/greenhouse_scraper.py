"""
greenhouse_scraper.py
Supplementary job source — fetches open positions directly from Greenhouse ATS boards.

Many tech companies (Canva, Atlassian, Culture Amp, etc.) use Greenhouse.
The boards API is free, requires no auth, and returns structured JSON.
Runs alongside Apify/LinkedIn to catch jobs LinkedIn misses.
"""

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def _strip_html(text):
    """Strip HTML tags from Greenhouse job content."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _matches_location(job, location_filters):
    """Check if a Greenhouse job matches any of the configured location filters."""
    if not location_filters:
        return True
    job_location = (job.get("location", {}).get("name", "") or "").lower()
    return any(loc.lower() in job_location for loc in location_filters)


def _fetch_slug(slug, company_name, location_filters):
    """Fetch all jobs for a single Greenhouse board slug."""
    try:
        url = GREENHOUSE_API.format(slug=slug)
        resp = requests.get(url, params={"content": "true"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        jobs = []
        for gh_job in data.get("jobs", []):
            if not _matches_location(gh_job, location_filters):
                continue

            description = _strip_html(gh_job.get("content", ""))

            job = {
                "id": f"greenhouse-{slug}-{gh_job['id']}",
                "title": gh_job.get("title", ""),
                "company": company_name,
                "description": description,
                "requirements": "",
                "seniority": "",
                "url": gh_job.get("absolute_url", ""),
                "posted_date": gh_job.get("updated_at", ""),
                "location": gh_job.get("location", {}).get("name", "") if isinstance(gh_job.get("location"), dict) else "",
                "employment_type": "",
                "search_term": "greenhouse_direct",
                "scraped_at": datetime.now().isoformat(),
            }

            if job["title"] and job["url"]:
                jobs.append(job)

        return jobs
    except Exception as e:
        print(f"[greenhouse] Error fetching {slug} ({company_name}): {e}", file=sys.stderr)
        return []


def scrape_greenhouse_jobs(config, strategy):
    """
    Fetch jobs from configured Greenhouse boards.
    Returns same format as apify_scraper: {"status": "ok"|"no_results", "jobs": [...]}.
    """
    gh_config = config.get("greenhouse", {})
    if not gh_config.get("enabled", False):
        return {"status": "disabled", "jobs": []}

    slugs = gh_config.get("slugs", {})
    if not slugs:
        return {"status": "no_slugs", "jobs": []}

    location_filters = gh_config.get("location_filter", ["Melbourne", "Australia", "Remote"])

    all_jobs = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_fetch_slug, slug, company_name, location_filters): slug
            for slug, company_name in slugs.items()
        }
        for future in as_completed(futures):
            slug = futures[future]
            try:
                jobs = future.result()
                all_jobs.extend(jobs)
            except Exception as e:
                print(f"[greenhouse] Error for {slug}: {e}", file=sys.stderr)

    if not all_jobs:
        return {"status": "no_results", "jobs": []}

    print(f"[greenhouse] Fetched {len(all_jobs)} jobs from {len(slugs)} Greenhouse boards.")
    return {"status": "ok", "jobs": all_jobs}


if __name__ == "__main__":
    # Quick test with a known Greenhouse board
    test_config = {
        "greenhouse": {
            "enabled": True,
            "slugs": {"canva": "Canva"},
            "location_filter": ["Melbourne", "Australia", "Remote"],
        }
    }
    result = scrape_greenhouse_jobs(test_config, {})
    print(f"Status: {result['status']}, Jobs: {len(result['jobs'])}")
    for job in result["jobs"][:3]:
        print(f"  {job['title']} — {job['location']}")
