"""
scorer.py
Runs the 3-agent scoring panel for each job and returns fit scores with rationale.
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)


def get_client():
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )


def get_model():
    return os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4-20250514")


def load_prompt(name, prompts_dir="prompts"):
    path = os.path.join(prompts_dir, f"{name}.txt")
    with open(path, "r") as f:
        return f.read()


def load_strategy(path="strategy.json"):
    with open(path, "r") as f:
        return json.load(f)


def build_job_context(job):
    return f"""
JOB TITLE: {job.get('title', '')}
COMPANY: {job.get('company', '')}
SENIORITY: {job.get('seniority', 'Not specified')}
LOCATION: {job.get('location', '')}
EMPLOYMENT TYPE: {job.get('employment_type', '')}

JOB DESCRIPTION:
{job.get('description', '')}

REQUIREMENTS:
{job.get('requirements', '')}
""".strip()


def build_candidate_context(strategy):
    selling_points = "\n".join(
        f"- {sp['name']}: {sp['metric']} — {sp['story']}"
        for sp in strategy.get("selling_points", [])
    )
    gaps = "\n".join(
        f"- {g['gap']}: {g['strategy']}"
        for g in strategy.get("known_gaps", [])
    )
    return f"""
CANDIDATE PROFILE

POSITIONING: {strategy.get('positioning_statement', '')}

SELLING POINTS:
{selling_points}

KNOWN GAPS AND MITIGATION STRATEGIES:
{gaps}

TARGET TITLES: {', '.join(strategy.get('target_titles', []))}
TARGET INDUSTRIES: {', '.join(strategy.get('target_industries', []))}
AVOID INDUSTRIES: {', '.join(strategy.get('avoid_industries', []))}
COMPANY PREFERENCE: {strategy['company_preferences'].get('type', '')}
SALARY FLOOR: ${strategy['salary'].get('floor_aud', 0):,} AUD (excl. super)
""".strip()


_cost_tracker = {"total_tokens": 0, "total_cost_usd": 0.0, "calls": 0}


def get_cost_stats():
    """Return accumulated cost stats for this session."""
    return dict(_cost_tracker)


def _track_usage(response):
    """Track token usage and estimated cost from an OpenRouter response."""
    usage = getattr(response, "usage", None)
    if usage:
        tokens = (getattr(usage, "total_tokens", 0) or 0)
        _cost_tracker["total_tokens"] += tokens
        # Claude Sonnet 4 via OpenRouter: ~$3/M input, ~$15/M output
        input_t = getattr(usage, "prompt_tokens", 0) or 0
        output_t = getattr(usage, "completion_tokens", 0) or 0
        cost = (input_t * 3.0 / 1_000_000) + (output_t * 15.0 / 1_000_000)
        _cost_tracker["total_cost_usd"] += cost
    _cost_tracker["calls"] += 1


def run_agent(client, system_prompt, job_context, candidate_context, retries=3):
    """Calls the LLM via OpenRouter with the agent system prompt and returns the raw response."""
    user_message = f"""
Here is the job to evaluate:

{job_context}

---

Here is the candidate profile:

{candidate_context}

