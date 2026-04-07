"""
notion_writer.py
Writes scored job entries to the Notion master database.
"""

import json
import os
import sys
import requests
from notion_client import Client
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)


def load_config(path="config.json"):
    with open(path, "r") as f:
        config = json.load(f)
    config["notion"]["token"] = os.environ["NOTION_TOKEN"]
    config["notion"]["jobs_database_id"] = os.environ["NOTION_DATABASE_ID"]
    config["discord"]["webhook_url"] = os.environ["DISCORD_WEBHOOK_URL"]
    return config


AREA_LABELS = {
    "seniority_culture": "Seniority & Culture",
    "fit_classifier": "Fit & Opportunity",
    "devils_advocate": "Devil's Advocate",
}


def create_text_block(content):
    """Helper to create a Notion paragraph block."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": str(content)[:2000]}}]
        }
    }


def create_heading_block(content, level=2):
    key = f"heading_{level}"
    return {
        "object": "block",
        "type": key,
        key: {
            "rich_text": [{"type": "text", "text": {"content": str(content)}}]
        }
    }


def format_interview_questions(questions):
    """Formats interview questions list into readable text."""
    if not questions:
        return "No interview questions generated."
    lines = []
    for i, q in enumerate(questions, 1):
        if isinstance(q, dict):
            lines.append(f"Q{i}: {q.get('question', '')}")
            if q.get('answer_angle'):
                lines.append(f"   → Angle: {q.get('answer_angle', '')}")
            lines.append("")
        else:
            lines.append(f"Q{i}: {q}")
    return "\n".join(lines)


def write_job_to_notion(client, database_id, scored_job, research=None, near_relevant=False, application_answers=None, tailored_cv=None):
    """
    Creates a new Notion page for a single job entry.
    scored_job: output from scorer.py
    research: output from researcher.py (optional)
    near_relevant: if True, marks as near-relevant (below threshold)
    """
    job = scored_job.get("job_data", {})
    title = scored_job.get("title", job.get("title", "Unknown Role"))
    company = scored_job.get("company", job.get("company", "Unknown Company"))
    fit_score = scored_job.get("fit_score", 0)
    classification = scored_job.get("classification", "Unknown")
    strongest_area = scored_job.get("strongest_area", "")
    weakest_area = scored_job.get("weakest_area", "")
    strongest_strategy = scored_job.get("strongest_strategy", "")
    weakest_strategy = scored_job.get("weakest_strategy", "")
    job_url = scored_job.get("url", job.get("url", ""))

    # Build properties
    properties = {
        "Job Title": {
            "title": [{"type": "text", "text": {"content": title}}]
        },
        "Company": {
            "rich_text": [{"type": "text", "text": {"content": company}}]
        },
        "Fit Score": {
            "number": fit_score
        },
        "Classification": {
            "select": {"name": classification}
        },
        "Status": {
            "select": {"name": "Near-Relevant" if near_relevant else "New"}
        },
        "Date Surfaced": {
            "date": {"start": datetime.now().strftime("%Y-%m-%d")}
        },
        "Strongest Area": {
            "rich_text": [{"type": "text", "text": {"content": AREA_LABELS.get(strongest_area, strongest_area)}}]
        },
        "Weakest Area": {
            "rich_text": [{"type": "text", "text": {"content": AREA_LABELS.get(weakest_area, weakest_area)}}]
        },
        "Strongest Strategy": {
            "rich_text": [{"type": "text", "text": {"content": strongest_strategy[:2000]}}]
        },
        "Weakest Strategy": {
            "rich_text": [{"type": "text", "text": {"content": weakest_strategy[:2000]}}]
        },
    }

    if job_url:
        properties["Job URL"] = {"url": job_url}

    # Build child blocks for rich content
    children = []

    # Score summary
    children.append(create_heading_block("Fit Score Breakdown", 2))
    agent_scores = scored_job.get("agent_scores", {})
    score_summary = (
        f"Total: {fit_score}/100 ({classification})\n\n"
        f"Seniority & Culture: {agent_scores.get('seniority_culture', {}).get('score', 0)}/33\n"
        f"Fit & Opportunity: {agent_scores.get('fit_classifier', {}).get('score', 0)}/33\n"
        f"Devil's Advocate: {agent_scores.get('devils_advocate', {}).get('score', 0)}/34\n\n"
        f"✅ Strongest — {AREA_LABELS.get(strongest_area, strongest_area)}: {strongest_strategy}\n\n"
        f"⚠️  Weakest — {AREA_LABELS.get(weakest_area, weakest_area)}: {weakest_strategy}"
    )
    children.append(create_text_block(score_summary))

    # Agent rationales
    children.append(create_heading_block("Agent Rationales", 2))
    for agent_key, label in [
        ("seniority_culture", "Agent 1 — Seniority & Culture"),
        ("fit_classifier", "Agent 2 — Fit & Opportunity"),
        ("devils_advocate", "Agent 3 — Devil's Advocate")
    ]:
        rationale = agent_scores.get(agent_key, {}).get("rationale", "")
        if rationale:
            children.append(create_heading_block(label, 3))
            # Split long rationale into chunks (Notion limit per block)
            for chunk_start in range(0, len(rationale), 1900):
                children.append(create_text_block(rationale[chunk_start:chunk_start + 1900]))

    # Company research
    if research:
        children.append(create_heading_block("Company Research", 2))
        company_info = research.get("company", {})
        company_text = "\n".join([
            f"Overview: {company_info.get('overview', 'N/A')}",
            f"Mission: {company_info.get('mission', 'N/A')}",
            f"Recent News: {company_info.get('recent_news', 'N/A')}",
            f"Culture Signals: {company_info.get('culture_signals', 'N/A')}",
            f"Team Structure: {company_info.get('team_structure', 'N/A')}",
        ])
        children.append(create_text_block(company_text))

        # Role breakdown
        children.append(create_heading_block("Role Breakdown", 2))
        role_info = research.get("role", {})
        role_text = "\n".join([
            f"Must-Have Requirements:\n" + "\n".join(f"• {r}" for r in role_info.get("must_have", [])),
            f"\nNice-to-Have:\n" + "\n".join(f"• {r}" for r in role_info.get("nice_to_have", [])),
            f"\nApplication Process: {role_info.get('application_process', 'N/A')}",
            f"\nInferred Team: {role_info.get('inferred_team', 'N/A')}",
        ])
        children.append(create_text_block(role_text))

        # Interview questions
        children.append(create_heading_block("Interview Preparation", 2))
        questions_text = format_interview_questions(research.get("interview_questions", []))
        for chunk_start in range(0, len(questions_text), 1900):
            children.append(create_text_block(questions_text[chunk_start:chunk_start + 1900]))

        # Hiring manager
        hm = research.get("hiring_manager")
        if hm:
            children.append(create_heading_block("Hiring Manager Outreach", 2))
            hm_parts = []
            if hm.get("search_url"):
                hm_parts.append(f"LinkedIn search: {hm['search_url']}")
            if hm.get("note"):
                hm_parts.append(hm["note"])
            if hm.get("outreach_draft"):
                hm_parts.append(f"\nOutreach Draft:\n{hm['outreach_draft']}")
            children.append(create_text_block("\n".join(hm_parts)))

    # Application answers (if available)
    if application_answers:
        children.append(create_heading_block("Draft Application Answers", 2))
        question_labels = {
            "q1": "Why are you interested in this role?",
            "q2": "Why this company?",
            "q3": "Describe your most relevant experience",
            "q4": "What's your biggest weakness/area for growth?",
            "q5": "What are your salary expectations?",
        }
        for key, label in question_labels.items():
            answer = application_answers.get(key, "")
            if answer:
                children.append(create_heading_block(f"Q: {label}", 3))
                children.append(create_text_block(answer))

    # Tailored CV (if available)
    if tailored_cv:
        children.append(create_heading_block("Tailored CV", 2))
        for chunk_start in range(0, len(tailored_cv), 1900):
            children.append(create_text_block(tailored_cv[chunk_start:chunk_start + 1900]))

    # Create the page
    new_page = client.pages.create(
        parent={"database_id": database_id},
        properties=properties,
        children=children[:100]  # Notion API limit: 100 blocks per request
    )

    print(f"[notion_writer] Created page: {title} @ {company} (Score: {fit_score})")
    return new_page["id"]


def get_existing_urls(token, database_id):
    """Fetch all Job URLs already in the Notion database to prevent duplicates."""
    existing = set()
    has_more = True
    start_cursor = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    while has_more:
        body = {"page_size": 100}
        if start_cursor:
            body["start_cursor"] = start_cursor
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            headers=headers,
            json=body,
        )
        data = resp.json()
        for page in data.get("results", []):
            url_prop = page.get("properties", {}).get("Job URL", {}).get("url")
            if url_prop:
                existing.add(url_prop)
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")
    return existing


def write_all_jobs(scoring_result, research_map=None, application_answers_map=None, tailored_cvs_map=None, config_path="config.json"):
    """
    Writes top jobs to Notion, skipping duplicates.
    Includes application answers and tailored CVs for qualifying jobs.
    """
    config = load_config(config_path)
    client = Client(auth=config["notion"]["token"], notion_version="2022-06-28")
    database_id = config["notion"]["jobs_database_id"]
    research_map = research_map or {}
    application_answers_map = application_answers_map or {}
    tailored_cvs_map = tailored_cvs_map or {}

    # Fetch existing URLs to prevent duplicates
    existing_urls = get_existing_urls(config["notion"]["token"], database_id)
    print(f"[notion_writer] {len(existing_urls)} existing entries in Notion.")

    written = []

    # Top jobs (deep-analyzed)
    for job in scoring_result.get("top_3", []):
        job_url = job.get("url", job.get("job_data", {}).get("url", ""))
        if job_url in existing_urls:
            print(f"[notion_writer] Skipped (duplicate): {job.get('title', '')} @ {job.get('company', '')}")
            continue
        research = research_map.get(job_url)
        answers = application_answers_map.get(job_url)
        cv = tailored_cvs_map.get(job_url)
        is_fill = job.get("near_relevant_fill", False)
        page_id = write_job_to_notion(
            client, database_id, job, research,
            near_relevant=is_fill,
            application_answers=answers,
            tailored_cv=cv,
        )
        written.append({"job": f"{job['title']} @ {job['company']}", "page_id": page_id, "type": "top"})
        existing_urls.add(job_url)

    print(f"[notion_writer] Wrote {len(written)} entries to Notion.")
    return written


if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            scoring_result = json.load(f)
        write_all_jobs(scoring_result)
