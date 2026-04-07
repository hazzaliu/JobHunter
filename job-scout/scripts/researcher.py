"""
researcher.py
Company and role research module. Fetches company websites and public LinkedIn profiles.
Uses an LLM via OpenRouter for synthesis.
"""

import json
import os
import sys
import re
import time
import requests
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


def load_strategy(path="strategy.json"):
    with open(path, "r") as f:
        return json.load(f)


def fetch_url(url, timeout=10):
    """Simple URL fetch with error handling."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; JobScoutBot/1.0)"}
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        # Basic HTML stripping
        text = response.text
        # Remove scripts and styles
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:8000]  # Limit to 8k chars for context
    except Exception as e:
        return f"[fetch error: {e}]"


def _web_search(query, max_results=3):
    """Search the web via DuckDuckGo. Free, no API key."""
    try:
        from ddgs import DDGS
        results = DDGS().text(query, max_results=max_results)
        return [r["href"] for r in results if r.get("href")]
    except Exception:
        return []


def search_company_website(company_name):
    """Find and fetch the company's website using search."""
    # Try search first
    urls = _web_search(f"{company_name} company about")
    for url in urls:
        content = fetch_url(url)
        if "[fetch error" not in content and len(content) > 200:
            return {"url": url, "content": content}

    # Fallback: guess common patterns
    company_slug = company_name.lower().replace(" ", "").replace(".", "").replace(",", "")
    for url in [f"https://www.{company_slug}.com/about", f"https://www.{company_slug}.com.au/about"]:
        content = fetch_url(url)
        if "[fetch error" not in content and len(content) > 200:
            return {"url": url, "content": content}

    return {"url": None, "content": "Company website not found."}


def search_careers_page(company_name, job_title):
    """Find the company's careers page using search."""
    urls = _web_search(f"{company_name} careers jobs")
    for url in urls:
        if any(kw in url.lower() for kw in ["career", "jobs", "hiring", "work"]):
            content = fetch_url(url)
            if "[fetch error" not in content and len(content) > 200:
                return {"url": url, "content": content[:3000]}

    return {"url": None, "content": "Careers page not found."}


_cost_tracker = {"total_tokens": 0, "total_cost_usd": 0.0, "calls": 0}


def get_cost_stats():
    """Return accumulated cost stats for this session."""
    return dict(_cost_tracker)


def call_llm(client, prompt, max_tokens=800, retries=3):
    """Helper to call OpenRouter with retry on failure."""
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=get_model(),
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            usage = getattr(response, "usage", None)
            if usage:
                input_t = getattr(usage, "prompt_tokens", 0) or 0
                output_t = getattr(usage, "completion_tokens", 0) or 0
                _cost_tracker["total_tokens"] += input_t + output_t
                _cost_tracker["total_cost_usd"] += (input_t * 3.0 / 1_000_000) + (output_t * 15.0 / 1_000_000)
            _cost_tracker["calls"] += 1
            return response.choices[0].message.content
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"[researcher] API error (attempt {attempt + 1}/{retries}), retrying in {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
            else:
                raise


def parse_json_response(raw_text):
    """Parse JSON from LLM response, stripping markdown code fences if present."""
    text = raw_text.strip()
    # Strip ```json ... ``` or ``` ... ```
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return json.loads(text)


def synthesise_company_research(client, company_name, website_content, job_description):
    """Uses LLM to extract structured company research from raw web content."""
    prompt = f"""You are researching the company "{company_name}" for a job applicant.

Here is content from their website:
{website_content}

Here is the job description:
{job_description[:2000]}

Extract and return a JSON object with these exact keys:
- overview: 2-3 sentence company overview
- mission: their stated mission or purpose (quote if available, otherwise infer)
- recent_news: any notable recent news, product launches, or strategic moves (or "None found")
- culture_signals: 3-4 key culture signals from their language and messaging
- team_structure: any hints about team size, structure, or how product/tech teams are organised

Return ONLY valid JSON, no other text."""

    raw = call_llm(client, prompt)

    try:
        return parse_json_response(raw)
    except json.JSONDecodeError:
        return {
            "overview": "Research not available",
            "mission": "N/A",
            "recent_news": "N/A",
            "culture_signals": "N/A",
            "team_structure": "N/A"
        }


