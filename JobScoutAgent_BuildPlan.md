# Job Scout Agent — Build Plan

> **Note:** This is the original build specification used to develop JobHunter. Example data references a sample candidate profile. Replace with your own details when customising.

**[Your Name] | Claude Scheduled Tasks Runtime**
*Version 1.0 — March 2026*

---

## Runtime Choice: Claude Scheduled Tasks

Running on Claude Scheduled Tasks means Claude is the agent — not just a component. The daily job scout is a Cowork skill that Claude follows on a schedule, using Python scripts for API calls and file generation, and Claude's own reasoning for scoring, research, and document tailoring. This removes the need for any cloud infrastructure, and keeps everything within the tools already set up.

**What this means in practice:**
- No server to manage, no deployment pipeline
- Python scripts handle API integrations (Apify, Notion, Discord)
- Claude handles all AI reasoning (scoring agents, document generation, research)
- State persists in local JSON files and Notion
- Discord notifications sent via webhook (no bot setup required)

---

## Architecture Overview

```
DAILY TRIGGER (Claude Scheduled Tasks — 7:00 AM)
         │
         ▼
[1] apify_scraper.py ──→ raw job listings (JSON)
         │
         ▼
[2] deduplicator.py ──→ filters seen/blocked jobs ──→ seen_jobs.json
         │
         ▼
[3] Claude: 3-Agent Scoring Panel ──→ fit scores + classifications
         │
         ▼
[4] Threshold Gate ──→ Top 3 + Near-Relevant list
         │
    ┌────┴────┐
    ▼         ▼
[5a] Claude:      [5b] Claude:
Research Module   Document Generation
(company + role)  (resume + cover letter)
    │                   │
    └────────┬───────────┘
             ▼
[6] notion_writer.py ──→ Notion Master Database
             │
             ▼
[7] discord_notify.py ──→ Discord Webhook ──→ the user's channel
```

**Interaction flow (when Claude needs missing info):**
```
Claude detects gap → discord_notify.py sends question to Discord
→ the user replies in Cowork → Claude continues generation
→ answer stored in strategy.json for future runs
```

---

## File Structure

```
/job-scout/
│
├── config.json              # API keys, webhook URL, search preferences
├── strategy.json            # Career Strategy Profile (evolves over time)
├── seen_jobs.json           # Deduplication store (persistent across sessions)
├── pending_questions.json   # Questions awaiting the user's reply
│
├── scripts/
│   ├── apify_scraper.py     # Calls Apify LinkedIn Jobs Scraper
│   ├── deduplicator.py      # Seen jobs filter + blocked list management
│   ├── scorer.py            # Orchestrates 3-agent scoring, returns fit scores
│   ├── doc_generator.py     # python-docx resume + cover letter generation
│   ├── notion_writer.py     # Writes job entries + attaches docs to Notion
│   ├── discord_notify.py    # Sends formatted reports via Discord webhook
│   └── researcher.py        # Company website + LinkedIn public profile research
│
├── templates/
│   ├── resume_template.docx    # the user's locked base resume template
│   └── coverletter_template.docx  # the user's locked base cover letter template
│
├── prompts/
│   ├── agent1_seniority.txt    # Agent 1 system prompt
│   ├── agent2_fit.txt          # Agent 2 system prompt
│   ├── agent3_devils.txt       # Agent 3 system prompt
│   ├── resume_tailor.txt       # Resume tailoring instructions
│   └── coverletter_tailor.txt  # Cover letter tailoring instructions
│
└── SKILL.md                 # Daily orchestration script Claude follows
```

---

## Prerequisites — What the user Needs to Set Up

Before any code is written, the user needs four things. Each has a setup section below.

### 1. Apify Account
- Sign up at apify.com (free tier sufficient to start)
- Go to Actors → search "LinkedIn Jobs Scraper"
- Note your **API Token** from Settings → Integrations
- Note the **Actor ID** for the LinkedIn Jobs Scraper

```json
// config.json (Apify section)
"apify": {
  "api_token": "YOUR_APIFY_TOKEN",
  "actor_id": "YOUR_ACTOR_ID",
  "search_terms": ["Product Manager AI", "AI Product Manager", "Data Product Manager", "Senior PM AI"],
  "location": "Melbourne, Australia",
  "max_results": 50
}
```

