"""
run_daily_scout.py
Main daily orchestration script. Run this every morning via Claude Scheduled Tasks.

Pipeline:
  1. Load config and strategy
  2. Scrape jobs via Apify (broad search terms)
  3. Deduplicate and pre-filter
  4. Embed & rank all jobs by vector similarity to profile (fast, free)
  5. Deep analysis on top 3 via 3-agent panel (expensive, rich)
  6. Research top 3 jobs
  7. Write all entries to Notion
  8. Send Discord report
  9. Log run outcome
"""

import json
import os
import sys
import traceback
from datetime import datetime
from dotenv import load_dotenv

# Load .env before any script imports
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)

# Ensure scripts dir is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))

from apify_scraper import scrape_jobs, load_config, load_strategy
from deduplicator import filter_jobs, load_seen_jobs, save_seen_jobs
from embedder import (
    load_or_create_profile_embedding,
    score_jobs_by_similarity,
    select_top_jobs,
    get_model,
)
from level_filter import filter_by_level
from reranker import rerank_jobs
from scorer import score_all_jobs, get_cost_stats as scorer_cost
from researcher import research_all_jobs, get_cost_stats as researcher_cost
from notion_writer import write_all_jobs
from discord_notify import (
    send_daily_report,
    send_no_new_jobs,
    send_error_alert
)
from feedback import run_feedback


def log_run(status, details, logs_dir="logs"):
    os.makedirs(logs_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "status": status,
        **details
    }
    log_path = os.path.join(logs_dir, f"run_{timestamp}.json")
    with open(log_path, "w") as f:
        json.dump(log_entry, f, indent=2)
    print(f"[run_daily_scout] Run logged: {log_path}")
    return log_entry


