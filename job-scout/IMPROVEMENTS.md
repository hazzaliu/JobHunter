# JobHunter Pipeline — Improvement Backlog

**Date:** 2026-04-07
**Status:** All items completed

---

## Priority 1 — Data Loss & Correctness (Things that are actively hurting results)

### 1.1 Consultancy filter blocking good PM roles
**Impact:** High — 24 jobs blocked last run (biggest single filter)
**Problem:** `is_blocked()` matches `"consult"` anywhere in the job description. A PM role at Xero that says "consulting with stakeholders" gets killed. Any JD with normal business language like "you'll consult cross-functionally" is filtered out.
**Fix:** Match "consult"/"consulting" on company name only, not full job description. Or use a smarter check — only block if the company itself is a consultancy.
**Effort:** Small (30 min)

### 1.2 Seen jobs persisted before pipeline completes
**Impact:** High — jobs permanently buried by transient failures
**Problem:** The deduplicator writes to `seen_jobs.json` at step 4. If the pipeline crashes at step 6 (API error, rate limit), those jobs are already marked "seen" and will never be re-evaluated. A single OpenRouter outage can permanently bury your best matches.
**Fix:** Defer `seen_jobs.json` write to after the full pipeline succeeds. Hold newly-seen IDs in memory until step 8 completes.
**Effort:** Small (30 min)

### 1.3 Stale profile embedding never auto-rebuilds
**Impact:** Medium — silent drift between your profile and the embedding
**Problem:** `profile_embedding.pkl` is cached forever. If you update your CV, cover letter, or strategy.json, the embedding still uses the old cached version. The pipeline gives no warning.
**Fix:** Hash the source files (CV + cover letter + strategy.json). Store the hash alongside the embedding. On load, recompute the hash and rebuild if it's changed.
**Effort:** Small (30 min)

### 1.4 Hiring manager research fabricates data
**Impact:** Medium — actively harmful if acted on
**Problem:** `find_hiring_manager()` asks the LLM to identify a hiring manager "based on your knowledge." The LLM cannot search LinkedIn — it will confidently hallucinate names, titles, and URLs from stale training data. You might message the wrong person.
**Fix:** Either remove this feature entirely, or replace with a real data source (e.g., LinkedIn API, or just flag "hiring manager research: manual lookup needed" with a suggested search query).
**Effort:** Small to remove, Medium to replace properly

---

## Priority 2 — Performance & Cost (Things that waste time and money)

### 2.1 Sequential LLM calls (biggest performance bottleneck)
**Impact:** High — pipeline takes ~15 min when it could take ~5 min
**Problem:** The 3 scoring agents run sequentially per job (`scorer.py:156-158`). For 5 jobs that's 15 serial API calls. Research adds another 20 serial calls (4 per job × 5). Total: ~35 sequential LLM calls.
**Fix:** Use `concurrent.futures.ThreadPoolExecutor` to run the 3 agents in parallel per job, and research calls in parallel across jobs.
**Effort:** Medium (1-2 hours)

### 2.2 Apify scraper runs 18 search terms sequentially
**Impact:** Medium — ~4.5 minutes of blocking scraper calls
**Problem:** Each `client.actor().call()` blocks until the Apify actor finishes. 18 terms × ~15 seconds = ~4.5 min. These are independent and could overlap.
**Fix:** Use threading or Apify's async API to run multiple search terms concurrently (batch of 3-4 at a time to respect rate limits).
**Effort:** Medium (1 hour)

### 2.3 No cost tracking
**Impact:** Medium — spending ~$90-150/month blindly
**Problem:** ~50 LLM calls per run × 30 days. No visibility into spend. No budget cap. No alert if a run burns more than expected.
**Fix:** Track token usage from OpenRouter responses. Log daily cost in the run log. Add a configurable daily budget cap that skips deep analysis if exceeded.
**Effort:** Medium (1 hour)