### 2. Notion Integration
- Go to notion.so/my-integrations → New Integration → name it "Job Scout"
- Note your **Integration Token**
- Create a new Notion database (Claude will define the schema in Sprint 6)
- Share the database with your integration
- Note the **Database ID** (from the URL: notion.so/[workspace]/`DATABASE_ID`?v=...)

```json
// config.json (Notion section)
"notion": {
  "token": "YOUR_NOTION_TOKEN",
  "database_id": "YOUR_DATABASE_ID"
}
```

### 3. Discord Webhook
No bot required — just a webhook URL from your existing server.
- Open your Discord server → Settings → Integrations → Webhooks → New Webhook
- Name it "Job Scout" and choose your notifications channel
- Copy the **Webhook URL**

```json
// config.json (Discord section)
"discord": {
  "webhook_url": "YOUR_DISCORD_WEBHOOK_URL"
}
```

### 4. Python Environment
The scripts require Python 3.11+ and the following packages:
```bash
pip install apify-client notion-client python-docx requests anthropic
```

---

## Build Sprints

Each sprint produces something testable before the next begins.

---

### Sprint 1 — Foundation & Strategy Profile
**Goal:** Working file structure, config, and a completed Career Strategy Profile.
**Estimated time:** 1–2 hours (mostly interview time with the user)

**Tasks:**
- Create the full file structure above
- Build `config.json` schema and populate with the user's API keys
- Conduct the Strategy Interview (from Phase 0 of the flow spec) and produce `strategy.json`
- Create `seen_jobs.json` as an empty array `[]`
- Create `pending_questions.json` as an empty array `[]`

**strategy.json schema:**
```json
{
  "positioning_statement": "...",
  "target_roles": ["Product Manager", "AI Product Manager", "Senior PM"],
  "target_industries": ["SaaS", "AI/ML", "Fintech", "HealthTech"],
  "blocked_industries": [],
  "blocked_companies": [],
  "culture_preferences": "...",
  "risk_appetite": "stretch",
  "non_negotiables": {
    "min_salary": null,
    "work_mode": "hybrid",
    "company_stage": ["scaleup", "enterprise"]
  },
  "selling_points": ["SurveyForge (93% efficiency gain)", "Synthetic data prototype (70% cost reduction)", "..."],
  "known_gaps": ["Limited regulated industry experience", "No formal ML engineering background"],
  "gap_strategies": { "...": "..." },
  "feedback_log": []
}
```

**Milestone:** Strategy Profile reviewed and confirmed by the user. File structure in place.

---

### Sprint 2 — Apify Scout Pipeline
**Goal:** Working scraper that returns deduplicated, pre-filtered job listings as JSON.
**Estimated time:** 2–3 hours

**Tasks:**
- Build `apify_scraper.py`:
  - Calls Apify LinkedIn Jobs Scraper with search terms + location from config
  - Returns structured job objects: `{id, title, company, description, requirements, seniority, url, posted_date}`
  - Handles API errors gracefully (rate limits, empty results)
- Build `deduplicator.py`:
  - Loads `seen_jobs.json`
  - Filters out any job IDs already in the seen list
  - Filters out jobs from blocked industries/companies in `strategy.json`
  - Runs lightweight pre-filter (removes obvious graduate/entry-level roles)
  - Returns clean list of new, eligible jobs
  - Appends new job IDs to `seen_jobs.json`

**Edge cases handled:**
- All results are duplicates → function returns empty list with a `"all_duplicate"` flag
- Apify returns 0 results → function returns empty list with `"no_results"` flag
- API timeout → retries once, then returns `"api_error"` flag

**Milestone:** Run the scraper manually. Confirm it returns Melbourne PM/AI jobs and that re-running it returns zero results (deduplication working).

---

### Sprint 3 — Multi-Agent Scoring Engine
**Goal:** Each eligible job receives a Fit Score (0–100), classification, and written rationale.
**Estimated time:** 3–4 hours

**Tasks:**
- Write the three agent system prompts (stored in `/prompts/`):

**agent1_seniority.txt** — instructs Claude to evaluate seniority match, years of experience alignment, company size/stage fit, and culture signals from the JD. Score: 0–33.