def run():
    print(f"\n{'='*60}")
    print(f"JOB SCOUT — Daily Run — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    try:
        # ── Step 0: Feedback loop — learn from Notion decisions ──
        print("[0/8] Checking feedback from Notion...")
        try:
            feedback_insights = run_feedback(strategy_path="strategy.json")
        except Exception as e:
            print(f"      Feedback check failed (non-fatal): {e}")
            feedback_insights = {}

        # ── Step 1: Load config and strategy ─────────────────
        print("\n[1/8] Loading configuration...")
        config = load_config("config.json")
        strategy = load_strategy("strategy.json")
        threshold = strategy["search_configuration"].get("fit_score_threshold", 30)
        deep_count = strategy["search_configuration"].get("deep_analysis_count", 3)
        print(f"      Threshold: {threshold}/100 | Deep analysis: top {deep_count}")

        # ── Step 2: Load profile embedding ─────────────────
        print("\n[2/8] Loading profile embedding...")
        profile_embedding, profile_text = load_or_create_profile_embedding(
            private_docs_dir="private_docs",
            strategy_path="strategy.json",
            embeddings_dir="embeddings",
        )

        # ── Step 3: Scrape jobs via Apify ────────────────────
        print(f"\n[3/8] Scraping jobs via Apify ({len(strategy['search_configuration']['search_terms'])} search terms)...")
        scrape_result = scrape_jobs(config, strategy)

        if scrape_result["status"] == "no_results":
            print("      No results returned from Apify.")
            send_no_new_jobs("config.json")
            log_run("no_results", {"scraped": 0, "qualified": 0})
            return

        raw_jobs = scrape_result["jobs"]
        print(f"      Scraped {len(raw_jobs)} raw jobs.")

        # ── Step 4: Deduplicate and filter ───────────────────
        print("\n[4/8] Deduplicating and filtering...")
        filter_result = filter_jobs(raw_jobs, "seen_jobs.json", "strategy.json")

        if filter_result["status"] == "all_duplicate":
            print("      All results are duplicates.")
            send_no_new_jobs("config.json")
            log_run("all_duplicate", {
                "scraped": len(raw_jobs),
                "qualified": 0,
                "filter_stats": filter_result["stats"]
            })
            return

        eligible_jobs = filter_result["jobs"]
        print(f"      {len(eligible_jobs)} eligible jobs after filtering.")

        if not eligible_jobs:
            print("      No eligible jobs after filtering.")
            send_no_new_jobs("config.json")
            log_run("no_eligible", {
                "scraped": len(raw_jobs),
                "qualified": 0,
                "filter_stats": filter_result["stats"]
            })
            return

        # ── Step 5: Vector similarity scoring (all jobs) ─────
        print(f"\n[5/8] Embedding & scoring {len(eligible_jobs)} jobs by vector similarity...")
        embed_model = get_model()
        scored_jobs = score_jobs_by_similarity(eligible_jobs, profile_embedding, embed_model)

        # ── Step 5b: Level alignment filter (top candidates only) ──
        print(f"\n[5b/8] Checking seniority alignment on top candidates...")
        level_filtered, level_stats = filter_by_level(
            scored_jobs, strategy_path="strategy.json", max_to_check=15
        )

        # ── Step 5c: Cross-encoder reranking ──────────────────
        print(f"\n[5c/8] Cross-encoder reranking top candidates...")
        reranked_jobs = rerank_jobs(
            level_filtered, strategy_path="strategy.json", top_n=20
        )

        # After reranking, take the top N by rerank order for deep analysis
        # (don't filter by embedding threshold — the reranker already judged relevance)
        top_jobs = reranked_jobs[:deep_count]
        remaining = reranked_jobs[deep_count:]

        print(f"      Top {deep_count} for deep analysis (by composite score):")
        for i, job in enumerate(top_jobs, 1):
            composite = job.get("composite_score", 0)
            title_m = job.get("title_match_score", 0)
            print(f"        {i}. composite:{composite:5.1f} embed:{job['fit_score']:5.1f} title:{title_m:3.0f} | {job['title']} @ {job['company']}")

        # ── Step 6: Deep analysis on top 5 (3-agent panel) ───
        genuine_top = top_jobs
        if genuine_top:
            print(f"\n[6/8] Running 3-agent deep analysis on top {len(genuine_top)} jobs...")
            deep_results = score_all_jobs(
                [j["job_data"] for j in genuine_top],
                strategy_path="strategy.json",
                prompts_dir="prompts"
            )
            # Merge deep analysis into the top jobs — including the score
            for deep_job in deep_results.get("all_scored", []):
                for top_job in top_jobs:
                    if top_job.get("url") == deep_job.get("url"):
                        top_job["embedding_score"] = top_job.get("fit_score")  # Preserve original
                        deep_score = deep_job.get("fit_score", top_job["fit_score"])
                        top_job["fit_score"] = deep_score
                        # Classification based on final score, not Agent 2's partial view
                        if deep_score >= 70:
                            top_job["classification"] = "Safe"
                        elif deep_score >= 45:
                            top_job["classification"] = "Stretch"
                        else:
                            top_job["classification"] = "Reach"
                        top_job["agent_scores"] = deep_job.get("agent_scores", {})
                        top_job["strongest_area"] = deep_job.get("strongest_area", "")
                        top_job["weakest_area"] = deep_job.get("weakest_area", "")
                        top_job["strongest_strategy"] = deep_job.get("strongest_strategy", "")
                        top_job["weakest_strategy"] = deep_job.get("weakest_strategy", "")
                        top_job["deep_analysis"] = True
                        break
        else:
            print("\n[6/8] No qualifying jobs for deep analysis.")

        # ── Step 7: Research top 3 jobs ──────────────────────
        research_jobs = [j for j in top_jobs if j.get("deep_analysis")]
        print(f"\n[7/8] Researching {len(research_jobs)} jobs...")
        # Research needs job_data format
        research_input = [j.get("job_data", j) for j in research_jobs]
        research_map = research_all_jobs(research_input, strategy_path="strategy.json")
        print(f"      Research complete for {len(research_map)} jobs.")

        # ── Step 8: Write to Notion ──────────────────────────
        print("\n[8/8] Writing to Notion & sending Discord report...")

        # Build scoring_result in the format notion_writer and discord_notify expect
        scoring_result = {
            "status": "ok" if any(j.get("deep_analysis") for j in top_jobs) else "no_qualifying",
            "threshold": threshold,
            "top_3": top_jobs,
            "near_relevant": [],  # Don't write noise to Notion
            "all_scored": scored_jobs,
        }

        notion_pages = write_all_jobs(scoring_result, research_map, config_path="config.json")
        print(f"      {len(notion_pages)} pages created in Notion.")

        # Send Discord report
        send_daily_report(scoring_result, notion_pages, config_path="config.json")

        # ── Commit seen jobs now that pipeline succeeded ─────
        newly_seen = filter_result.get("newly_seen", [])
        if newly_seen:
            existing_seen = load_seen_jobs("seen_jobs.json")
            existing_seen.extend(newly_seen)
            save_seen_jobs(existing_seen, "seen_jobs.json")
            print(f"      Committed {len(newly_seen)} jobs to seen_jobs.json")

        # ── Log success ──────────────────────────────────────
        log_entry = log_run("success", {
            "scraped": len(raw_jobs),
            "eligible": len(eligible_jobs),
            "scored_by_embedding": len(scored_jobs),
            "deep_analysed": len([j for j in top_jobs if j.get("deep_analysis")]),
            "qualifying": len([j for j in top_jobs if not j.get("near_relevant_fill")]),
            "top_jobs": [f"{j['title']} @ {j['company']} (score:{j['fit_score']})" for j in top_jobs],
            "notion_pages_created": len(notion_pages),
            "filter_stats": filter_result["stats"],
            "level_filter_stats": level_stats,
            "cost": {
                "scorer": scorer_cost(),
                "researcher": researcher_cost(),
            }
        })

        # Cost summary
        sc = scorer_cost()
        rc = researcher_cost()
        total_cost = sc["total_cost_usd"] + rc["total_cost_usd"]
        total_calls = sc["calls"] + rc["calls"]
        total_tokens = sc["total_tokens"] + rc["total_tokens"]

        print(f"\n{'='*60}")
        print(f"✅ Run complete — {len(scored_jobs)} scored, {len(top_jobs)} surfaced, {len(notion_pages)} Notion entries.")
        print(f"💰 Cost: ${total_cost:.3f} USD | {total_calls} API calls | {total_tokens:,} tokens")
        print(f"{'='*60}\n")

    except Exception as e:
        error_msg = f"Unexpected error during daily run:\n{traceback.format_exc()}"
        print(f"\n❌ ERROR: {error_msg}", file=sys.stderr)

        try:
            send_error_alert(str(e), config_path="config.json")
        except Exception:
            pass

        log_run("error", {"error": str(e), "traceback": traceback.format_exc()})
        sys.exit(1)


if __name__ == "__main__":
    run()
