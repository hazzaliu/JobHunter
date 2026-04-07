"""
reranker.py
Cross-encoder reranking + composite scoring stage using FlashRank.

Sits between embedding retrieval and LLM deep analysis.
The bi-encoder (embedder.py) retrieves candidates by keyword similarity;
this cross-encoder re-scores them with full cross-attention between
the candidate profile and each job description, catching cases where
keyword overlap is high but actual career fit is low.

The composite score combines:
  - Cross-encoder rerank score (40%) — career-fit relevance
  - Embedding similarity score (30%) — semantic overlap
  - Title match boost (30%) — does the job title match target roles?
"""

import json
import os
import re
import sys

from flashrank import Ranker, RerankRequest

MODEL_NAME = "ms-marco-MiniLM-L-12-v2"


def get_ranker(cache_dir=None):
    """Load the cross-encoder reranker model."""
    if cache_dir is None:
        cache_dir = os.path.join(os.path.dirname(__file__), "..", "embeddings")
    return Ranker(model_name=MODEL_NAME, cache_dir=cache_dir)


def build_query(strategy_path="strategy.json"):
    """
    Build a concise query string from the candidate's strategy.
    Focused on role identity and career goals, not full CV text.
    """
    with open(strategy_path, "r") as f:
        strategy = json.load(f)

    target_titles = ", ".join(strategy.get("target_titles", []))
    target_functions = ", ".join(strategy.get("target_functions", []))
    target_industries = ", ".join(strategy.get("target_industries", []))
    positioning = strategy.get("positioning_statement", "")

    query = (
        f"I am a {target_titles} candidate. "
        f"{positioning} "
        f"Target functions: {target_functions}. "
        f"Target industries: {target_industries}."
    )
    return query


def _load_target_titles(strategy_path="strategy.json"):
    """Load target titles for title-match scoring."""
    with open(strategy_path, "r") as f:
        strategy = json.load(f)
    return [t.lower() for t in strategy.get("target_titles", [])]


def _title_match_score(job_title, target_titles):
    """
    Score how well a job title matches target titles. Returns 0-100.

    100 = exact target title match (e.g. "Senior Product Manager")
     75 = partial title match (e.g. "Product Manager" when target is "Senior Product Manager")
     50 = role-family match (e.g. "Product Analyst" — same family, different level)
     25 = adjacent role (e.g. "Business Analyst", "Data Scientist")
      0 = no match (e.g. "Java Developer", "HR Manager")
    """
    title_lower = job_title.lower()

    # Exact match against any target title
    for target in target_titles:
        # Strip parenthetical notes like "(stretch)"
        clean_target = re.sub(r'\(.*?\)', '', target).strip()
        if clean_target in title_lower or title_lower in clean_target:
            return 100

    # Core role keywords for partial matching
    pm_keywords = ["product manager", "product owner", "product lead"]
    data_keywords = ["data scientist", "data analyst", "analytics manager", "insights manager"]
    adjacent_keywords = ["business analyst", "delivery manager", "program manager", "strategy manager"]

    # Partial PM match
    if any(kw in title_lower for kw in pm_keywords):
        return 75

    # Data role match
    if any(kw in title_lower for kw in data_keywords):
        return 50

    # Adjacent role match
    if any(kw in title_lower for kw in adjacent_keywords):
        return 25

    # Check for any "product" or "data" in title
    if "product" in title_lower or "data" in title_lower or "analytics" in title_lower:
        return 25

    return 0


def _rescale_rerank(score, scores_list):
    """
    Rescale a rerank score relative to the min/max in this batch.
    Maps the compressed 0.92-0.97 range to a usable 0-100 scale.
    """
    if not scores_list or len(scores_list) < 2:
        return 50.0
    mn, mx = min(scores_list), max(scores_list)
    if mx == mn:
        return 50.0
    return round(((score - mn) / (mx - mn)) * 100, 1)


def build_passage(job):
    """Build a passage dict from a job for FlashRank."""
    text_parts = [
        job.get("title", ""),
        f"at {job.get('company', '')}",
        job.get("description", "")[:1500],
    ]
    return {
        "id": job.get("job_id") or job.get("id") or job.get("url", ""),
        "text": " ".join(filter(None, text_parts)),
        "meta": job,
    }