**agent2_fit.txt** — instructs Claude to assess skill coverage (% of JD requirements the user demonstrably meets), classify as Safe/Stretch/Reach, and explain the basis. Score: 0–33.

**agent3_devils.txt** — instructs Claude to take the opposing view: what's missing, what a sceptical recruiter would flag, and whether there are structural red flags in the role itself. Score: 0–34.

- Build `scorer.py`:
  - Takes a job object and the user's strategy profile as inputs
  - Calls each agent prompt sequentially (or in parallel for speed)
  - Parses each agent's score + rationale
  - Sums scores → Fit Score out of 100
  - Identifies strongest agent score and weakest agent score
  - Returns full scoring object per job

**Scoring output schema:**
```json
{
  "job_id": "...",
  "fit_score": 74,
  "classification": "Stretch",
  "agent_scores": {
    "seniority_culture": {"score": 26, "rationale": "..."},
    "fit_classifier": {"score": 24, "rationale": "...", "label": "Stretch"},
    "devils_advocate": {"score": 24, "rationale": "..."}
  },
  "strongest_area": "seniority_culture",
  "weakest_area": "fit_classifier",
  "strongest_strategy": "Lead with your PM track record at Forethought — title and scope are a strong match.",
  "weakest_strategy": "The JD requires stakeholder management at exec level. Reframe your MBS consulting fellowship.",
  "qualifies": true
}
```

- Apply threshold gate: mark jobs with score ≥ 60 as `"qualifies": true`
- Sort all qualifying jobs by score descending → select top 3
- Tag remaining as near-relevant

**Milestone:** Run scorer on 5 sample job descriptions. Verify scores feel calibrated — a clearly great fit should score 75+, a poor fit should score below 50.

---

### Sprint 4 — Document Generation Engine
**Goal:** Tailored .docx resume and cover letter generated for each Top 3 role.
**Estimated time:** 4–5 hours (most complex sprint)

**Tasks:**
- Convert the user's resume and cover letter PDFs into Word `.docx` templates (using the docx skill)
- These become the locked base templates in `/templates/`

- Build `doc_generator.py`:
  - Takes a job object + scoring output + the user's strategy profile
  - **Pre-generation check:** Scans JD for requirements not addressable from known experience
  - If gaps found: writes questions to `pending_questions.json` and returns `"awaiting_input"` status
  - If no gaps (or after gaps resolved): proceeds to generation

  **Resume generation:**
  - Applies six tailoring levers via python-docx:
    1. Role subtitle under name → matches target title
    2. Summary block → rewritten via Claude prompt using JD + strategy profile
    3. Key achievements → three most relevant selected + lightly reworded
    4. Role bullets → each role's bullets rewritten to surface relevant skills
    5. Technical skills section → categories renamed + content reordered
    6. Tone calibration → technical depth adjusted to company type
  - Saves output as `[Company]_[Role]_Resume_the user.docx`

  **Cover letter generation:**
  - Applies 4-paragraph Claude prompt using JD + company research + strategy profile
  - Para 1: Role + company + the user's positioning for this specific context
  - Para 2: Forethought experience reframed for this JD with relevant metrics
  - Para 3: MBS/Blitzm experience tied to role's secondary requirements
  - Para 4: Company-specific fit based on research output
  - Saves output as `[Company]_[Role]_CoverLetter_the user.docx`

- Build the **Application Advice Note** generation:
  - A short Claude-generated markdown note covering strongest angle, weakest area + coping strategy, tone recommendation, and one preparation action item
  - Stored as plain text in the Notion entry (not a Word doc)

**Pending questions flow:**
```
doc_generator detects gap
→ writes to pending_questions.json
→ discord_notify sends question to the user's channel
→ the user replies in Cowork session
→ answer written back to strategy.json
→ doc_generator re-runs for that job with complete info
```

**Milestone:** Generate a resume and cover letter for one real job description. Compare against the user's existing tailored examples — structure, tone, and content should feel equivalent in quality.

---

### Sprint 5 — Company & Role Research Module
**Goal:** Each Top 3 role has a structured research dossier ready for Notion.
**Estimated time:** 2–3 hours