### 2.4 Search term dilution (18 terms, many irrelevant)
**Impact:** Medium — wasting Apify credits and adding noise
**Problem:** "Solutions Architect", "Data Engineer", "AI Engineer", "Innovation Manager" are roles you wouldn't take. They waste scraping credits, add noise, and consume LLM calls in level filtering. Last run: 30% of scraped jobs were pure noise.
**Fix:** Cut to 8-10 focused terms: Product Manager, Senior Product Manager, AI Product Manager, Digital Product Manager, Technical Product Manager, Data Analyst, Senior Data Analyst, Business Analyst, Analytics Manager, Data Scientist.
**Effort:** Tiny (5 min — just edit strategy.json)

---

## Priority 3 — Reliability & Robustness (Things that will break eventually)

### 3.1 No retry logic on API calls
**Impact:** Medium — a single 429/500 kills a job's analysis
**Problem:** Every LLM call (`run_agent`, `call_llm`, `check_level_alignment`) has no retry. OpenRouter returns 429 (rate limit) or 500 (server error) occasionally. One failure = that job gets no analysis and an exception is caught silently.
**Fix:** Add a simple retry decorator (3 attempts, exponential backoff) on `run_agent()` and `call_llm()`.
**Effort:** Small (30 min)

### 3.2 `seen_jobs.json` grows unbounded
**Impact:** Low now, Medium over time
**Problem:** Every run appends entries. No cleanup, rotation, or max size. After 6 months of daily runs you'll have 5000+ entries. File load/save slows down, and if a write gets corrupted mid-save, you lose everything.
**Fix:** Cap at last 90 days of entries (trim on load). Write to a temp file first, then atomic rename.
**Effort:** Small (30 min)

### 3.3 `parse_classification()` is fragile
**Impact:** Low — occasional misclassification
**Problem:** Checks if "reach" appears anywhere in the Agent 2 response text. A response saying "reach out to the hiring manager" gets classified as Reach instead of Safe. The check order (Reach → Stretch → Safe) means Reach wins whenever the word appears.
**Fix:** Check only the `CLASSIFICATION:` line in the response, not the full text. Or require exact match after the header.
**Effort:** Small (15 min)

### 3.4 `posted_within_hours` loaded twice in scraper
**Impact:** Tiny — cosmetic, no functional bug
**Problem:** Lines 45 and 50 of `apify_scraper.py` both read `posted_within_hours`. The second one shadows the first. No bug (same value), but confusing.
**Fix:** Remove the duplicate.
**Effort:** Tiny (2 min)

---

## Priority 4 — Quality & Intelligence (Things that make the results smarter)

### 4.1 No feedback loop from your decisions
**Impact:** High over time — pipeline never learns
**Problem:** `feedback_log` in strategy.json is always empty. When you skip a job in Notion, mark one "Applied", or reject one — none of that information feeds back into scoring. The pipeline makes the same judgement mistakes every day.
**Fix:** Build a lightweight feedback mechanism: read Notion statuses nightly, log patterns (e.g., "user always skips Reach-classified jobs" or "user applied to 3 HealthTech roles in a row"), adjust scoring weights or search terms accordingly.
**Effort:** Large (4-6 hours for the full loop)

### 4.2 Reranker scores are too compressed
**Impact:** Medium — reranking helps order but doesn't discriminate strongly
**Problem:** Cross-encoder scores range 0.92-0.97 for all jobs. A PM role (0.971) vs a BDM role (0.967) — the spread is too narrow to meaningfully separate. The model (`ms-marco-MiniLM-L-12`) is trained on web search, not job matching.
**Fix:** Either fine-tune a cross-encoder on job matching data (expensive), or switch to a T5-based reranker (`rank-T5-flan`) which may generalise better to this domain. Alternatively, use the rerank scores as a tiebreaker rather than primary ordering.
**Effort:** Medium to evaluate alternatives, Large to fine-tune

