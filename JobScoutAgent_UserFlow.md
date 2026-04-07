# Job Scout Agent — User Flow Specification

> **Note:** This is the original product specification used to develop JobHunter. Example data references a sample candidate profile. Replace with your own details when customising.

**[Your Name] | Product Manager & AI Specialist**
*Version 1.0 — March 2026*

---

## Document Purpose

This document maps the complete user flow for a personal AI-powered job scouting agent. It defines every phase of the system — from one-time onboarding through daily scouting, multi-agent scoring, document generation, Notion delivery, and ongoing strategy evolution. It is intended to serve as the product specification before any build begins.

---

## System Architecture at a Glance

```
[Apify Scraper] → [Deduplication Filter] → [3-Agent Scoring Panel]
      → [Threshold Gate] → [Document Generation Engine]
      → [Company Research Module] → [Notion Database]
      → [Discord Notification] → [User Review & Feedback]
      → [Strategy Memory Layer]
```

**Core integrations:** Apify (scraping), Notion (database), Discord (notifications + interaction hub), Claude (all AI reasoning), Word (document output)

---

## Phase 0 — Initial Setup & Onboarding (One-time)

**Trigger:** First time the system is activated.

### 0.1 Document Upload
the user uploads:
- Base resume (master copy)
- All tailored resume variants
- All cover letter variants

The system reads and indexes these to extract: experience history, quantified achievements, skill vocabulary, tone and voice, and the six customisation levers (role subtitle, summary, key achievements, role bullets, skills section, cover letter paragraphs).

**Template locked:** The system preserves the existing two-column resume layout and 4-paragraph cover letter structure. It does not redesign — it tailors within the established template.

### 0.2 Strategy Interview
The system conducts a structured interview to build the user's Career Strategy Profile. Questions cover:

- **Career direction** — Where do you want to be in 2–3 years? What kind of work energises you?
- **Role preferences** — What titles or functions are you targeting? What are you open to vs. closed to?
- **Industry preferences** — Any sectors you're excited about? Any you want to avoid entirely?
- **Company culture** — What does a great team environment look like to you? What have you left behind and why?
- **Stretch vs. safe** — How much risk appetite do you have right now? More stability or more ambition?
- **Non-negotiables** — Salary floor, remote/hybrid preferences, team size, company stage (startup vs. enterprise)?
- **Career inspiration** — Who's doing work you admire? What companies do you respect?

**Output:** A structured Career Strategy Profile stored in memory, used to calibrate all future scoring and filtering.

### 0.3 Strategy Document Generated
The system produces a written strategy document summarising:
- the user's positioning statement (how to frame his unique combination of AI + product + data)
- Target role clusters with rationale
- Key selling points to lead with across all applications
- Known gaps and a mitigation strategy for each

This document is stored in Notion and updated whenever the user gives feedback that materially changes the strategy.

**Edge case — incomplete answers:** If the user skips questions or gives vague answers, the system flags those gaps and asks follow-ups before proceeding. The strategy is not locked until the user confirms it.

---

## Phase 1 — Daily Scout (Automated, runs each morning)

**Trigger:** Scheduled daily run (recommended: 7:00 AM local time).