**Tasks:**
- Build `researcher.py`:
  - **Company research:** Fetches company website using WebFetch. Extracts: mission, product overview, recent news, culture signals, team structure hints.
  - **Role research:** Parses JD to extract must-have vs. nice-to-have requirements, inferred team structure, application process steps from careers page.
  - **LinkedIn public profile search:** Searches for the likely hiring manager or adjacent person (Head of Product, CPO, team lead) using public LinkedIn. Returns name, title, LinkedIn URL only.
  - **Outreach draft:** Claude generates a brief, personalised LinkedIn InMessage or email draft based on the public profile findings. Stored in Notion — the user sends it himself.
  - **Interview question generation:** Claude generates 8–12 role-specific questions + suggested answer angles drawing on the user's actual experience.

**Research output schema:**
```json
{
  "company": {
    "overview": "...",
    "mission": "...",
    "recent_news": "...",
    "culture_signals": "...",
    "team_structure": "..."
  },
  "role": {
    "must_have": ["...", "..."],
    "nice_to_have": ["...", "..."],
    "application_process": "...",
    "inferred_team": "..."
  },
  "hiring_manager": {
    "name": "...",
    "title": "...",
    "linkedin_url": "...",
    "outreach_draft": "..."
  },
  "interview_questions": [
    {"question": "...", "answer_angle": "..."}
  ]
}
```

**Edge case — hiring manager not findable:** Field set to `null`. No outreach draft generated. No placeholder shown in Notion.

**Milestone:** Run researcher on one company. Confirm the research output feels genuinely useful — not just scraped text, but organised and actionable.

---

### Sprint 6 — Notion Database Integration
**Goal:** Each Top 3 job gets a fully populated Notion database row with all documents attached.
**Estimated time:** 2–3 hours

**Notion database schema (defined and created as part of this sprint):**

| Property | Type | Notes |
|---|---|---|
| Job Title | Title | Primary field |
| Company | Text | |
| Fit Score | Number | 0–100 |
| Classification | Select | Safe / Stretch / Reach |
| Status | Select | New / Reviewing / Applied / Interviewing / Rejected / Offer |
| Date Surfaced | Date | Auto |
| Strongest Area | Text | From scoring output |
| Weakest Area | Text | From scoring output |
| Strongest Strategy | Text | What to lead with |
| Weakest Strategy | Text | How to cope |
| Application Advice | Text | Full advice note |
| Company Research | Text | Structured research summary |
| Interview Questions | Text | Formatted Q&A list |
| Application Process | Text | Steps from careers page |
| Hiring Manager | Text | Name + LinkedIn URL |
| Outreach Draft | Text | Draft InMessage/email |
| Job URL | URL | Direct link |
| Notes | Text | the user's personal notes |
| Resume | Files | Attached .docx |
| Cover Letter | Files | Attached .docx |

**Tasks:**
- Build `notion_writer.py`:
  - Creates a new page in the Notion database for each Top 3 job
  - Maps all scoring + research + advice data to the correct properties
  - Uploads the generated .docx files as file attachments
  - Creates a secondary page for each near-relevant job with score + explanation only (no documents)

**Milestone:** Run full pipeline on one job end-to-end. Confirm Notion row appears correctly populated with all fields and file attachments accessible.

---

### Sprint 7 — Discord Notifications
**Goal:** the user receives a clean, actionable daily report in Discord.
**Estimated time:** 1–2 hours

**Tasks:**
- Build `discord_notify.py`:
  - Sends daily report via Discord webhook (no bot required)
  - Report format:

```
🔍 Job Scout — 27 March 2026

TOP 3 MATCHES

1️⃣  Senior PM – AI Products | Atlassian
   Score: 84/100 · Stretch
   ✅ Strongest: Seniority & culture fit (lead with Forethought PM role)
   ⚠️  Weakest: No enterprise-scale AI deployment (reframe RAG project scope)
   → Notion: [link]

2️⃣  Product Lead – Data Platform | Seek
   Score: 76/100 · Safe
   ✅ Strongest: Skill coverage (strong match across 80% of JD requirements)
   ⚠️  Weakest: Leadership scope lighter than your current trajectory
   → Notion: [link]

3️⃣  AI Product Manager | Canva
   Score: 71/100 · Stretch
   ✅ Strongest: Cultural fit (product-led, fast-moving environment)
   ⚠️  Weakest: Consumer product experience not well established
   → Notion: [link]

──────────────────────────────
NEAR-RELEVANT (below threshold)

• Data Analyst PM | REA Group — Score: 52/100
  Why it didn't qualify: 3 core requirements unmet (Tableau, CRM domain, B2C focus)
  What would close the gap: surface your Brand Catalyser dashboard work more prominently

──────────────────────────────
Reply here or in Cowork to give feedback, skip industries, or ask questions.
```

