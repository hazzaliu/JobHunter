"""
discord_notify.py
Sends formatted daily reports and alerts via Discord webhook.
"""

import json
import os
import sys
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)


def load_config(path="config.json"):
    with open(path, "r") as f:
        config = json.load(f)
    config["discord"]["webhook_url"] = os.environ["DISCORD_WEBHOOK_URL"]
    return config


AREA_LABELS = {
    "seniority_culture": "Seniority & Culture",
    "fit_classifier": "Fit & Opportunity",
    "devils_advocate": "Devil's Advocate"
}

CLASSIFICATION_EMOJI = {
    "Safe": "🟢",
    "Stretch": "🟡",
    "Reach": "🔴"
}


def send_webhook(webhook_url, content):
    """Sends a message to Discord via webhook."""
    payload = {"content": content}
    response = requests.post(webhook_url, json=payload)
    if response.status_code not in (200, 204):
        print(f"[discord_notify] Webhook error: {response.status_code} {response.text}", file=sys.stderr)
        return False
    return True


def format_job_entry(job, rank, notion_pages=None):
    """Formats a single job entry for the Discord report."""
    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    fit_score = job.get("fit_score", 0)
    classification = job.get("classification", "Unknown")
    strongest_area = AREA_LABELS.get(job.get("strongest_area", ""), job.get("strongest_area", ""))
    weakest_area = AREA_LABELS.get(job.get("weakest_area", ""), job.get("weakest_area", ""))
    strongest_strategy = job.get("strongest_strategy", "")
    weakest_strategy = job.get("weakest_strategy", "")
    emoji = CLASSIFICATION_EMOJI.get(classification, "⚪")
    is_fill = job.get("near_relevant_fill", False)

    # Find Notion page link
    notion_link = ""
    if notion_pages:
        for page in notion_pages:
            if f"{title} @ {company}" in page.get("job", ""):
                page_id = page.get("page_id", "")
                if page_id:
                    notion_link = f"\n   → Notion: https://notion.so/{page_id.replace('-', '')}"

    fill_note = " *(near-relevant fill)*" if is_fill else ""

    lines = [
        f"**{rank}. {title}** | {company}{fill_note}",
        f"   Score: **{fit_score}/100** · {emoji} {classification}",
        f"   ✅ Strongest — {strongest_area}: *{strongest_strategy[:120]}{'...' if len(strongest_strategy) > 120 else ''}*",
        f"   ⚠️  Weakest — {weakest_area}: *{weakest_strategy[:120]}{'...' if len(weakest_strategy) > 120 else ''}*",
    ]
    if notion_link:
        lines.append(notion_link)

    return "\n".join(lines)


def format_near_relevant_entry(job):
    """Formats a near-relevant job for the bottom of the report."""
    title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")
    score = job.get("fit_score", 0)
    weakest_area = AREA_LABELS.get(job.get("weakest_area", ""), "")
    weakest_strategy = job.get("weakest_strategy", "")

    return (
        f"• **{title}** | {company} — Score: {score}/100\n"
        f"  Gap: {weakest_area} — {weakest_strategy[:150]}"
    )


def send_daily_report(scoring_result, notion_pages=None, config_path="config.json"):
    """Sends the full daily scouting report to Discord."""
    config = load_config(config_path)
    webhook_url = config["discord"]["webhook_url"]
    today = datetime.now().strftime("%d %B %Y")

    top_3 = scoring_result.get("top_3", [])
    near_relevant = [j for j in scoring_result.get("near_relevant", []) if not j.get("near_relevant_fill")]
    status = scoring_result.get("status", "ok")

    # Header
    lines = [
        f"🔍 **Job Scout — {today}**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # Status message for low-match days
    if status == "no_qualifying":
        lines.append("⚠️  **No strong matches today** (0 jobs scored ≥60). Showing best available below.")
        lines.append("")
    elif status == "partial_qualifying":
        qualifying_count = len([j for j in top_3 if not j.get("near_relevant_fill")])
        lines.append(f"ℹ️  **{qualifying_count} qualifying match{'es' if qualifying_count != 1 else ''} today** (threshold: 60/100). Remaining slots filled from near-relevant pool.")
        lines.append("")

    # Top 3
    if top_3:
        lines.append("**TOP MATCHES**")
        lines.append("")
        for i, job in enumerate(top_3, 1):
            lines.append(format_job_entry(job, i, notion_pages))
            lines.append("")

    # Near-relevant section
    if near_relevant:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"**NEAR-RELEVANT** *(below 60 threshold)*")
        lines.append("")
        for job in near_relevant[:5]:  # Cap at 5
            lines.append(format_near_relevant_entry(job))
            lines.append("")

    # Footer
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if status == "no_qualifying":
        lines.append("📌 *Reply here or in Cowork to lower the threshold, adjust search terms, or give feedback.*")
    else:
        lines.append("📌 *Reply here or in Cowork to give feedback, skip industries, or ask questions.*")

    # Discord has a 2000 char limit per message — split if needed
    full_message = "\n".join(lines)

    if len(full_message) <= 2000:
        send_webhook(webhook_url, full_message)
    else:
        # Split into chunks
        chunks = []
        current_chunk = []
        current_len = 0
        for line in lines:
            if current_len + len(line) + 1 > 1900:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_len = len(line)
            else:
                current_chunk.append(line)
                current_len += len(line) + 1
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        for chunk in chunks:
            send_webhook(webhook_url, chunk)

    print(f"[discord_notify] Daily report sent ({len(top_3)} top matches, {len(near_relevant)} near-relevant).")


def send_question(question_text, job_title, company, config_path="config.json"):
    """Sends a context-gathering question to Discord when doc generation is paused."""
    config = load_config(config_path)
    webhook_url = config["discord"]["webhook_url"]

    message = (
        f"⏸️ **Document generation paused — input needed**\n\n"
        f"I'm preparing your resume and cover letter for **{job_title} @ {company}** "
        f"but need a bit more context first:\n\n"
        f"❓ {question_text}\n\n"
        f"*Reply here or in Cowork — I'll continue once you respond.*"
    )

    send_webhook(webhook_url, message)
    print(f"[discord_notify] Question sent for {job_title} @ {company}.")


def send_error_alert(error_message, config_path="config.json"):
    """Sends an error notification to Discord."""
    config = load_config(config_path)
    webhook_url = config["discord"]["webhook_url"]

    message = (
        f"⚠️ **Job Scout — Run Error**\n\n"
        f"{error_message}\n\n"
        f"*I'll retry on the next scheduled run.*"
    )
    send_webhook(webhook_url, message)


def send_no_new_jobs(config_path="config.json"):
    """Sends a short message when all results are duplicates."""
    config = load_config(config_path)
    webhook_url = config["discord"]["webhook_url"]
    today = datetime.now().strftime("%d %B %Y")

    message = (
        f"🔍 **Job Scout — {today}**\n\n"
        f"Nothing new today — all scraped listings have been seen before. "
        f"I'll run again tomorrow. Let me know if you'd like me to broaden the search terms."
    )
    send_webhook(webhook_url, message)


if __name__ == "__main__":
    # Test: send a test message
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        config = load_config()
        send_webhook(
            config["discord"]["webhook_url"],
            "✅ **Job Scout** — Discord connection test successful."
        )
        print("Test message sent.")
