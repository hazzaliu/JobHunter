# JobHunter

An AI-powered job hunting pipeline that scrapes LinkedIn daily, scores jobs against your profile using a multi-stage ranking system, and delivers the best matches to Notion and Discord.

Built for job seekers who are tired of manually scrolling through irrelevant listings. You configure your career profile once, and JobHunter runs daily to find, rank, research, and deliver the roles that actually match your trajectory.

## How it works

```
LinkedIn (Apify)
    │
    ▼
┌─────────────────────────┐
│  1. Scrape               │  10 search terms, mid-senior, full-time, past week
│  2. Dedup & Filter       │  Remove seen jobs, entry-level, blocked companies
│  3. Embed & Score        │  Cosine similarity against your CV (free, instant)
│  4. Level Filter         │  LLM checks seniority alignment (1 cheap call each)
│  5. Cross-encoder Rerank │  Full cross-attention reranking + composite scoring
│  6. Deep Analysis        │  3-agent panel: Seniority, Fit, Devil's Advocate
│  7. Research             │  Company info, role breakdown, interview prep
│  8. Deliver              │  Notion database + Discord notification
│  9. Feedback Loop        │  Learns from your Applied/Skipped decisions
└─────────────────────────┘
```

## Scoring pipeline

JobHunter uses a 3-stage scoring pipeline to separate signal from noise:

**Stage 1 — Embedding retrieval (all jobs, free)**
Cosine similarity between your profile (CV + strategy) and each job description using `all-MiniLM-L6-v2`. Fast, free, runs on CPU. Scores are rescaled from the model's natural 0.25-0.75 range to 0-100.

**Stage 2 — Cross-encoder reranking (top 20, free)**
FlashRank's `ms-marco-MiniLM-L-12-v2` cross-encoder re-scores the top 20 with full cross-attention. Combined with embedding score and title-match boost into a composite score (40% rerank + 30% embed + 30% title match).

**Stage 3 — 3-agent deep analysis (top 5, ~$0.50)**
Three LLM agents evaluate each job independently, then scores are summed:

| Agent | Points | Evaluates |
|---|---|---|
| Seniority & Culture | /33 | Level fit, company culture, growth potential |
| Fit & Opportunity | /33 | Skills coverage, experience match, career value |
| Devil's Advocate | /34 | Honest risks, red flags, mitigation strategies |

## Setup

### Prerequisites

