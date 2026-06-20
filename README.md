# Leads Radar

A daily agent that scrapes job postings from LinkedIn, France Travail, HelloWork, and the open web, extracts ICP signals using Claude Haiku, and writes results to Google Sheets + a CSV committed to this repo.

**Runs every day at 06:00 UTC via GitHub Actions.**

---

## Setup in 3 steps

### Step 1 — Create a Google Sheet and a service account

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → create a project (or use an existing one).
2. Enable the **Google Sheets API** and **Google Drive API** for the project.
3. Create a **Service Account**: IAM & Admin → Service Accounts → Create.
4. On the service account, click **Keys → Add Key → Create new key → JSON**. A file downloads.
5. Open that JSON file — you'll need its **full contents** as a secret in step 2.
6. Create a new Google Sheet. Copy the long ID from its URL: `https://docs.google.com/spreadsheets/d/**THIS_PART**/edit`
7. Share that sheet with the service account's email address (looks like `xxx@yourproject.iam.gserviceaccount.com`). Give it **Editor** access.

---

### Step 2 — Add secrets to GitHub

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**.

Add these 5 secrets exactly as named:

| Secret name | Value |
|---|---|
| `APIFY_TOKEN` | Your Apify API token from [console.apify.com/account/integrations](https://console.apify.com/account/integrations) |
| `TAVILY_KEY` | Your Tavily API key from [app.tavily.com](https://app.tavily.com) |
| `ANTHROPIC_API_KEY` | Your Anthropic API key from [console.anthropic.com/keys](https://console.anthropic.com/keys) |
| `GOOGLE_SHEETS_CREDENTIALS` | The **full JSON content** of the service account key file from step 1 (paste the whole thing, one line) |
| `GOOGLE_SHEET_ID` | The Sheet ID from step 1 (the long string from the URL) |

---

### Step 3 — Push and check Actions

```bash
git add .
git commit -m "init: leads radar"
git push
```

Then go to **Actions tab** in your GitHub repo. You'll see the `Leads Radar` workflow. Click **Run workflow** to trigger it manually and verify it works before waiting for the daily schedule.

---

## Local testing (free, no API calls)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy the env template
cp .env.example .env
# (you don't need to fill in real values for DRY_RUN)

# 3. Run with fixture data
DRY_RUN=true python -m src.main
```

Output appears in `output/leads.csv`.

---

## Output

| Column | Description |
|---|---|
| `company_name` | Company hiring |
| `job_title` | Role being posted |
| `signal_type` | `treasury hire` / `payments hire` / `AI/fintech hire` |
| `location` | City / country |
| `posting_date` | Date of posting (YYYY-MM-DD) |
| `source` | `linkedin` / `france_travail` / `hellowork` / `tavily` |
| `url` | Link to the original posting |

---

## Cost estimate (per daily run)

| Service | Usage | Est. cost |
|---|---|---|
| Apify | ~180 actor calls/day (3 actors × ~60 results) | ~$0.10–0.30 |
| Tavily | 18 searches/day (6 queries × 3 results) | ~$0.02 |
| Claude Haiku | ~200 extractions × ~300 tokens | ~$0.02 |
| GitHub Actions | ~5 min/day on free tier | Free |
| **Total** | | **~$0.15–0.35/day** |
