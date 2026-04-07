"""
apify_scraper.py
Calls the Apify LinkedIn Jobs Scraper and returns structured job listings.
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from apify_client import ApifyClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)


def load_config(config_path="config.json"):
    with open(config_path, "r") as f:
        config = json.load(f)
    # Override secrets from .env
    config["apify"]["api_token"] = os.environ["APIFY_API_TOKEN"]
    config["apify"]["actor_id"] = "harvestapi/linkedin-job-search"
    config["notion"]["token"] = os.environ["NOTION_TOKEN"]
    config["notion"]["jobs_database_id"] = os.environ["NOTION_DATABASE_ID"]
    config["discord"]["webhook_url"] = os.environ["DISCORD_WEBHOOK_URL"]
    return config


def load_strategy(strategy_path="strategy.json"):
    with open(strategy_path, "r") as f:
        return json.load(f)


def _scrape_term(client, actor_id, term, location, max_items, posted_limit):
    """Scrape jobs for a single search term. Designed to run in a thread."""
    try:
        run_input = {
            "jobTitles": [term],
            "locations": [location],
            "maxItems": max_items,
            "employmentType": ["full-time"],
            "experienceLevel": ["mid-senior"],
            "postedLimit": posted_limit,
        }

        run = client.actor(actor_id).call(run_input=run_input)
        dataset_id = run["defaultDatasetId"]

        jobs = []
        for item in client.dataset(dataset_id).iterate_items():
            company_raw = item.get("company", "")
            if isinstance(company_raw, dict):
                company_name = company_raw.get("name", "") or company_raw.get("companyName", "")
            else:
                company_name = item.get("companyName", "") or str(company_raw)

            job = {
                "id": item.get("id") or item.get("jobId") or item.get("linkedinUrl", ""),
                "title": item.get("title", ""),
                "company": company_name,
                "description": item.get("descriptionText", "") or item.get("description", ""),
                "requirements": item.get("requirements", ""),
                "seniority": item.get("experienceLevel", "") or item.get("seniorityLevel", ""),
                "url": item.get("linkedinUrl", "") or item.get("url", "") or item.get("jobUrl", ""),
                "posted_date": item.get("postedDate", "") or item.get("postedAt", ""),
                "location": (item.get("location", {}).get("linkedinText", "")
                            if isinstance(item.get("location"), dict)
                            else item.get("location", "")),
                "employment_type": item.get("employmentType", ""),
                "search_term": term,
                "scraped_at": datetime.now().isoformat()
            }

            if job["title"] and job["company"] and job["url"]:
                jobs.append(job)

        return jobs
    except Exception as e:
        print(f"[apify_scraper] Error for term '{term}': {e}", file=sys.stderr)
        return []


def scrape_jobs(config, strategy, max_concurrent=3):
    """
    Calls Apify LinkedIn Jobs Scraper with search terms from strategy.
    Runs up to max_concurrent search terms in parallel.
    Returns list of structured job objects, or a status dict on failure.
    """
    client = ApifyClient(config["apify"]["api_token"])
    actor_id = config["apify"]["actor_id"]

    search_config = strategy["search_configuration"]
    location = search_config["location"]
    search_terms = search_config["search_terms"]
    max_results = config["apify"].get("max_results_per_run", 50)
    max_items = max(5, max_results // len(search_terms))

    # Map config posted_within_hours to Apify's postedLimit values
    posted_hours = config["apify"].get("posted_within_hours", 72)
    if posted_hours <= 1:
        posted_limit = "1h"
    elif posted_hours <= 24:
        posted_limit = "24h"
    elif posted_hours <= 168:
        posted_limit = "week"
    else:
        posted_limit = "month"

    all_jobs = []

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {
            executor.submit(
                _scrape_term, client, actor_id, term, location, max_items, posted_limit
            ): term
            for term in search_terms
        }
        for future in as_completed(futures):
            term = futures[future]
            try:
                jobs = future.result()
                all_jobs.extend(jobs)
            except Exception as e:
                print(f"[apify_scraper] Error for term '{term}': {e}", file=sys.stderr)

    if not all_jobs:
        return {"status": "no_results", "jobs": []}

    # Deduplicate within this run by URL
    seen_urls = set()
    unique_jobs = []
    for job in all_jobs:
        if job["url"] not in seen_urls:
            seen_urls.add(job["url"])
            unique_jobs.append(job)

    print(f"[apify_scraper] Scraped {len(unique_jobs)} unique jobs across {len(search_terms)} search terms.")
    return {"status": "ok", "jobs": unique_jobs}


if __name__ == "__main__":
    config = load_config()
    strategy = load_strategy()
    result = scrape_jobs(config, strategy)
    print(json.dumps(result, indent=2))
