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

Modes:
  python run_daily_scout.py              # full daily run
  python run_daily_scout.py --validate   # preflight only (env, config, strategy, CV) — no external calls
  python run_daily_scout.py --dry-run    # full pipeline minus Notion writes, Discord sends, seen-jobs commit
"""

import argparse
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
from greenhouse_scraper import scrape_greenhouse_jobs
from application_writer import generate_all_application_answers, get_cost_stats as app_writer_cost
from cv_tailor import generate_all_cvs, get_cost_stats as cv_tailor_cost
from notion_writer import write_all_jobs
from discord_notify import (
    send_daily_report,
    send_no_new_jobs,
    send_error_alert
)
from feedback import run_feedback


REQUIRED_ENV_VARS = [
    "OPENROUTER_API_KEY",
    "APIFY_API_TOKEN",
    "NOTION_TOKEN",
    "NOTION_DATABASE_ID",
    "DISCORD_WEBHOOK_URL",
]


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


def validate_setup():
    """
    Preflight check: no API calls, no scraping, no writes.
    Verifies env vars, config/strategy files, private_docs, and profile embedding build.
    Returns True if everything is in order, False otherwise.
    """
    print(f"\n{'='*60}")
    print("JOB SCOUT — Preflight validation")
    print(f"{'='*60}\n")

    ok = True

    print("[1/5] Checking required environment variables...")
    missing = [k for k in REQUIRED_ENV_VARS if not os.environ.get(k)]
    if missing:
        print(f"      ❌ Missing: {', '.join(missing)}")
        print(f"         Copy .env.example to .env and fill these in.")
        ok = False
    else:
        print(f"      ✅ All {len(REQUIRED_ENV_VARS)} env vars present.")

    print("\n[2/5] Checking config.json...")
    if not os.path.exists("config.json"):
        print("      ❌ config.json not found. Copy config.example.json to config.json.")
        ok = False
    else:
        try:
            with open("config.json") as f:
                json.load(f)
            print("      ✅ config.json is valid JSON.")
        except json.JSONDecodeError as e:
            print(f"      ❌ config.json is invalid JSON: {e}")
            ok = False

    print("\n[3/5] Checking strategy.json...")
    if not os.path.exists("strategy.json"):
        print("      ❌ strategy.json not found. Copy strategy.example.json to strategy.json.")
        ok = False
    else:
        try:
            with open("strategy.json") as f:
                strategy = json.load(f)
            search_terms = strategy.get("search_configuration", {}).get("search_terms", [])
            if not search_terms:
                print("      ⚠️  strategy.json has no search_configuration.search_terms. Pipeline will scrape nothing.")
                ok = False
            elif not strategy.get("positioning_statement"):
                print("      ⚠️  strategy.json is missing positioning_statement — scoring will be weak.")
            else:
                print(f"      ✅ strategy.json valid ({len(search_terms)} search terms).")
        except json.JSONDecodeError as e:
            print(f"      ❌ strategy.json is invalid JSON: {e}")
            ok = False

    print("\n[4/5] Checking private_docs/ for CV...")
    pd = "private_docs"
    if not os.path.isdir(pd):
        print(f"      ❌ {pd}/ not found. Create it and drop your resume PDF in.")
        ok = False
    else:
        pdfs = [f for f in os.listdir(pd) if f.lower().endswith(".pdf")]
        cv_pdfs = [f for f in pdfs if "resume" in f.lower() or "cv" in f.lower()]
        if not cv_pdfs:
            print(f"      ❌ No CV/resume PDF in {pd}/ (filename must contain 'resume' or 'cv').")
            ok = False
        else:
            print(f"      ✅ Found CV: {cv_pdfs[0]}")

    if not ok:
        print("\n❌ Validation failed. Fix the issues above before running the pipeline.\n")
        return False

    print("\n[5/5] Building profile embedding (this may download the model on first run)...")
    try:
        from embedder import load_or_create_profile_embedding
        embedding, profile_text = load_or_create_profile_embedding(
            private_docs_dir="private_docs",
            strategy_path="strategy.json",
            embeddings_dir="embeddings",
        )
        print(f"      ✅ Profile embedding built ({len(profile_text)} chars, {len(embedding)} dims).")
    except Exception as e:
        print(f"      ❌ Profile embedding failed: {e}")
        return False

    print(f"\n{'='*60}")
    print("✅ Preflight validation passed. Ready for a real run.")
    print(f"{'='*60}\n")
    return True


def run(dry_run=False):
    mode_label = "DRY RUN" if dry_run else "Daily Run"
    print(f"\n{'='*60}")
    print(f"JOB SCOUT — {mode_label} — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if dry_run:
        print("(dry-run: Notion writes, Discord sends, seen-jobs commits all skipped)")
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
            if not dry_run:
                send_no_new_jobs("config.json")
            else:
                print("      [dry-run] Would send 'no new jobs' Discord message.")
            log_run("no_results", {"scraped": 0, "qualified": 0})
            return

        raw_jobs = scrape_result["jobs"]
        print(f"      Scraped {len(raw_jobs)} raw jobs from LinkedIn.")

        # ── Step 3b: Greenhouse scraping (supplementary) ───
        try:
            if config.get("greenhouse", {}).get("enabled", False):
                print(f"\n[3b/8] Scraping Greenhouse boards...")
                gh_result = scrape_greenhouse_jobs(config, strategy)
                if gh_result["status"] == "ok" and gh_result["jobs"]:
                    raw_jobs.extend(gh_result["jobs"])
                    print(f"      Added {len(gh_result['jobs'])} jobs from Greenhouse. Total: {len(raw_jobs)}.")
        except Exception as e:
            print(f"      Greenhouse scraping failed (non-fatal): {e}")

        # ── Step 4: Deduplicate and filter ───────────────────
        print("\n[4/8] Deduplicating and filtering...")
        filter_result = filter_jobs(raw_jobs, "seen_jobs.json", "strategy.json")

        if filter_result["status"] == "all_duplicate":
            print("      All results are duplicates.")
            if not dry_run:
                send_no_new_jobs("config.json")
            else:
                print("      [dry-run] Would send 'no new jobs' Discord message.")
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
            if not dry_run:
                send_no_new_jobs("config.json")
            else:
                print("      [dry-run] Would send 'no new jobs' Discord message.")
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

        # ── Step 7b: Application materials (70+ only) ──────
        application_answers = {}
        tailored_cvs = {}
        qualifying_for_materials = [j for j in top_jobs if j.get("deep_analysis") and j.get("fit_score", 0) >= 70]
        if qualifying_for_materials:
            print(f"\n[7b/8] Generating application materials for {len(qualifying_for_materials)} jobs (score >= 70)...")
            try:
                application_answers = generate_all_application_answers(
                    qualifying_for_materials, research_map, strategy_path="strategy.json"
                )
            except Exception as e:
                print(f"      Application answers failed (non-fatal): {e}")
            try:
                tailored_cvs = generate_all_cvs(
                    qualifying_for_materials, research_map, strategy_path="strategy.json"
                )
            except Exception as e:
                print(f"      CV generation failed (non-fatal): {e}")
            print(f"      Generated {len(application_answers)} answer sets, {len(tailored_cvs)} tailored CVs.")
        else:
            print("\n[7b/8] No jobs scoring 70+ — skipping application materials.")

        # ── Step 8: Write to Notion ──────────────────────────
        if dry_run:
            print("\n[8/8] [dry-run] Skipping Notion writes, Discord report, and seen-jobs commit.")
        else:
            print("\n[8/8] Writing to Notion & sending Discord report...")

        # Build scoring_result in the format notion_writer and discord_notify expect
        scoring_result = {
            "status": "ok" if any(j.get("deep_analysis") for j in top_jobs) else "no_qualifying",
            "threshold": threshold,
            "top_3": top_jobs,
            "near_relevant": [],  # Don't write noise to Notion
            "all_scored": scored_jobs,
        }

        if not dry_run:
            notion_pages = write_all_jobs(
                scoring_result, research_map,
                application_answers_map=application_answers,
                tailored_cvs_map=tailored_cvs,
                config_path="config.json"
            )
            print(f"      {len(notion_pages)} pages created in Notion.")

            # Send Discord report
            send_daily_report(scoring_result, notion_pages, application_answers, config_path="config.json")
        else:
            notion_pages = []
            print(f"      [dry-run] Would create {len(top_jobs)} Notion pages and send Discord report.")
            print(f"      [dry-run] Top jobs:")
            for i, job in enumerate(top_jobs, 1):
                print(f"        {i}. {job.get('fit_score', 0):.0f}/100 — {job.get('title', '')} @ {job.get('company', '')}")

        # ── Commit seen jobs now that pipeline succeeded ─────
        newly_seen = filter_result.get("newly_seen", [])
        if newly_seen and not dry_run:
            existing_seen = load_seen_jobs("seen_jobs.json")
            existing_seen.extend(newly_seen)
            save_seen_jobs(existing_seen, "seen_jobs.json")
            print(f"      Committed {len(newly_seen)} jobs to seen_jobs.json")
        elif newly_seen and dry_run:
            print(f"      [dry-run] Would commit {len(newly_seen)} jobs to seen_jobs.json")

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
                "application_writer": app_writer_cost(),
                "cv_tailor": cv_tailor_cost(),
            },
            "materials_generated": len(application_answers),
        })

        # Cost summary
        sc = scorer_cost()
        rc = researcher_cost()
        ac = app_writer_cost()
        cc = cv_tailor_cost()
        total_cost = sc["total_cost_usd"] + rc["total_cost_usd"] + ac["total_cost_usd"] + cc["total_cost_usd"]
        total_calls = sc["calls"] + rc["calls"] + ac["calls"] + cc["calls"]
        total_tokens = sc["total_tokens"] + rc["total_tokens"] + ac["total_tokens"] + cc["total_tokens"]

        print(f"\n{'='*60}")
        entry_suffix = f", {len(notion_pages)} Notion entries" if not dry_run else " (dry-run: no Notion writes)"
        print(f"✅ Run complete — {len(scored_jobs)} scored, {len(top_jobs)} surfaced{entry_suffix}.")
        print(f"💰 Cost: ${total_cost:.3f} USD | {total_calls} API calls | {total_tokens:,} tokens")
        print(f"{'='*60}\n")

    except Exception as e:
        error_msg = f"Unexpected error during daily run:\n{traceback.format_exc()}"
        print(f"\n❌ ERROR: {error_msg}", file=sys.stderr)

        if not dry_run:
            try:
                send_error_alert(str(e), config_path="config.json")
            except Exception:
                pass

        log_run("error", {"error": str(e), "traceback": traceback.format_exc()})
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Job Scout daily pipeline.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate",
        action="store_true",
        help="Preflight only — check env vars, config, strategy, CV, and profile embedding. No external calls.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full pipeline but skip Notion writes, Discord sends, and seen-jobs commit.",
    )
    args = parser.parse_args()

    if args.validate:
        ok = validate_setup()
        sys.exit(0 if ok else 1)

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
