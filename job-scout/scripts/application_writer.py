"""
application_writer.py
Generates draft application answers for top-scoring jobs (70+).

Uses Claude Haiku for cost efficiency — short-form Q&A doesn't need
the full power of Sonnet. One LLM call per job generates all 5 answers.
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

HAIKU_MODEL = "anthropic/claude-haiku-4.5"

_cost_tracker = {"total_tokens": 0, "total_cost_usd": 0.0, "calls": 0}


def get_cost_stats():
    return dict(_cost_tracker)


def _get_client():
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )


def _call_llm(client, prompt, max_tokens=1200, retries=3):
    """Call Haiku via OpenRouter with retry and cost tracking."""
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=HAIKU_MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            usage = getattr(response, "usage", None)
            if usage:
                input_t = getattr(usage, "prompt_tokens", 0) or 0
                output_t = getattr(usage, "completion_tokens", 0) or 0
                _cost_tracker["total_tokens"] += input_t + output_t
                # Haiku pricing: ~$0.25/M input, ~$1.25/M output
                _cost_tracker["total_cost_usd"] += (input_t * 0.25 / 1_000_000) + (output_t * 1.25 / 1_000_000)
            _cost_tracker["calls"] += 1
            return response.choices[0].message.content
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"[application_writer] API error (attempt {attempt + 1}/{retries}), retrying in {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
            else:
                raise


def _parse_json_response(raw_text):
    """Parse JSON from LLM response, stripping markdown code fences."""
    text = raw_text.strip()
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return json.loads(text)


def _load_strategy(path="strategy.json"):
    with open(path, "r") as f:
        return json.load(f)


def generate_application_answers(scored_job, research, strategy):
    """
    Generate draft answers to 5 common application questions for one job.
    Returns dict with keys q1-q5.
    """
    client = _get_client()

    job = scored_job.get("job_data", scored_job)
    title = job.get("title", "")
    company = job.get("company", "")

    # Build context from strategy
    selling_points = "\n".join(
        f"- {sp['name']}: {sp['metric']} — {sp['story']}"
        for sp in strategy.get("selling_points", [])
    )
    known_gaps = "\n".join(
        f"- {g['gap']}: {g['strategy']}"
        for g in strategy.get("known_gaps", [])
    )
    positioning = strategy.get("positioning_statement", "")
    salary = strategy.get("salary", {})

    # Build context from research
    company_info = research.get("company", {}) if research else {}
    role_info = research.get("role", {}) if research else {}
    must_have = ", ".join(role_info.get("must_have", []))
    nice_to_have = ", ".join(role_info.get("nice_to_have", []))

    # Build context from agent analysis
    agent_scores = scored_job.get("agent_scores", {})
    strongest = scored_job.get("strongest_strategy", "")
    weakest = scored_job.get("weakest_strategy", "")

    prompt = f"""You are writing application answers for a candidate applying to {title} at {company}.

CANDIDATE POSITIONING:
{positioning}

SELLING POINTS:
{selling_points}

KNOWN GAPS (with mitigation strategies):
{known_gaps}

JOB REQUIREMENTS:
Must-have: {must_have}
Nice-to-have: {nice_to_have}

COMPANY CONTEXT:
Overview: {company_info.get('overview', 'N/A')}
Mission: {company_info.get('mission', 'N/A')}
Culture signals: {company_info.get('culture_signals', 'N/A')}

ANALYSIS SUMMARY:
Strongest angle: {strongest}
Weakest angle: {weakest}

SALARY CONTEXT:
Floor: ${salary.get('floor_aud', 0):,} AUD, Target: ${salary.get('target_aud', 0):,} AUD (excluding superannuation)

Generate answers to these 5 questions. Each answer should be 3-5 sentences, specific to THIS role, drawing on the candidate's real experience.

1. "Why are you interested in this role?"
2. "Why this company?"
3. "Describe your most relevant experience"
4. "What's your biggest weakness or area for growth?"
5. "What are your salary expectations?"

For Q4, pick the most relevant gap from the known gaps list and use its mitigation strategy.
For Q5, frame the salary range professionally — express flexibility while anchoring at the target.

Return a JSON object with keys "q1" through "q5", each containing the answer text.
Return ONLY valid JSON, no other text."""

    raw = _call_llm(client, prompt)

    try:
        answers = _parse_json_response(raw)
        print(f"[application_writer] Generated answers for {title} @ {company}")
        return answers
    except json.JSONDecodeError:
        print(f"[application_writer] Failed to parse answers for {title} @ {company}", file=sys.stderr)
        return {}


def generate_all_application_answers(qualifying_jobs, research_map, strategy_path="strategy.json"):
    """
    Generate application answers for all qualifying jobs (70+).
    Returns {url: {q1..q5}} map.
    """
    strategy = _load_strategy(strategy_path)
    answers_map = {}

    os.makedirs("output_docs", exist_ok=True)

    for job in qualifying_jobs:
        job_url = job.get("url", job.get("job_data", {}).get("url", ""))
        research = research_map.get(job_url, {})

        try:
            answers = generate_application_answers(job, research, strategy)
            if answers:
                answers_map[job_url] = answers

                # Save locally
                company_slug = job.get("company", "unknown").lower().replace(" ", "_")[:20]
                date_str = datetime.now().strftime("%Y-%m-%d")
                filepath = f"output_docs/answers_{company_slug}_{date_str}.json"
                with open(filepath, "w") as f:
                    json.dump({"job": f"{job.get('title', '')} @ {job.get('company', '')}", "answers": answers}, f, indent=2)

        except Exception as e:
            print(f"[application_writer] Error for {job.get('title', '')}: {e}", file=sys.stderr)

    return answers_map


if __name__ == "__main__":
    print("Usage: called from run_daily_scout.py pipeline")
