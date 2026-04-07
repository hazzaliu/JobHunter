"""
level_filter.py
Fast LLM-based seniority/level alignment check.

After vector similarity scoring, this filter asks one focused question per job:
"Is this role at the right level for the candidate?"

Filters out roles that are clearly too senior (Director+, VP, Head of, C-suite)
or too junior (grad, intern, entry-level) relative to the candidate's experience.
"""

import json
import os
import re
import sys
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)


def get_client():
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )


def get_model():
    return os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")


def check_level_alignment(client, job, candidate_summary):
    """
    Ask the LLM one focused question: is this job at the right level?
    Returns: {"aligned": bool, "reason": str, "level": "too_senior"|"right_level"|"too_junior"}
    """
    job_text = f"{job.get('title', '')} at {job.get('company', '')}"
    description = job.get("description", "")[:1500]

    # Experience years from strategy (default 4)
    years_exp = strategy.get("search_configuration", {}).get("years_experience", 4)
    min_years = max(1, years_exp - 2)
    max_years = years_exp + 2

    prompt = f"""You are a seniority-level checker. Your ONLY job is to assess whether this role's SENIORITY LEVEL matches someone with ~{years_exp} years of professional experience.

DO NOT judge whether the role is a good fit, relevant to the candidate's skills, or in the right field. Only judge seniority/experience level.

CANDIDATE: ~{years_exp} years professional experience. Target seniority: mid-level to senior individual contributor.

JOB:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Description: {description}

RULES:
- "right_level" = roles requiring {min_years}-{max_years} years experience, mid-level to senior individual contributor or early people manager. Mark aligned=true.
- "too_senior" = roles requiring {max_years + 2}+ years, Director/VP/Head of/C-suite, or managing large teams (20+). Mark aligned=false.
- "too_junior" = entry-level, graduate, intern, or roles requiring 0-1 years experience. Mark aligned=false.

IMPORTANT: If the seniority level is right ({min_years}-{max_years} years), ALWAYS return aligned=true, even if the role is in a completely different field.

Return ONLY a JSON object:
{{"aligned": true/false, "level": "right_level"|"too_senior"|"too_junior", "reason": "one sentence about seniority only"}}"""

    try:
        response = client.chat.completions.create(
            model=get_model(),
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences
        raw = re.sub(r'^```(?:json)?\s*\n?', '', raw)
        raw = re.sub(r'\n?```\s*$', '', raw)
        result = json.loads(raw)
        return result
    except Exception as e:
        # On error, default to allowing the job through
        print(f"[level_filter] Error checking {job_text}: {e}", file=sys.stderr)
        return {"aligned": True, "level": "right_level", "reason": "Check failed, defaulting to pass"}


def filter_by_level(scored_jobs, strategy_path="strategy.json", max_to_check=15):
    """
    Check level alignment for the top N scored jobs.
    Returns filtered list with level info attached, and stats.
    """
    # Build a concise candidate summary from strategy
    with open(strategy_path, "r") as f:
        strategy = json.load(f)

    # Build candidate summary dynamically from strategy
    target_titles = ", ".join(strategy.get("target_titles", []))
    positioning = strategy.get("positioning_statement", "")[:200]

    # Extract experience hints from selling points
    experience_hints = []
    for sp in strategy.get("selling_points", [])[:3]:
        experience_hints.append(f"- {sp.get('name', '')}: {sp.get('metric', '')}")
    experience_text = "\n".join(experience_hints) if experience_hints else "See profile for details."

    candidate_summary = (
        f"Target level: {target_titles}\n"
        f"Positioning: {positioning}\n"
        f"Key experience:\n{experience_text}"
    )

    client = get_client()
    jobs_to_check = scored_jobs[:max_to_check]
    remaining = scored_jobs[max_to_check:]

    aligned_jobs = []
    filtered_count = 0

    for job in jobs_to_check:
        job_data = job.get("job_data", job)
        result = check_level_alignment(client, job_data, candidate_summary)

        job["level_check"] = result

        level = result.get("level", "right_level")
        # Only filter on actual seniority mismatch — right_level always passes
        if result.get("aligned", True) or level == "right_level":
            aligned_jobs.append(job)
        else:
            filtered_count += 1
            reason = result.get("reason", "")
            print(f"[level_filter] FILTERED ({level}): {job.get('title', '')} @ {job.get('company', '')} — {reason}")

    # Append unchecked jobs (lower similarity, not worth checking)
    aligned_jobs.extend(remaining)

    print(f"[level_filter] Checked {len(jobs_to_check)} jobs | "
          f"{filtered_count} filtered out | "
          f"{len(jobs_to_check) - filtered_count} passed")

    return aligned_jobs, {
        "checked": len(jobs_to_check),
        "filtered": filtered_count,
        "passed": len(jobs_to_check) - filtered_count,
    }


if __name__ == "__main__":
    # Quick test
    client = get_client()
    test_jobs = [
        {"title": "Head of AI", "company": "SAP", "description": "Lead entire AI division. 15+ years experience. VP level."},
        {"title": "Product Manager", "company": "Canva", "description": "3-5 years PM experience. Agile, data products."},
        {"title": "Junior Data Analyst", "company": "Startup", "description": "Entry level. 0-1 years. Excel, basic SQL."},
    ]
    summary = "Product Manager with 4 years experience in tech/data. Target: Senior PM, AI PM, Data PM roles."
    for job in test_jobs:
        result = check_level_alignment(client, job, summary)
        print(f"{job['title']:30s} → aligned={result['aligned']}, level={result['level']}, reason={result['reason']}")
