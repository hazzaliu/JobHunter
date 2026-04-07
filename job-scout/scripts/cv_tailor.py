"""
cv_tailor.py
Generates a tailored markdown CV for each top-scoring job (70+).

Uses Claude Sonnet for quality — CV text needs to be polished and precise.
One LLM call per job generates a complete markdown CV with reordered
bullets, adapted summary, and skills matched to the job's requirements.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

_cost_tracker = {"total_tokens": 0, "total_cost_usd": 0.0, "calls": 0}


def get_cost_stats():
    return dict(_cost_tracker)


def _get_client():
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )


def _get_model():
    return os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")


def _call_llm(client, prompt, max_tokens=2000, retries=3):
    """Call Sonnet via OpenRouter with retry and cost tracking."""
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=_get_model(),
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            usage = getattr(response, "usage", None)
            if usage:
                input_t = getattr(usage, "prompt_tokens", 0) or 0
                output_t = getattr(usage, "completion_tokens", 0) or 0
                _cost_tracker["total_tokens"] += input_t + output_t
                # Sonnet pricing: ~$3/M input, ~$15/M output
                _cost_tracker["total_cost_usd"] += (input_t * 3.0 / 1_000_000) + (output_t * 15.0 / 1_000_000)
            _cost_tracker["calls"] += 1
            return response.choices[0].message.content
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"[cv_tailor] API error (attempt {attempt + 1}/{retries}), retrying in {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
            else:
                raise


def _load_strategy(path="strategy.json"):
    with open(path, "r") as f:
        return json.load(f)


def generate_tailored_cv(scored_job, research, strategy):
    """
    Generate a tailored markdown CV for one job.
    Returns markdown string.
    """
    client = _get_client()

    job = scored_job.get("job_data", scored_job)
    title = job.get("title", "")
    company = job.get("company", "")
    description = job.get("description", "")[:2000]
    requirements = job.get("requirements", "")[:1000]

    # Strategy context
    positioning = strategy.get("positioning_statement", "")
    selling_points = "\n".join(
        f"- {sp['name']}: {sp['metric']} — {sp['story']} (Best for: {sp.get('when_to_use', '')})"
        for sp in strategy.get("selling_points", [])
    )
    known_gaps = "\n".join(
        f"- {g['gap']}: {g['strategy']}"
        for g in strategy.get("known_gaps", [])
    )
    differentiators = "\n".join(
        f"- {d}" for d in strategy.get("competitive_edge", {}).get("key_differentiators", [])
    )

    # Research context
    role_info = research.get("role", {}) if research else {}
    must_have = "\n".join(f"- {r}" for r in role_info.get("must_have", []))
    nice_to_have = "\n".join(f"- {r}" for r in role_info.get("nice_to_have", []))

    # Agent analysis context
    strongest = scored_job.get("strongest_strategy", "")
    weakest = scored_job.get("weakest_strategy", "")

    prompt = f"""You are tailoring a CV/resume for a candidate applying to {title} at {company}.

CANDIDATE POSITIONING:
{positioning}

KEY DIFFERENTIATORS:
{differentiators}

SELLING POINTS (with metrics and full stories):
{selling_points}

KNOWN GAPS (with mitigation strategies — de-emphasize these areas):
{known_gaps}

JOB DESCRIPTION (excerpt):
{description}

ROLE REQUIREMENTS:
Must-have:
{must_have}

Nice-to-have:
{nice_to_have}

ANALYSIS:
Lead with: {strongest}
Mitigate: {weakest}

INSTRUCTIONS:
Generate a complete, polished markdown CV with these sections:

1. **Name and Contact** — Use "[Candidate Name]" as placeholder. Add a one-line tagline tailored to this specific role.

2. **Professional Summary** — 3-4 sentences adapted from the positioning statement. Emphasize alignment with this role's must-have requirements. Reference 1-2 specific metrics from selling points.

3. **Key Achievements** — 4-5 bullet points, reordered so the most relevant to THIS role comes first. Each bullet: achievement name, metric, and one sentence of context. Adapt the framing to match job requirements.

4. **Professional Experience** — For each role derived from the selling points, write 2-3 bullets that emphasize aspects matching the job's must-have requirements. Lead with the most relevant bullets.

5. **Technical Skills** — List skills that directly match the job requirements first, then supporting skills. Group into categories (e.g., "Product & Strategy", "Technical", "Tools").

6. **Education** — List qualifications from the selling points and differentiators.

RULES:
- Do NOT invent experience, skills, or qualifications. Use ONLY what's in the selling points, differentiators, and positioning.
- Reorder and rephrase to emphasize relevance to THIS role, but keep all facts truthful.
- De-emphasize areas flagged in known gaps unless they're directly required.
- Lead every section with the strongest match to this role's must-have requirements.
- Use concise, action-oriented language. No fluff.
- Output clean markdown only. No commentary, no explanations outside the CV."""

    raw = _call_llm(client, prompt)
    # Strip any markdown code fences the LLM might wrap around the output
    cv = re.sub(r'^```(?:markdown)?\s*\n?', '', raw.strip())
    cv = re.sub(r'\n?```\s*$', '', cv)

    print(f"[cv_tailor] Generated CV for {title} @ {company} ({len(cv)} chars)")
    return cv


def generate_all_cvs(qualifying_jobs, research_map, strategy_path="strategy.json"):
    """
    Generate tailored CVs for all qualifying jobs (70+).
    Returns {url: markdown_string} map.
    """
    strategy = _load_strategy(strategy_path)
    cvs_map = {}

    os.makedirs("output_docs", exist_ok=True)

    for job in qualifying_jobs:
        job_url = job.get("url", job.get("job_data", {}).get("url", ""))
        research = research_map.get(job_url, {})

        try:
            cv_markdown = generate_tailored_cv(job, research, strategy)
            if cv_markdown:
                cvs_map[job_url] = cv_markdown

                # Save locally
                company_slug = job.get("company", "unknown").lower().replace(" ", "_")[:20]
                date_str = datetime.now().strftime("%Y-%m-%d")
                filepath = f"output_docs/cv_{company_slug}_{date_str}.md"
                with open(filepath, "w") as f:
                    f.write(cv_markdown)
                print(f"[cv_tailor] Saved: {filepath}")

        except Exception as e:
            print(f"[cv_tailor] Error for {job.get('title', '')}: {e}", file=sys.stderr)

    return cvs_map


if __name__ == "__main__":
    print("Usage: called from run_daily_scout.py pipeline")