Please provide your evaluation now.
"""
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=get_model(),
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ]
            )
            _track_usage(response)
            return response.choices[0].message.content
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"[scorer] API error (attempt {attempt + 1}/{retries}), retrying in {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
            else:
                raise


def parse_score_from_response(response_text, max_score):
    """Extracts the numerical score from an agent response."""
    import re
    # Look for patterns like "SCORE: 24", "Score: 24/33", "24 out of 33"
    patterns = [
        r"SCORE:\s*(\d+)",
        r"Score:\s*(\d+)",
        r"score:\s*(\d+)",
        r"(\d+)\s*/\s*" + str(max_score),
        r"(\d+)\s+out of\s+" + str(max_score),
    ]
    for pattern in patterns:
        match = re.search(pattern, response_text)
        if match:
            score = int(match.group(1))
            return min(score, max_score)  # Cap at max
    # Default to middle score if parsing fails
    return max_score // 2


def parse_classification(response_text):
    """Extracts Safe/Stretch/Reach classification from Agent 2 response."""
    import re
    # Look for the explicit CLASSIFICATION: line first
    match = re.search(r'CLASSIFICATION:\s*(Safe|Stretch|Reach)', response_text, re.IGNORECASE)
    if match:
        return match.group(1).capitalize()
    # Fallback: check near the top of the response (less likely to hit false positives)
    for line in response_text.split("\n")[:10]:
        line_lower = line.lower().strip()
        if "safe" in line_lower and ("classif" in line_lower or "label" in line_lower):
            return "Safe"
        if "stretch" in line_lower and ("classif" in line_lower or "label" in line_lower):
            return "Stretch"
        if "reach" in line_lower and ("classif" in line_lower or "label" in line_lower):
            return "Reach"
    return "Stretch"  # Default


def score_job(job, strategy, prompts_dir="prompts"):
    """
    Runs all 3 agents on a single job and returns the full scoring object.
    """
    client = get_client()

    job_context = build_job_context(job)
    candidate_context = build_candidate_context(strategy)

    # Load agent prompts
    prompt_a1 = load_prompt("agent1_seniority", prompts_dir)
    prompt_a2 = load_prompt("agent2_fit", prompts_dir)
    prompt_a3 = load_prompt("agent3_devils", prompts_dir)

    # Run all 3 agents in parallel
    print(f"[scorer] Scoring: {job.get('title')} @ {job.get('company')}")

    with ThreadPoolExecutor(max_workers=3) as executor:
        f1 = executor.submit(run_agent, client, prompt_a1, job_context, candidate_context)
        f2 = executor.submit(run_agent, client, prompt_a2, job_context, candidate_context)
        f3 = executor.submit(run_agent, client, prompt_a3, job_context, candidate_context)
        response_a1 = f1.result()
        response_a2 = f2.result()
        response_a3 = f3.result()

    # Parse scores
    score_a1 = parse_score_from_response(response_a1, 33)
    score_a2 = parse_score_from_response(response_a2, 33)
    score_a3 = parse_score_from_response(response_a3, 34)
    classification = parse_classification(response_a2)

    total_score = score_a1 + score_a2 + score_a3

    # Determine strongest and weakest
    scores = {
        "seniority_culture": score_a1,
        "fit_classifier": score_a2,
        "devils_advocate": score_a3
    }
    strongest_area = max(scores, key=scores.get)
    weakest_area = min(scores, key=scores.get)

    # Extract strategy/recommendation from agent response
    def extract_strategy(response, keyword):
        lines = response.split("\n")
        for i, line in enumerate(lines):
            if keyword.lower() in line.lower():
                # Check if content is on the same line after the keyword header
                after_colon = line.split(":", 1)[-1].strip() if ":" in line else ""
                if after_colon and len(after_colon) > 10:
                    return after_colon.lstrip("- ").strip()
                # Otherwise grab the next non-empty line(s)
                for j in range(i + 1, min(i + 5, len(lines))):
                    if lines[j].strip():
                        return lines[j].strip().lstrip("- ").strip()
        # Fallback: return last non-empty line
        for line in reversed(lines):
            if line.strip():
                return line.strip()
        return ""

    strongest_strategy = extract_strategy(
        response_a1 if strongest_area == "seniority_culture"
        else response_a2 if strongest_area == "fit_classifier"
        else response_a3,
        "strategy" if strongest_area != "devils_advocate" else "recommend"
    )

    weakest_strategy = extract_strategy(
        response_a1 if weakest_area == "seniority_culture"
        else response_a2 if weakest_area == "fit_classifier"
        else response_a3,
        "strategy" if weakest_area != "devils_advocate" else "recommend"
    )

    result = {
        "job_id": job.get("id") or job.get("url", ""),
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "url": job.get("url", ""),
        "fit_score": total_score,
        "classification": classification,
        "qualifies": total_score >= 60,
        "agent_scores": {
            "seniority_culture": {
                "score": score_a1,
                "max": 33,
                "rationale": response_a1
            },
            "fit_classifier": {
                "score": score_a2,
                "max": 33,
                "rationale": response_a2,
                "label": classification
            },
            "devils_advocate": {
                "score": score_a3,
                "max": 34,
                "rationale": response_a3
            }
        },
        "strongest_area": strongest_area,
        "weakest_area": weakest_area,
        "strongest_strategy": strongest_strategy,
        "weakest_strategy": weakest_strategy,
        "job_data": job
    }

    return result


def score_all_jobs(jobs, strategy_path="strategy.json", prompts_dir="prompts"):
    """
    Scores all eligible jobs, sorts by fit score, applies threshold gate.
    Returns top_3, near_relevant, and all_scored lists.
    """
    strategy = load_strategy(strategy_path)
    threshold = strategy["search_configuration"].get("fit_score_threshold", 60)

    all_scored = []
    for job in jobs:
        try:
            result = score_job(job, strategy, prompts_dir)
            all_scored.append(result)
        except Exception as e:
            print(f"[scorer] Error scoring {job.get('title')}: {e}", file=sys.stderr)
            continue

    # Sort by score descending
    all_scored.sort(key=lambda x: x["fit_score"], reverse=True)

    qualifying = [j for j in all_scored if j["fit_score"] >= threshold]
    near_relevant = [j for j in all_scored if j["fit_score"] < threshold]

    top_3 = qualifying[:3]

    # If fewer than 3 qualified, fill with top near-relevant, labelled clearly
    if len(top_3) < 3:
        fill_count = 3 - len(top_3)
        for job in near_relevant[:fill_count]:
            job["near_relevant_fill"] = True
        top_3.extend(near_relevant[:fill_count])

    status = "ok"
    if len(qualifying) == 0:
        status = "no_qualifying"
    elif len(qualifying) < 3:
        status = "partial_qualifying"

    print(f"[scorer] Scored {len(all_scored)} jobs | "
          f"{len(qualifying)} qualifying (≥{threshold}) | "
          f"{len(near_relevant)} near-relevant")

    return {
        "status": status,
        "threshold": threshold,
        "top_3": top_3,
        "near_relevant": near_relevant,
        "all_scored": all_scored
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            jobs = json.load(f)
        result = score_all_jobs(jobs)
        # Print without full rationale text for readability
        for job in result["top_3"]:
            print(f"{job['fit_score']:3d} | {job['classification']:7s} | {job['title']} @ {job['company']}")