### 4.3 Researcher website lookup mostly fails
**Impact:** Medium — company research section is usually empty
**Problem:** `search_company_website()` guesses URLs like `www.{companyslug}.com/about`. This fails for most companies. "Data & AI Talent Australia" → `dataaiitalentaustralia.com` (doesn't exist). Most results return "Research not available."
**Fix:** Use a search API (e.g., Google Custom Search, Serper, or Tavily) to find the actual company website, then fetch it. Much higher hit rate.
**Effort:** Medium (1-2 hours + API key setup)

### 4.4 Classification systems conflict
**Impact:** Low — confusing but not broken
**Problem:** The embedder classifies by similarity score (Safe ≥60). Agent 2 independently classifies by skills coverage. When deep analysis merges, you get "85/100 Safe" alongside "81/100 Stretch" for similar-quality roles. The two systems don't agree on what Safe/Stretch/Reach means.
**Fix:** After deep analysis, always use Agent 2's classification. Drop the embedder's classification for deep-analyzed jobs entirely.
**Effort:** Small (15 min)

---

## Summary Table

| # | Issue | Impact | Effort | Priority |
|---|---|---|---|---|
| 1.1 | Consultancy filter too aggressive | High | Small | P1 |
| 1.2 | Seen jobs persisted too early | High | Small | P1 |
| 1.3 | Stale embedding cache | Medium | Small | P1 |
| 1.4 | Hiring manager hallucination | Medium | Small | P1 |
| 2.1 | Sequential LLM calls | High | Medium | P2 |
| 2.2 | Sequential Apify calls | Medium | Medium | P2 |
| 2.3 | No cost tracking | Medium | Medium | P2 |
| 2.4 | Search term dilution | Medium | Tiny | P2 |
| 3.1 | No API retry logic | Medium | Small | P3 |
| 3.2 | Unbounded seen_jobs.json | Low→Med | Small | P3 |
| 3.3 | Fragile classification parsing | Low | Small | P3 |
| 3.4 | Duplicate posted_within_hours | Tiny | Tiny | P3 |
| 4.1 | No feedback loop | High | Large | P4 |
| 4.2 | Reranker score compression | Medium | Med-Large | P4 |
| 4.3 | Website lookup mostly fails | Medium | Medium | P4 |
| 4.4 | Classification system conflict | Low | Small | P4 |

---

## Implementation Status

All items completed on 2026-04-07.

| # | Issue | Status |
|---|---|---|
| 1.1 | Consultancy filter | Done — matches company name only |
| 1.2 | Seen jobs deferred | Done — commits after pipeline success |
| 1.3 | Stale embedding cache | Done — SHA-256 hash invalidation |
| 1.4 | Hiring manager hallucination | Done — replaced with search URL + outreach template |
| 2.1 | Parallel LLM calls | Done — ThreadPoolExecutor on agents + research |
| 2.2 | Parallel Apify calls | Done — 3 concurrent search terms |
| 2.3 | Cost tracking | Done — per-call token/USD tracking in logs |
| 2.4 | Search term cleanup | Done — cut from 18 to 10 terms |
| 3.1 | API retry logic | Done — 3 retries with exponential backoff |
| 3.2 | Unbounded seen_jobs | Done — capped at 2000, atomic writes |
| 3.3 | Classification parsing | Done — matches CLASSIFICATION: line explicitly |
| 3.4 | Duplicate config load | Done — removed shadowed variable |
| 4.1 | Feedback loop | Done — reads Notion statuses, generates insights, auto-blocks repeat-skipped companies |
| 4.2 | Reranker score compression | Done — composite scoring (40% rerank + 30% embed + 30% title match), spread 83.5 vs ~5 before |
| 4.3 | Website lookup | Done — DuckDuckGo search via ddgs package |
| 4.4 | Classification conflict | Done — score-based classification after deep analysis |