def analyse_role_requirements(client, job_description, job_title):
    """Uses LLM to break down role requirements into must-have vs nice-to-have."""
    prompt = f"""Analyse this job description for "{job_title}" and extract the requirements.

JOB DESCRIPTION:
{job_description[:3000]}

Return a JSON object with these exact keys:
- must_have: list of 4-6 non-negotiable requirements (skills, experience, qualifications)
- nice_to_have: list of 3-4 preferred but optional requirements
- application_process: any stated application process steps or instructions (or "Standard application")
- inferred_team: brief inference about team structure/reporting based on the JD

Return ONLY valid JSON, no other text."""

    raw = call_llm(client, prompt, max_tokens=600)

    try:
        return parse_json_response(raw)
    except json.JSONDecodeError:
        return {
            "must_have": [],
            "nice_to_have": [],
            "application_process": "Standard application",
            "inferred_team": "Not determinable from JD"
        }


def find_hiring_manager(client, company_name, job_title):
    """
    Generates a LinkedIn search query and outreach template for the hiring manager.
    Does NOT hallucinate specific names — provides a manual lookup guide instead.
    """
    search_query = f'"{company_name}" ("Head of Product" OR "Director of Product" OR "VP Product" OR "Product Lead" OR "CPO")'
    search_url = f"https://www.linkedin.com/search/results/people/?keywords={requests.utils.quote(company_name + ' Head of Product')}"

    # Build candidate context from strategy for the outreach draft
    strategy = load_strategy()
    candidate_name = "the candidate"
    positioning = strategy.get("positioning_statement", "")
    top_selling = ""
    if strategy.get("selling_points"):
        sp = strategy["selling_points"][0]
        top_selling = f"{sp.get('name', '')}: {sp.get('metric', '')}"

    prompt = f"""Draft a brief, professional LinkedIn InMessage template (3-4 sentences) that a job candidate could personalise and send to a product leader at {company_name} regarding the {job_title} role.

Candidate context: {positioning[:200]}. Top achievement: {top_selling}.

The message should:
- Reference the specific role
- Highlight one relevant achievement
- Be warm but professional
- End with a soft ask (coffee chat or quick call)

Return ONLY the message text, no JSON, no formatting."""

    outreach_draft = call_llm(client, prompt, max_tokens=300)

    return {
        "name": None,
        "title": None,
        "linkedin_url": None,
        "search_url": search_url,
        "search_query": search_query,
        "outreach_draft": outreach_draft.strip(),
        "note": "Manual lookup required — search LinkedIn using the query above, then personalise the outreach draft."
    }


def generate_interview_questions(client, job_title, job_description, company_name, strategy):
    """Generates tailored interview questions with answer angles using the candidate's experience."""
    selling_points = "\n".join(
        f"- {sp['name']}: {sp['metric']}"
        for sp in strategy.get("selling_points", [])
    )

    prompt = f"""Generate 10 likely interview questions for a {job_title} role at {company_name}.

JOB DESCRIPTION (excerpt):
{job_description[:2000]}

CANDIDATE SELLING POINTS:
{selling_points}

For each question, provide:
- The question itself
- A suggested answer angle drawing specifically on the candidate's selling points listed below

Return a JSON array of objects with keys: "question" and "answer_angle".

Return ONLY valid JSON array, no other text."""

    raw = call_llm(client, prompt, max_tokens=1500)

    try:
        return parse_json_response(raw)
    except json.JSONDecodeError:
        return []


def research_job(job, strategy_path="strategy.json"):
    """
    Full research pipeline for a single job.
    Returns structured research object.
    """
    strategy = load_strategy(strategy_path)

    company_name = job.get("company", "")
    job_title = job.get("title", "")
    job_description = job.get("description", "") + " " + job.get("requirements", "")

    print(f"[researcher] Researching: {job_title} @ {company_name}")

    # Fetch website first (needed by company synthesis), then run LLM calls in parallel
    # Run sequentially (httpx/tokenizer threading issues on macOS)
    website = search_company_website(company_name)
    client = get_client()

    company_data = synthesise_company_research(client, company_name, website["content"], job_description)
    role_data = analyse_role_requirements(client, job_description, job_title)
    hiring_manager = find_hiring_manager(client, company_name, job_title)
    interview_questions = generate_interview_questions(client, job_title, job_description, company_name, strategy)

    return {
        "job_url": job.get("url", ""),
        "company": company_data,
        "role": role_data,
        "hiring_manager": hiring_manager,
        "interview_questions": interview_questions
    }


def research_all_jobs(top_jobs, strategy_path="strategy.json"):
    """Research all top jobs in parallel and return a URL-keyed map."""
    jobs_to_research = [j for j in top_jobs if not j.get("near_relevant_fill")]
    research_map = {}

    for job in jobs_to_research:
        try:
            research = research_job(job, strategy_path)
            research_map[job.get("url", "")] = research
        except Exception as e:
            print(f"[researcher] Error researching {job.get('title')}: {e}", file=sys.stderr)

    return research_map


if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            job = json.load(f)
        result = research_job(job)
        print(json.dumps(result, indent=2))