- Handles low-match days with appropriate messaging
- Sends pending questions as a separate message when doc generation is paused

**Milestone:** Trigger a test notification manually. Confirm format renders correctly in Discord and all Notion links are live.

---

### Sprint 8 — Orchestration Skill & Scheduled Task
**Goal:** The full pipeline runs automatically every morning without manual intervention.
**Estimated time:** 2–3 hours

**Tasks:**
- Write `SKILL.md` — the daily orchestration script Claude follows:
  - Step-by-step instructions covering all phases in sequence
  - Error handling instructions (what to do if Apify fails, if Notion is unreachable, etc.)
  - Fallback behaviours for each edge case
  - Instructions for reading config.json, strategy.json, and seen_jobs.json at the start of each run
  - Instructions for updating seen_jobs.json and strategy.json at the end of each run

- Register the scheduled task in Claude Cowork:
  - **Schedule:** Daily at 7:00 AM
  - **Task:** Run the job scout skill
  - **Notification:** On completion

- Add error logging:
  - Each run writes a brief log to `run_log.json` with timestamp, jobs processed, errors encountered, and outcome
  - If a run fails entirely, a Discord message is sent: *"Today's scout encountered an error: [description]. I'll retry tomorrow."*

**Milestone:** Let the scheduled task run twice on consecutive mornings. Confirm deduplication works across sessions (jobs from day 1 don't reappear on day 2). Confirm Notion updates and Discord notification fires.

---

## Testing Plan

| Sprint | Test | Pass Criteria |
|---|---|---|
| 1 | Strategy Profile review | the user confirms it accurately reflects his goals and preferences |
| 2 | Scraper + dedup | Returns Melbourne PM jobs; zero duplicates on second run |
| 3 | Scoring calibration | 5 sample JDs scored; best fit ≥75, worst fit ≤45 |
| 4 | Document quality | Output matches tone, structure, and accuracy of the user's existing tailored examples |
| 5 | Research completeness | Company dossier and interview questions feel genuinely useful |
| 6 | Notion population | All fields correct; documents accessible |
| 7 | Discord formatting | Report renders cleanly; links live |
| 8 | End-to-end run x2 | Full pipeline fires on schedule; dedup works across runs |

---

## Known Constraints & Workarounds

| Constraint | Workaround |
|---|---|
| Apify free tier has limited monthly runs | Start with 1 run/day; upgrade if needed (~$50/month for heavy use) |
| Discord webhook is one-way (can't receive replies) | the user replies in Cowork chat; questions handled in next session |
| Notion file upload size limits | Keep .docx files under 5MB (all generated docs will be well under this) |
| LinkedIn occasionally blocks scrapers | Apify's actor handles this with rotating proxies; monitor for errors in run log |
| python-docx has limited formatting control | Complex formatting elements (blue accent colours, two-column layout) require careful template setup in Sprint 4 |

---

## Post-Launch Improvements (Future Sprints)

- **Outcome tracking:** Log interview/rejection/offer outcomes and feed back into scoring calibration
- **Pattern detection:** If the user skips 5+ Safe roles, auto-adjust to prioritise Stretch
- **Gap coaching:** Monthly summary of recurring gaps with concrete improvement suggestions
- **Notion linked databases:** Split into Jobs / Companies / Applications for richer filtering as volume grows
- **Near-relevant document generation:** Generate docs on request for near-relevant jobs the user flags as interesting

---

## Ready to Build — Start Order

1. **Right now:** the user sets up Apify, Notion, and Discord webhook; shares API keys
2. **Sprint 1:** Conduct Strategy Interview and build the career profile
3. **Sprints 2–8:** Claude builds each component; the user reviews milestone output before proceeding

Total estimated build time: 6–8 focused sessions.

---

*This plan will be updated at the end of each sprint to reflect what was learned.*