### 1.1 Apify Scrape
The system calls the Apify LinkedIn Jobs Scraper with the following parameters:
- **Location:** Melbourne, Australia (strict)
- **Search terms:** Derived from the user's target role clusters (e.g., "Product Manager AI", "AI Product Lead", "Data Product Manager", etc.) — list maintained and updated based on feedback
- **Filters:** Full-time, hybrid/remote-friendly (per the user's preferences), posted within last 24–48 hours

Raw job listings are returned as structured data: title, company, description, requirements, seniority level, posted date, job URL.

### 1.2 Deduplication Filter
Before any scoring occurs, each job listing is checked against the **Seen Jobs Log** — a persistent database of every job ID previously surfaced or applied for across all sessions.

- Jobs already seen → silently discarded
- Jobs the user marked as "not interested" → silently discarded
- Jobs from blocked industries/companies → silently discarded
- New jobs only proceed to scoring

**Edge case — all results are duplicates:** If every scraped result has been seen before, the system widens the search window to 72 hours and retries once. If still no new results, it sends a short Discord message: *"Nothing new today — all results have been seen before. I'll try again tomorrow."*

### 1.3 Quick Pre-Filter
Before engaging the full scoring agents, a lightweight pre-filter removes obvious mismatches:
- Roles outside Melbourne (if location is wrong in the data)
- Roles clearly below experience level (e.g., graduate/entry-level when the user is mid-senior)
- Roles with a company or industry on the blocked list

Remaining jobs proceed to the scoring panel.

---

## Phase 2 — Multi-Agent Scoring Panel

Each qualifying job is evaluated by three specialised agents working in parallel. Their outputs are then synthesised into a single consensus Fit Score.

### Agent 1 — Seniority & Culture Analyst
**Question:** Does this role match the user's level, and does the company feel right?

Evaluates:
- Seniority alignment: Does the title and responsibility level match the user's current trajectory (mid-senior PM/Data)?
- Years of experience required vs. the user's profile
- Company size and stage (startup, scaleup, enterprise) against the user's stated preferences
- Culture signals extracted from the job description (pace, values language, team structure)
- Industry fit against the user's interests and exclusions

**Output:** Score out of 33 + brief rationale

### Agent 2 — Fit & Opportunity Classifier
**Question:** Is this a safe bet, a stretch, or a reach?

Evaluates:
- Skills match: What % of the listed requirements does the user demonstrably have?
- Experience match: Has the user done comparable work at comparable scale?
- Classifies the role as one of three types:
  - **Safe** — the user meets most requirements; high probability of interview
  - **Stretch** — the user meets ~60–75% of requirements; achievable with strong positioning
  - **Reach** — the user meets <60% of requirements; unlikely without significant gap-filling

**Output:** Score out of 33 + classification label (Safe / Stretch / Reach) + brief rationale

### Agent 3 — Devil's Advocate
**Question:** What's the honest case against applying?

Evaluates:
- What's missing from the user's profile relative to the job description?
- Are there red flags in the role (e.g., unclear scope, requires specific domain the user lacks)?
- Is there a risk of underlevelling (role is too junior) or overlevelling (the user is overqualified and likely to leave)?
- What would a sceptical recruiter say when reviewing the user's resume against this JD?

**Output:** Score out of 34 + critical observations + honest risk flag

### Consensus Fit Score
The three agent scores are summed to produce a **Fit Score out of 100**.

The score is reported alongside:
- **Strongest dimension** (which agent scored highest and why) → strategy: lead with this in the application
- **Weakest dimension** (which agent scored lowest and why) → strategy: how to cope, reframe, or avoid it
- **Role classification** (Safe / Stretch / Reach) from Agent 2
- **Overall recommendation** (one sentence: why this role is or isn't worth pursuing)

---

## Phase 3 — Threshold Gate & Selection

### 3.1 Scoring Threshold
**Minimum score to qualify for the Top 3: 60/100**

- Score ≥ 60 → Qualifies for Top 3 consideration
- Score 40–59 → Near-relevant; included in the full list with explanation
- Score < 40 → Excluded from both lists; silently logged

### 3.2 Top 3 Selection Logic
From qualifying jobs (≥60), the system selects the three highest-scoring roles as the primary recommendations.

**Tiebreaker:** If multiple roles have identical scores, prefer: (1) Stretch over Safe (more growth value), (2) more recently posted, (3) larger/more recognised company.

**Edge case — fewer than 3 qualifying jobs:**
- If 1–2 jobs qualify: Surface those plus the highest-scoring near-relevant jobs to fill the report, clearly labelled as "Near-Relevant — below threshold."
- If 0 jobs qualify: Surface the top 3 near-relevant results. The daily report leads with: *"No strong matches today. Here's what came closest and why they didn't qualify."* The system asks for the user's input: *"Should I lower the threshold temporarily, or broaden the search terms?"*

### 3.3 Full List
All jobs that passed the pre-filter (regardless of score) are logged in the Notion database in a "Full Pipeline" view. This gives the user visibility across the entire market, not just the curated top 3.

---

## Phase 4 — Document Generation

Triggered for each of the Top 3 roles.

### 4.1 Context Check (Pre-Generation)
Before generating documents, the system scans the job description for any requirements that cannot be confidently addressed with the user's known experience. If gaps are found, the system **pauses** and sends a Discord message:

> *"Before I generate your resume and cover letter for [Role] at [Company], I have a quick question: [specific gap]. For example: 'Do you have experience with [X technology / domain]? If so, could you describe it briefly so I can reflect it accurately?'"*

The system waits for the user's reply before continuing. the user's response is stored in the experience memory layer for future use.

**Edge case — multiple gaps in one role:** The system batches all questions into a single Discord message rather than sending them one by one, to minimise interruptions.

### 4.2 Resume Tailoring
The system generates a tailored .docx resume using the user's base template. The six customisation levers are applied:

1. **Role subtitle** — Updated to match the target title (e.g., "Senior Product Manager | AI Platforms")
2. **Summary block** — Rewritten to align the user's positioning with the specific role and company context
3. **Key achievements** — Three most relevant achievements selected and refined with job-specific language
4. **Role bullets** — Bullet points across each position rewritten to surface the experience most relevant to this JD; existing content reframed, not invented
5. **Technical skills section** — Categories renamed and content reordered to front-load the skills the JD prioritises
6. **Tone** — Adjusted based on company culture signals (e.g., more technical for a startup, more governance-focused for enterprise/regulated industries)

**Constraint:** The system emphasises existing experience. It does not fabricate experience the user does not have. If a skill is missing, it is either omitted or addressed through honest adjacent framing.

### 4.3 Cover Letter Tailoring
The system generates a tailored .docx cover letter using the user's established 4-paragraph structure:

- **Para 1:** Role title + company name + the user's core positioning relevant to this role
- **Para 2:** Specific experience (Forethought, SurveyForge, synthetic data) reframed for this JD with relevant metrics
- **Para 3:** Secondary experience or MBS fellowship work, tied to the role's specific requirements
- **Para 4:** Why this company specifically — draws from company research (Phase 5) to reference values, mission, or products authentically
- **Closing:** Warm, consistent sign-off ("Warm regards, [Your Name]")

### 4.4 Application Advice Note
Alongside the documents, the system generates a short advisory note for each role covering:
- The strongest angle to lead with in interviews
- The weakest area and how to address it if raised
- Suggested tone and approach for this company's culture
- Any specific preparation the user should do before applying (e.g., "research their recent product launch")

---

## Phase 5 — Company & Role Research

Runs in parallel with document generation for each of the Top 3 roles.

### 5.1 Company Research
The system searches the company's public website and publicly accessible LinkedIn page to compile:
- Company overview (mission, products, size, stage)
- Recent news, product launches, or strategic announcements
- Values and culture signals
- Engineering/product team structure (if visible)
- Any known challenges or growth areas

### 5.2 Role Research
From the job listing and company website:
- Full breakdown of requirements (must-have vs. nice-to-have)
- Inferred team structure and reporting line
- Application process steps (if listed on the careers page)
- Any stated interview format or assessment process

### 5.3 Hiring Manager Research (Nice-to-Have)
The system searches LinkedIn public profiles for the likely hiring manager or a relevant adjacent person (e.g., Head of Product, CPO, direct team lead) at the company.

**Scope:** Public LinkedIn profiles only. No Apollo, Hunter.io, or any paid enrichment tools.

**Deliverable:** Name, title, LinkedIn URL, and a brief note on their background — formatted as a conversation starter draft (InMessage or email) that the user can personalise and send himself.

The system does not send anything on the user's behalf.

### 5.4 Interview Preparation
Based on the role description and company research, the system generates:
- 8–12 likely interview questions tailored to this specific role
- Suggested answer angles for each question, drawing on the user's actual experience

---

## Phase 6 — Notion Database Population

Each Top 3 role gets a dedicated row in the user's Notion master database. Each row contains:

| Field | Content |
|---|---|
| Job Title | Role name |
| Company | Company name |
| Fit Score | Numerical score + Safe/Stretch/Reach label |
| Status | New / Reviewing / Applied / Rejected / Interviewing |
| Date Surfaced | Automated timestamp |
| Strongest Fit Area | From Agent 1/2/3 output |
| Weakest Fit Area | From Agent 3 output |
| Tailored Resume | Linked .docx file |
| Cover Letter | Linked .docx file |
| Application Advice | Inline text note |
| Company Research | Inline structured summary |
| Interview Questions | Inline list |
| Application Process | Inline steps from company careers page |
| Hiring Manager | Name + LinkedIn URL + draft outreach (if found) |
| Job URL | Direct link to listing |
| Notes | the user's personal notes field |

Near-relevant jobs (below threshold) are also logged in a separate "Pipeline" view with score and brief explanation, but without full document generation.

---

## Phase 7 — Notification & Daily Report

### 7.1 Discord Notification
Once Notion is populated and documents are ready, the system sends a structured message to the user's Discord channel:

---
**🔍 Daily Job Scout — [Date]**

**Top 3 Matches**
1. [Role] at [Company] — Score: 84/100 (Stretch) — *"Strong AI product fit; compliance experience is the gap."*
2. [Role] at [Company] — Score: 76/100 (Safe) — *"High likelihood of interview; leadership scope is lighter than ideal."*
3. [Role] at [Company] — Score: 71/100 (Stretch) — *"Good cultural fit; missing enterprise healthcare domain knowledge."*

Documents, research, and interview prep are ready in Notion → [link]

**Near-Relevant (below threshold)**
- [Role] at [Company] — Score: 52/100 — Didn't qualify: missing 3 core requirements. Advice included.

*Reply here to give feedback, skip an industry, or ask questions.*

---

### 7.2 Low-Match Days
If fewer than 3 roles cleared the threshold, the report clearly explains why, what the near-relevant roles were missing, and offers a concrete suggestion: *"You could close this gap by [specific action]. Want me to adjust search terms or lower the threshold temporarily?"*

---

## Phase 8 — User Review & Application

the user reviews the Notion database at his own pace. He can:
- Read the company research and fit score breakdown
- Review and edit the tailored resume and cover letter
- Check the interview questions and application process steps
- Reach out to the hiring manager using the drafted outreach as a starting point
- Apply directly via the job listing link
- Update the Status field as he progresses (Reviewing → Applied → Interviewing, etc.)

The system does not apply on the user's behalf.

---

## Phase 9 — Feedback & Strategy Evolution

### 9.1 Giving Feedback
the user gives feedback conversationally via Discord or by telling Claude directly. Examples:

- *"Skip fintech roles going forward"*
- *"I've been skipping all the roles that are too operations-heavy — adjust the filter"*
- *"That Medibank-type role was interesting. Find more like that."*
- *"Lower the threshold to 55 this week, I want more options"*

The system acknowledges, applies the change immediately, and logs the preference update in the Strategy Profile.

### 9.2 Implicit Learning
The system also tracks implicit signals:
- If the user consistently ignores Safe-rated roles, it adjusts to prioritise Stretch roles
- If the user applies to roles in a certain industry more than others, it notes the pattern
- If the user's feedback on near-relevant roles reveals a consistent gap, the system proactively suggests: *"You might want to build experience in [X] — this has come up in 5 rejected roles this month."*

### 9.3 Strategy Profile Updates
The Career Strategy Profile is a living document. It is updated whenever:
- the user explicitly instructs a change
- The system detects a pattern that suggests a preference shift
- A new industry, company type, or role type is added or removed

the user can review the current strategy at any time by asking. The system confirms any material change before applying it.

---

## Edge Case Summary

| Scenario | System Response |
|---|---|
| All scraped results are duplicates | Widens search window; if still nothing, sends brief Discord note |
| Fewer than 3 qualifying jobs (≥60) | Surfaces qualified jobs + near-relevant to fill report; asks for guidance |
| Zero qualifying jobs | Surfaces top 3 near-relevant; explains gaps; asks whether to lower threshold |
| Missing information before doc generation | Pauses; sends Discord message asking for specific context; waits for reply |
| the user's reply adds new experience | Stores in memory layer; uses in all future document generation |
| the user wants to block an industry | Applied immediately; logged in Strategy Profile; confirmed via Discord |
| No new jobs (market quiet) | Short Discord note; no report generated; retries next day |
| Hiring manager not findable | Field left blank in Notion; no outreach draft generated |
| the user asks to adjust fit threshold | Applied for next run; can be made permanent or temporary |

---

## Document & Template Standards

**Resume:** Two-column layout. Role subtitle in blue. Company names in blue. Right-column panel for summary, key achievements, certifications. Bullet points within roles. Based on the user's existing Forethought/base resume structure.

**Cover Letter:** Matching header style. 4–5 paragraphs. Opens with role + company + positioning. Second paragraph covers Forethought experience + metrics. Third covers secondary experience or MBS. Fourth covers company-specific fit. Closes warmly. Sign-off: *"Warm regards, [Your Name]."*

**Tone:** Professional, warm, data-driven. Uses specific numbers and outcomes. Avoids vague claims. Adapts technical depth to company type.

---

## Open Questions for Future Sprints

- Should the system track application outcomes (interview / rejection / offer) to improve future scoring calibration?
- Should near-relevant jobs ever trigger partial document generation (e.g., cover letter only) if the user requests it?
- Could the system suggest when to follow up on applications that have gone quiet?
- Should the Notion database eventually split into linked databases (Jobs / Companies / Applications) for richer filtering?

---

*This document will be updated as the system evolves through testing and feedback.*
