# Job Getter

Local job-search engine that pulls public/API job sources, stores listings in SQLite, scores them against your resume and preferences, and generates a sortable HTML dashboard.

Repo target: [nate-chambers/job-getter](https://github.com/nate-chambers/job-getter)

## What It Does

Job Getter turns messy job boards and ATS feeds into a local apply-first queue.

- Pulls jobs from sources such as Newgrad/Jobright public pages, Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Adzuna, USAJOBS, FindWork, Remotive, Jobicy, RSS feeds, and GitHub job lists.
- Stores job history in `jobs.sqlite3`, so reruns can track new, changed, unchanged, and missing jobs.
- Scores each job for role fit, location fit, resume match, experience level, salary, interview difficulty, source quality, freshness, and concerns.
- Exports `jobs.csv`, `jobs_review.csv`, `jobs.html`, and `jobs_review.html`.
- Generates a local HTML dashboard with search, filters, sorting, card/table view, and local saved picks.

## Quick Start

```powershell
git clone https://github.com/nate-chambers/job-getter.git
cd job-getter
```

Put your resumes in `resumes/` as Markdown files if possible:

```text
resumes/software_engineering_resume.md
resumes/infrastructure_resume.md
resumes/solutions_engineer_resume.md
```

Copy the env template:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and add only the API keys you have. Then run:

```powershell
python .\job_finder.py sync
python .\job_finder.py queue --limit 50
python .\job_finder.py report --output jobs.html --title "Apply-First Job Queue"
```

Open `jobs.html` in your browser.

## Customize The Search

The main customization file is:

```text
job_getter.toml
```

Edit it for:

- Target cities and regions
- Remote rules
- Target role buckets
- Experience limits
- Resume-match threshold
- Interview-difficulty threshold
- Skills and strengths that should score higher
- Negative terms that should score lower
- Preferred companies

There is an example for a friend who wants SWE and adjacent roles around Portland:

```text
examples/portland_swe.toml
```

Use it like this:

```powershell
python .\job_finder.py queue --config examples\portland_swe.toml --limit 50
python .\job_finder.py report --config examples\portland_swe.toml --output jobs.html --title "Portland SWE Jobs"
```

You should also edit `sources.json` so the enabled sources and search queries match the new person. For Portland SWE, change broad API queries toward:

- `software engineer`
- `backend engineer`
- `frontend engineer`
- `full stack engineer`
- `platform engineer`
- `cloud engineer`
- `devops engineer`
- `site reliability engineer`
- `junior software engineer`
- `new grad software engineer`

## Customize With Codex Or Claude Code

Open this repo in Codex or Claude Code and use [CUSTOMIZATION_PROMPT.md](CUSTOMIZATION_PROMPT.md).

The assistant should:

1. Read `README.md`, `job_getter.toml`, `sources.json`, and the Markdown resumes in `resumes/`.
2. Ask what jobs, areas, experience range, skills, exclusions, and companies matter.
3. Edit `job_getter.toml` and `sources.json`.
4. Keep API keys in `.env`, never in source files.
5. Run tests.
6. Regenerate the local reports.

For example:

```text
My friend wants SWE and adjacent jobs around Portland, Oregon. Read the resumes, ask me what you need, and customize this repo for them.
```

The TOML route is better than hardcoding because you can keep one engine and swap search profiles without rewriting the Python.

## API Keys

The tool works best with a mix of free/public sources and credentialed APIs. You do not need every key.

Create `.env` from `.env.example`:

```powershell
Copy-Item .env.example .env
```

Fill in values like:

```text
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
USAJOBS_EMAIL=
USAJOBS_API_KEY=
FINDWORK_API_KEY=
CAREERONESTOP_USER_ID=
CAREERONESTOP_API_TOKEN=
SERPAPI_API_KEY=
```

Do not commit `.env`.

### Adzuna

1. Go to [Adzuna Developer](https://developer.adzuna.com/).
2. Create an account.
3. Create or use the default application.
4. Copy the Application ID into `ADZUNA_APP_ID`.
5. Copy the Application Key into `ADZUNA_APP_KEY`.

### USAJOBS

1. Go to [USAJOBS Developer](https://developer.usajobs.gov/).
2. Request an API key.
3. Use your registered email for `USAJOBS_EMAIL`.
4. Put the key in `USAJOBS_API_KEY`.

### FindWork

1. Go to [FindWork API](https://findwork.dev/developers/).
2. Create an account or API token.
3. Put the token in `FINDWORK_API_KEY`.

### CareerOneStop

1. Go to [CareerOneStop Web API](https://www.careeronestop.org/Developers/WebAPI/technical-information.aspx).
2. Request Web API access.
3. Put your user ID in `CAREERONESTOP_USER_ID`.
4. Put your token in `CAREERONESTOP_API_TOKEN`.

Note: CareerOneStop may need to enable specific API services for your token. If Jobs V2 returns `401 Unauthorized` but another CareerOneStop endpoint works, ask them to enable Jobs V2 / List Jobs access for your user ID.

### SerpAPI

1. Go to [SerpAPI Google Jobs API](https://serpapi.com/google-jobs-api).
2. Create an account.
3. Put the key in `SERPAPI_API_KEY`.

SerpAPI can consume paid credits. It is disabled by default.

## Commands

```powershell
python .\job_finder.py sync
python .\job_finder.py queue --limit 50
python .\job_finder.py search --query "linux networking"
python .\job_finder.py show 123
python .\job_finder.py mark 123 --status saved --notes "Good role"
python .\job_finder.py export --output jobs.csv
python .\job_finder.py export --preset review --output jobs_review.csv
python .\job_finder.py report --output jobs.html --title "Apply-First Job Queue"
python .\job_finder.py report --preset review --output jobs_review.html --title "Review Queue"
python .\job_finder.py packet 123
python .\job_finder.py rescore
```

Use a different TOML profile:

```powershell
python .\job_finder.py sync --config examples\portland_swe.toml
python .\job_finder.py report --config examples\portland_swe.toml --output jobs.html --title "Portland SWE Jobs"
```

Source-specific smoke test:

```powershell
python .\job_finder.py sync --source adzuna-seattle
```

Disabled-source test:

```powershell
python .\job_finder.py sync --source careeronestop-seattle --include-disabled
```

## Update Rules

- New jobs get `first_seen_at` and `last_seen_at`.
- Existing unchanged jobs update `last_seen_at`.
- Existing changed jobs update fields and `last_changed_at`.
- Missing jobs increment `missing_count`; after two missed syncs they are marked inactive.
- The main queue hides listings older than 30 days by default. Use `--include-stale` to show older stored jobs.
- Jobs without a source posted date use `first_seen_at` for freshness.

## Apply-First Rules

The default apply queue is intentionally strict.

For the default Nate config, `jobs.csv` and `jobs.html` require:

- Target role bucket
- Target location
- Resume match at or above the TOML threshold
- Score at or above the TOML threshold
- Interview difficulty at or below the TOML threshold
- Experience requirement at or below the TOML threshold
- No hard-excluded titles

For a different person, edit `job_getter.toml`; if SWE is listed in `roles.target_buckets`, SWE can appear in apply-first results.

## Source Types

Supported source types:

- `newgrad`
- `greenhouse`
- `lever`
- `ashby`
- `smartrecruiters`
- `workable`
- `recruitee`
- `bamboohr`
- `workday`
- `usajobs`
- `remotive`
- `arbeitnow`
- `jobicy`
- `remoteok`
- `rss`
- `themuse`
- `findwork`
- `careeronestop`
- `serpapi_google_jobs`
- `github_markdown`
- `adzuna`

The Newgrad collector parses public rendered pages only. It does not call Jobright private/internal pagination APIs.

## Tests

```powershell
python -m unittest tests.test_job_finder
python -m py_compile .\job_finder.py .\newgrad_jobs.py
```

## Local Files Not In Git

These are ignored because they are private or generated:

- `.env`
- `jobs.sqlite3`
- `jobs.csv`
- `jobs.html`
- `jobs_review.csv`
- `jobs_review.html`
- `applications/`
- `alerts/`
- `answer_bank.json`
- personal Markdown resumes in `resumes/`