def rerank_jobs(scored_jobs, strategy_path="strategy.json", top_n=20):
    """
    Rerank embedding-scored jobs using cross-encoder + composite scoring.

    Composite score = 40% rescaled rerank + 30% embedding + 30% title match.
    This provides better discrimination than any single signal alone.
    """
    if not scored_jobs:
        return scored_jobs

    ranker = get_ranker()
    query = build_query(strategy_path)
    target_titles = _load_target_titles(strategy_path)

    # Only rerank the top N from embedding retrieval
    to_rerank = scored_jobs[:top_n]
    remainder = scored_jobs[top_n:]

    passages = [build_passage(job) for job in to_rerank]
    request = RerankRequest(query=query, passages=passages)
    results = ranker.rerank(request)

    # Map reranked results back to original job dicts
    id_to_job = {}
    for job in to_rerank:
        job_id = job.get("job_id") or job.get("id") or job.get("url", "")
        id_to_job[job_id] = job

    # Collect raw rerank scores for rescaling
    raw_rerank_scores = [float(r["score"]) for r in results]

    reranked = []
    for result in results:
        job_id = result["id"]
        job = id_to_job.get(job_id)
        if not job:
            continue

        raw_rerank = float(result["score"])
        rescaled_rerank = _rescale_rerank(raw_rerank, raw_rerank_scores)
        embed_score = job.get("fit_score", 0)
        title_score = _title_match_score(job.get("title", ""), target_titles)

        # Composite: 40% rerank + 30% embedding + 30% title match
        composite = round(
            0.4 * rescaled_rerank + 0.3 * embed_score + 0.3 * title_score, 1
        )

        job["rerank_score"] = raw_rerank
        job["rerank_rescaled"] = rescaled_rerank
        job["title_match_score"] = title_score
        job["composite_score"] = composite
        reranked.append(job)

    # Sort by composite score descending
    reranked.sort(key=lambda x: x["composite_score"], reverse=True)

    # Log top results
    for i, job in enumerate(reranked[:5], 1):
        print(
            f"[reranker] #{i}: {job.get('title', '')} @ {job.get('company', '')} "
            f"(composite: {job['composite_score']}, embed: {job.get('fit_score', 0)}, "
            f"title: {job['title_match_score']}, rerank: {job['rerank_rescaled']})"
        )

    if reranked:
        print(
            f"[reranker] Reranked {len(to_rerank)} jobs | "
            f"Top composite: {reranked[0]['composite_score']} | "
            f"Spread: {reranked[0]['composite_score'] - reranked[-1]['composite_score']:.1f}"
        )

    # Append remainder (not reranked, lower embedding scores)
    for job in remainder:
        job["composite_score"] = job.get("fit_score", 0) * 0.3  # Only embedding component
    reranked.extend(remainder)
    return reranked


if __name__ == "__main__":
    test_jobs = [
        {
            "job_id": "1", "title": "Senior Product Manager - AI", "company": "Canva",
            "description": "Lead AI product strategy. 3-5 years PM experience. Agile, data products, LLMs.",
            "fit_score": 85.0,
        },
        {
            "job_id": "2", "title": "Senior Retail Data Analyst", "company": "Woolworths",
            "description": "Analyse POS data, stock levels, promotions. 5+ years retail analytics. SQL, Tableau.",
            "fit_score": 90.0,
        },
        {
            "job_id": "3", "title": "Java Developer", "company": "ANZ Bank",
            "description": "Backend microservices. 8+ years Java, Spring Boot, Kubernetes.",
            "fit_score": 40.0,
        },
        {
            "job_id": "4", "title": "Product Manager", "company": "Findex",
            "description": "Own product roadmap for professional services platform. Digital transformation.",
            "fit_score": 70.0,
        },
        {
            "job_id": "5", "title": "Business Development Manager (Hindi)", "company": "PrimeXBT",
            "description": "Crypto exchange, Hindi-speaking markets, sales targets, B2C.",
            "fit_score": 30.0,
        },
    ]
    reranked = rerank_jobs(test_jobs, strategy_path="strategy.json")
    print("\nFinal ranking:")
    for i, job in enumerate(reranked, 1):
        print(
            f"  {i}. {job['title']} @ {job['company']} "
            f"(composite: {job['composite_score']}, embed: {job.get('fit_score', 0)}, "
            f"title: {job.get('title_match_score', 0)})"
        )
