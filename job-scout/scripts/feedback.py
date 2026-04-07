"""
feedback.py
Reads user decisions from Notion and generates actionable feedback
to improve future pipeline runs.

Tracks patterns like:
- Which classifications does the user actually apply to?
- Which industries/companies get consistently skipped?
- What score ranges lead to applications vs skips?
- Are there title patterns in applied vs skipped jobs?

Outputs adjustments to strategy.json's feedback_log.
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)


def fetch_all_notion_entries():
    """Fetch all entries from the Notion jobs database."""
    token = os.environ["NOTION_TOKEN"]
    db_id = os.environ["NOTION_DATABASE_ID"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    all_pages = []
    has_more = True
    start_cursor = None

    while has_more:
        body = {"page_size": 100}
        if start_cursor:
            body["start_cursor"] = start_cursor
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=headers,
            json=body,
        )
        data = resp.json()
        all_pages.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    return all_pages


def extract_job_data(page):
    """Extract structured data from a Notion page."""
    props = page.get("properties", {})

    def get_text(prop_name):
        prop = props.get(prop_name, {})
        if prop.get("type") == "title":
            items = prop.get("title", [])
        elif prop.get("type") == "rich_text":
            items = prop.get("rich_text", [])
        else:
            items = []
        return items[0]["plain_text"] if items else ""

    def get_select(prop_name):
        prop = props.get(prop_name, {})
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""

    def get_number(prop_name):
        prop = props.get(prop_name, {})
        return prop.get("number", 0) or 0

    def get_url(prop_name):
        prop = props.get(prop_name, {})
        return prop.get("url", "") or ""

    return {
        "title": get_text("Job Title"),
        "company": get_text("Company"),
        "score": get_number("Fit Score"),
        "classification": get_select("Classification"),
        "status": get_select("Status"),
        "url": get_url("Job URL"),
        "date": props.get("Date Surfaced", {}).get("date", {}).get("start", "") if props.get("Date Surfaced", {}).get("date") else "",
    }


def analyse_feedback(entries):
    """
    Analyse user decisions and generate feedback insights.
    Returns a dict of patterns and suggested adjustments.
    """
    applied = [e for e in entries if e["status"] == "Applied"]
    skipped = [e for e in entries if e["status"] == "Skipped"]
    new = [e for e in entries if e["status"] == "New"]
    near_relevant = [e for e in entries if e["status"] == "Near-Relevant"]

    insights = {
        "timestamp": datetime.now().isoformat(),
        "total_entries": len(entries),
        "status_counts": {
            "applied": len(applied),
            "skipped": len(skipped),
            "new": len(new),
            "near_relevant": len(near_relevant),
        },
        "patterns": {},
        "suggestions": [],
    }

    # Not enough data to draw conclusions
    if len(applied) + len(skipped) < 3:
        insights["patterns"]["status"] = "insufficient_data"
        insights["suggestions"].append(
            "Not enough decisions yet — mark jobs as 'Applied' or 'Skipped' in Notion to start generating feedback."
        )
        return insights

    # ── Score patterns ──────────────────────────────────────
    applied_scores = [e["score"] for e in applied if e["score"] > 0]
    skipped_scores = [e["score"] for e in skipped if e["score"] > 0]

    if applied_scores:
        insights["patterns"]["applied_score_range"] = {
            "min": min(applied_scores),
            "max": max(applied_scores),
            "avg": round(sum(applied_scores) / len(applied_scores), 1),
        }
    if skipped_scores:
        insights["patterns"]["skipped_score_range"] = {
            "min": min(skipped_scores),
            "max": max(skipped_scores),
            "avg": round(sum(skipped_scores) / len(skipped_scores), 1),
        }

    # Suggest threshold adjustment
    if applied_scores and skipped_scores:
        min_applied = min(applied_scores)
        max_skipped = max(skipped_scores)
        if min_applied > max_skipped + 10:
            insights["suggestions"].append(
                f"You only apply to jobs scoring {min_applied}+. Consider raising the deep analysis threshold to {int(min_applied - 5)} to save LLM costs on jobs you'll skip anyway."
            )

    # ── Classification patterns ─────────────────────────────
    applied_cls = Counter(e["classification"] for e in applied)
    skipped_cls = Counter(e["classification"] for e in skipped)
    insights["patterns"]["applied_classifications"] = dict(applied_cls)
    insights["patterns"]["skipped_classifications"] = dict(skipped_cls)

    if skipped_cls.get("Reach", 0) > 3 and applied_cls.get("Reach", 0) == 0:
        insights["suggestions"].append(
            "You consistently skip 'Reach' jobs. Consider filtering them out before deep analysis to save costs."
        )
    if applied_cls.get("Stretch", 0) > applied_cls.get("Safe", 0):
        insights["suggestions"].append(
            "You apply to more 'Stretch' than 'Safe' roles — your ambition is higher than the pipeline's safety threshold. This is fine, no adjustment needed."
        )

    # ── Company/industry patterns ───────────────────────────
    skipped_companies = Counter(e["company"] for e in skipped)
    applied_companies = Counter(e["company"] for e in applied)
    insights["patterns"]["skipped_companies"] = dict(skipped_companies.most_common(5))
    insights["patterns"]["applied_companies"] = dict(applied_companies.most_common(5))

    # Detect repeatedly skipped companies
    repeat_skips = {c: n for c, n in skipped_companies.items() if n >= 2}
    if repeat_skips:
        insights["suggestions"].append(
            f"Repeatedly skipped companies: {', '.join(repeat_skips.keys())}. Consider adding to blocked list."
        )

    # ── Title keyword patterns ──────────────────────────────
    def extract_title_keywords(jobs):
        words = Counter()
        for j in jobs:
            for w in j["title"].lower().split():
                if len(w) > 3 and w not in {"senior", "junior", "lead", "the", "and", "for"}:
                    words[w] += 1
        return words

    applied_keywords = extract_title_keywords(applied)
    skipped_keywords = extract_title_keywords(skipped)

    # Keywords that appear in skipped but not applied
    skip_only = {k: v for k, v in skipped_keywords.items() if v >= 2 and k not in applied_keywords}
    if skip_only:
        insights["patterns"]["skip_only_keywords"] = dict(Counter(skip_only).most_common(5))
        insights["suggestions"].append(
            f"Title keywords that appear in skipped jobs but not applied ones: {', '.join(skip_only.keys())}. These might be worth filtering."
        )

    # Keywords that appear in applied jobs
    apply_keywords = {k: v for k, v in applied_keywords.items() if v >= 2}
    if apply_keywords:
        insights["patterns"]["applied_keywords"] = dict(Counter(apply_keywords).most_common(5))

    return insights


def update_strategy_feedback(insights, strategy_path="strategy.json"):
    """
    Write actionable feedback entries to strategy.json's feedback_log.
    Only adds new entries, doesn't duplicate.
    """
    with open(strategy_path, "r") as f:
        strategy = json.load(f)

    feedback_log = strategy.get("feedback_log", [])
    existing_types = {(e.get("type"), e.get("value")) for e in feedback_log}

    # Auto-block repeatedly skipped companies
    repeat_skips = insights.get("patterns", {}).get("skipped_companies", {})
    for company, count in repeat_skips.items():
        if count >= 3 and ("block_company", company) not in existing_types:
            feedback_log.append({
                "type": "block_company",
                "value": company,
                "reason": f"Skipped {count} times in Notion",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "auto": True,
            })
            print(f"[feedback] Auto-blocked company: {company} (skipped {count}x)")

    strategy["feedback_log"] = feedback_log
    with open(strategy_path, "w") as f:
        json.dump(strategy, f, indent=2)

    return feedback_log


def run_feedback(strategy_path="strategy.json"):
    """
    Full feedback pipeline: fetch Notion → analyse → update strategy → return insights.
    """
    print("[feedback] Fetching entries from Notion...")
    pages = fetch_all_notion_entries()
    entries = [extract_job_data(p) for p in pages]
    print(f"[feedback] Found {len(entries)} entries")

    insights = analyse_feedback(entries)

    # Print insights
    print(f"\n[feedback] Status counts: {insights['status_counts']}")

    patterns = insights.get("patterns", {})
    if patterns.get("applied_score_range"):
        r = patterns["applied_score_range"]
        print(f"[feedback] Applied score range: {r['min']}-{r['max']} (avg {r['avg']})")
    if patterns.get("skipped_score_range"):
        r = patterns["skipped_score_range"]
        print(f"[feedback] Skipped score range: {r['min']}-{r['max']} (avg {r['avg']})")
    if patterns.get("applied_classifications"):
        print(f"[feedback] Applied classifications: {patterns['applied_classifications']}")
    if patterns.get("skipped_classifications"):
        print(f"[feedback] Skipped classifications: {patterns['skipped_classifications']}")

    if insights["suggestions"]:
        print(f"\n[feedback] Suggestions:")
        for s in insights["suggestions"]:
            print(f"  → {s}")
    else:
        print("[feedback] No suggestions yet.")

    # Update strategy with auto-actions
    update_strategy_feedback(insights, strategy_path)

    # Save insights log
    os.makedirs("logs", exist_ok=True)
    log_path = f"logs/feedback_{datetime.now().strftime('%Y-%m-%d')}.json"
    with open(log_path, "w") as f:
        json.dump(insights, f, indent=2)
    print(f"[feedback] Insights saved to {log_path}")

    return insights


if __name__ == "__main__":
    run_feedback()