- Python 3.10+
- API keys for: [OpenRouter](https://openrouter.ai), [Apify](https://apify.com), [Notion](https://notion.so), [Discord](https://discord.com) (webhook)

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/JobHunter.git
cd JobHunter/job-scout
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp .env.example .env
# Edit .env with your API keys
```

| Key | Where to get it |
|---|---|
| `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `APIFY_API_TOKEN` | [console.apify.com/account/integrations](https://console.apify.com/account/integrations) |
| `NOTION_TOKEN` | [notion.so/my-integrations](https://www.notion.so/my-integrations) |
| `NOTION_DATABASE_ID` | From your Notion database URL (the 32-char hex string) |
| `DISCORD_WEBHOOK_URL` | Server Settings → Integrations → Webhooks |

### 3. Set up config

```bash
cp config.example.json config.json
# No changes needed — secrets come from .env
```

### 4. Create your career strategy

This is the most important step. Your strategy file tells the pipeline who you are, what you want, and how to evaluate roles.

```bash
cp strategy.example.json strategy.json
# Edit strategy.json with your career profile
```

Key sections to fill in:
- **`positioning_statement`** — Your 2-3 sentence professional identity
- **`target_titles`** — The job titles you're targeting
- **`selling_points`** — Your top 3-5 achievements with metrics
- **`known_gaps`** — Gaps in your experience and how to mitigate them
- **`search_configuration.search_terms`** — LinkedIn search queries (8-10 recommended)
- **`search_configuration.location`** — Your target city

### 5. Add your CV

Place your resume and (optionally) cover letter as PDFs in the `private_docs/` directory:

```bash
mkdir -p private_docs
cp ~/path/to/your/resume.pdf private_docs/YourName_Resume.pdf
cp ~/path/to/your/cover_letter.pdf private_docs/YourName_CoverLetter.pdf
```

File naming matters — the pipeline looks for "resume" or "cv" in the filename for the resume, and "cover" for the cover letter.

### 6. Set up Notion database

Create a new Notion database with these properties:

| Property | Type |
|---|---|
| Job Title | Title |
| Company | Text |
| Fit Score | Number |
| Classification | Select (`Safe`, `Stretch`, `Reach`) |
| Status | Select (`New`, `Applied`, `Skipped`, `Near-Relevant`) |
| Date Surfaced | Date |
| Strongest Area | Text |
| Weakest Area | Text |
| Strongest Strategy | Text |
| Weakest Strategy | Text |
| Job URL | URL |

Then share the database with your Notion integration (click "..." → "Connect to" → your integration name).

### 7. Run it

```bash
cd job-scout
python run_daily_scout.py
```

First run will:
- Download the embedding model (~90MB, cached after first use)
- Download the reranker model (~34MB, cached)
- Build your profile embedding from CV + strategy
- Scrape, score, analyse, and deliver results

### 8. Schedule daily runs (optional)

**macOS (launchd):**
```bash
# Create the plist — edit the Python path and project path to match your system
cat > ~/Library/LaunchAgents/com.jobscout.daily.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.jobscout.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/JobHunter/job-scout/run_daily_scout.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/JobHunter/job-scout</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/path/to/JobHunter/job-scout/logs/launchd_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/JobHunter/job-scout/logs/launchd_stderr.log</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.jobscout.daily.plist
```

**Linux (cron):**
```bash
crontab -e
# Add: 0 8 * * * cd /path/to/JobHunter/job-scout && python3 run_daily_scout.py >> logs/cron.log 2>&1
```

## Feedback loop

JobHunter learns from your decisions. When you change a job's status in Notion:

- **Applied** — the pipeline learns what score ranges and role types you actually pursue
- **Skipped** — companies skipped 3+ times are auto-blocked in future runs

The feedback system runs at the start of each daily run and saves insights to `logs/feedback_YYYY-MM-DD.json`.

## Cost

Approximate cost per daily run with 5 deep-analysed jobs:

| Component | Calls | Cost |
|---|---|---|
| Level filter | ~15 | ~$0.15 |
| Deep analysis (3 agents × 5 jobs) | 15 | ~$0.50 |
| Research (4 calls × 5 jobs) | 20 | ~$0.30 |
| **Total per run** | **~50** | **~$1.00** |
| **Monthly (daily runs)** | | **~$30** |

Embedding and cross-encoder reranking are free (local models on CPU).

Each run logs token usage and estimated cost in the run log.

## Project structure

```
JobHunter/
├── job-scout/
│   ├── run_daily_scout.py          # Main orchestrator
│   ├── config.json                 # Runtime config (from config.example.json)
│   ├── strategy.json               # Your career profile (from strategy.example.json)
│   ├── .env                        # API keys (from .env.example)
│   ├── seen_jobs.json              # Dedup log (auto-generated)
│   ├── requirements.txt            # Python dependencies
│   │
│   ├── scripts/
│   │   ├── apify_scraper.py        # LinkedIn scraping via Apify
│   │   ├── deduplicator.py         # Dedup + pre-filtering
│   │   ├── embedder.py             # Vector similarity scoring
│   │   ├── level_filter.py         # LLM seniority check
│   │   ├── reranker.py             # Cross-encoder reranking + composite scoring
│   │   ├── scorer.py               # 3-agent deep analysis panel
│   │   ├── researcher.py           # Company research + interview prep
│   │   ├── feedback.py             # Notion feedback loop
│   │   ├── notion_writer.py        # Write results to Notion
│   │   └── discord_notify.py       # Discord notifications
│   │
│   ├── prompts/                    # Agent system prompts (editable)
│   │   ├── agent1_seniority.txt
│   │   ├── agent2_fit.txt
│   │   └── agent3_devils.txt
│   │
│   ├── private_docs/               # Your CV and cover letter (gitignored)
│   ├── embeddings/                 # Cached model files (gitignored)
│   └── logs/                       # Run logs and feedback (gitignored)
│
├── .gitignore
└── README.md
```

## Customisation

**Scoring agents** — Edit the prompt files in `prompts/` to change how jobs are evaluated. Each agent has a clear scoring rubric you can adjust.

**Search terms** — Edit `search_configuration.search_terms` in `strategy.json`. Keep to 8-10 focused terms. More terms = more Apify credits burned for diminishing returns.

**Seniority range** — The level filter in `level_filter.py` is set for ~4 years experience. Adjust the prompt's experience ranges to match yours.

**LLM model** — Change `OPENROUTER_MODEL` in `.env`. Default is `anthropic/claude-sonnet-4` via OpenRouter. Any OpenAI-compatible model works.

**Composite scoring weights** — In `reranker.py`, the composite is `0.4 * rerank + 0.3 * embed + 0.3 * title_match`. Adjust if you want to weight differently.

## License

MIT
