"""
deduplicator.py
Filters scraped jobs against the seen jobs log, blocked list, and pre-filter rules.
"""

import json
import os
import re
import tempfile


def load_seen_jobs(path="seen_jobs.json"):
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def save_seen_jobs(seen, path="seen_jobs.json", max_entries=2000):
    """Save seen jobs with atomic write and size cap."""
    # Trim to most recent entries if over limit
    if len(seen) > max_entries:
        seen = seen[-max_entries:]
    # Atomic write: write to temp file, then rename
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(seen, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def load_strategy(path="strategy.json"):
    with open(path, "r") as f:
        return json.load(f)


ENTRY_LEVEL_SIGNALS = [
    "graduate", "grad role", "entry level", "entry-level",
    "junior", "intern", "internship", "cadet", "apprentice",
    "0-1 year", "0-2 year", "no experience required"
]

EXCLUDED_SENIORITY = ["internship", "entry level", "associate (entry)"]


def is_entry_level(job):
    """Returns True if the job appears to be entry-level or graduate."""
    title_lower = job.get("title", "").lower()
    desc_lower = (job.get("description", "") + " " + job.get("requirements", "")).lower()
    seniority_lower = job.get("seniority", "").lower()

    if seniority_lower in EXCLUDED_SENIORITY:
        return True

    for signal in ENTRY_LEVEL_SIGNALS:
        if signal in title_lower or signal in desc_lower[:500]:
            return True

    return False


def is_blocked(job, strategy):
    """Returns True if the job matches a blocked industry or company."""
    avoid_industries = [i.lower() for i in strategy.get("avoid_industries", [])]
    blocked_companies = [c.lower() for c in strategy.get("company_preferences", {}).get("blocked_companies", [])]

    # Check feedback log for blocked industries/companies added by user
    for entry in strategy.get("feedback_log", []):
        if entry.get("type") == "block_industry":
            avoid_industries.append(entry["value"].lower())
        if entry.get("type") == "block_company":
            blocked_companies.append(entry["value"].lower())

    company_lower = job.get("company", "").lower()
    desc_lower = (job.get("description", "") + " " + job.get("title", "")).lower()

    for blocked in blocked_companies:
        if blocked in company_lower:
            return True, f"Company '{job['company']}' is on the blocked list"

    # Consultancy check — only match on company name, not JD text
    # (JDs often say "consult with stakeholders" which is normal PM language)
    BIG_FOUR = ["deloitte", "pwc", "kpmg", "ernst & young", "ey ", " ey,"]
    if "consult" in company_lower:
        is_big_four = any(b in company_lower for b in BIG_FOUR)
        if not is_big_four:
            return True, "Consultancy company (not Big 4)"

    return False, None


def is_not_melbourne(job):
    """Returns True if the job location clearly excludes Melbourne."""
    location = job.get("location", "").lower()
    if not location:
        return False  # Don't filter if location is missing
    non_melbourne = ["sydney", "brisbane", "perth", "adelaide", "canberra", "new zealand", "singapore", "london"]
    for city in non_melbourne:
        if city in location and "melbourne" not in location:
            return True
    return False


def filter_jobs(scraped_jobs, seen_jobs_path="seen_jobs.json", strategy_path="strategy.json"):
    """
    Main filter function. Takes raw scraped jobs and returns:
    - new_jobs: list of jobs that passed all filters
    - stats: summary of what was filtered and why
    """
    seen = load_seen_jobs(seen_jobs_path)
    strategy = load_strategy(strategy_path)

    seen_ids = set(entry["id"] for entry in seen if isinstance(entry, dict))
    # Support legacy flat list of IDs
    seen_ids.update(entry for entry in seen if isinstance(entry, str))

    new_jobs = []
    stats = {
        "total_scraped": len(scraped_jobs),
        "filtered_duplicate": 0,
        "filtered_entry_level": 0,
        "filtered_blocked": 0,
        "filtered_location": 0,
        "passed": 0
    }

    newly_seen = []

    for job in scraped_jobs:
        job_id = job.get("id") or job.get("url", "")

        # 1. Deduplication check
        if job_id in seen_ids:
            stats["filtered_duplicate"] += 1
            continue

        # 2. Location check
        if is_not_melbourne(job):
            stats["filtered_location"] += 1
            seen_ids.add(job_id)
            newly_seen.append({"id": job_id, "title": job["title"], "company": job["company"], "reason": "wrong_location"})
            continue

        # 3. Entry-level check
        if is_entry_level(job):
            stats["filtered_entry_level"] += 1
            seen_ids.add(job_id)
            newly_seen.append({"id": job_id, "title": job["title"], "company": job["company"], "reason": "entry_level"})
            continue

        # 4. Blocked industry/company check
        blocked, reason = is_blocked(job, strategy)
        if blocked:
            stats["filtered_blocked"] += 1
            seen_ids.add(job_id)
            newly_seen.append({"id": job_id, "title": job["title"], "company": job["company"], "reason": reason})
            continue

        # Passed all filters
        new_jobs.append(job)
        seen_ids.add(job_id)
        newly_seen.append({"id": job_id, "title": job["title"], "company": job["company"], "reason": "passed"})
        stats["passed"] += 1

    # Don't persist seen jobs yet — caller should commit after pipeline succeeds
    # This prevents jobs from being permanently buried by transient failures

    if stats["filtered_duplicate"] == stats["total_scraped"]:
        return {"status": "all_duplicate", "jobs": [], "stats": stats, "newly_seen": newly_seen}

    if not new_jobs and stats["total_scraped"] == 0:
        return {"status": "no_results", "jobs": [], "stats": stats, "newly_seen": newly_seen}

    print(f"[deduplicator] {stats['passed']} jobs passed | "
          f"{stats['filtered_duplicate']} duplicates | "
          f"{stats['filtered_entry_level']} entry-level | "
          f"{stats['filtered_blocked']} blocked | "
          f"{stats['filtered_location']} wrong location")

    return {"status": "ok", "jobs": new_jobs, "stats": stats, "newly_seen": newly_seen}


if __name__ == "__main__":
    import sys
    # Test mode: pass a JSON file of scraped jobs as argument
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            data = json.load(f)
        result = filter_jobs(data.get("jobs", []))
        print(json.dumps(result, indent=2))
