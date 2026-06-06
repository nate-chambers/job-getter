# Job Finder TODO

This file is only for unfinished or follow-up work. Implemented features live in `README.md`.

## Credential-Gated Sources

- Resolve CareerOneStop account/API access:
  - `.env` now contains `CAREERONESTOP_USER_ID` and `CAREERONESTOP_API_TOKEN`.
  - The collector now uses the documented Jobs V2 path, JSON snippet params, and Bearer auth.
  - Current status: the token works for other CareerOneStop APIs, but Jobs V2 still returns `401 Unauthorized`.
  - Ask CareerOneStop to enable Jobs V2 / List Jobs access for this user id/token.
  - After auth works, flip `careeronestop-seattle` to `"enabled": true` in `sources.json`.

- Enable SerpAPI Google Jobs only if source discovery needs it:
  - Set `SERPAPI_API_KEY`.
  - Current status: key validated and source returned jobs.
  - Keep `serpapi-google-jobs-seattle` disabled unless explicitly running a Google Jobs/ATS discovery experiment.
  - Use sparingly because it may consume paid credits.

## Source Quality Audits

- Audit disabled no-key sources before enabling by default:
  - `remoteok-target-remote`
  - `weworkremotely-devops-rss`
  - `themuse-seattle`

- Run source-specific smoke tests when enabling each source:
  - `python .\job_finder.py sync --source SOURCE_NAME`
  - `python .\job_finder.py rescore`
  - `python .\job_finder.py audit --preset review --source SOURCE_NAME`

- Add more direct company sources after verifying public ATS endpoints:
  - F5
  - Cisco
  - Juniper
  - T-Mobile
  - Microsoft
  - Amazon
  - Expedia
  - Meta

- Investigate remaining ATS collectors only if useful source candidates appear:
  - Teamtailor
  - Jobvite
  - Rippling
  - Workday variants

## Matching Upgrades

- Add optional embedding-based matching behind a flag:
  - Keep current keyword and token similarity as the deterministic baseline.
  - Add a local embedding cache so rescoring does not recompute every posting.
  - Store model name/version in the DB or metadata so scores are auditable.

- Add resume-file-aware matching:
  - Parse the actual resume files in `resumes/`.
  - Compare each job against each resume version.
  - Recommend the best resume file in `packet JOB_ID`.

- Improve missing keyword analysis:
  - Separate required versus preferred skills when the posting text makes that clear.
  - Highlight only skills worth learning or emphasizing, not every missing tool.

## Workflow Upgrades

- Add manual contact/referral import:
  - CSV format: `name,company,email,linkedin,notes`.
  - Add `referral_possible` and `connection_notes` to jobs/reports.
  - Show contacts in `packet JOB_ID`.

- Add application history:
  - Store each status change as an event instead of only the current status.
  - Include date, status, reason, and notes.

- Add answer-bank categories for common application forms:
  - Work authorization.
  - Sponsorship.
  - Relocation.
  - Salary expectations.
  - Graduation date.
  - Start date.
  - Diversity questions where the preferred answer is usually opt-out.

- Add packet templates:
  - Infrastructure/network packet.
  - Solutions/sales engineering packet.
  - Cloud/support packet.

## Alerts And UI

- Add optional email/Slack/Discord alert delivery:
  - Keep local markdown summary as the default.
  - Send only apply-first jobs by default.
  - Include review jobs only when explicitly configured.

- Add a small local dashboard if the CLI/HTML reports stop being enough:
  - Queue view.
  - Review batches.
  - Source quality dashboard.
  - Application status board.
  - Duplicate/source cluster view.

## Research Parking Lot

- Optional broad-board importer:
  - JobSpy-style integration can be useful for manual experiments.
  - Keep disabled by default.
  - Do not let LinkedIn/Indeed/Glassdoor scraping pollute the daily apply-first queue.

- GitHub search terms for later source discovery:
  - `job scraper greenhouse lever ashby workday github`
  - `company career page scraper github`
  - `ATS job scraper github`
  - `job search automation resume matching github`
  - `direct company ATS scraper no browser github`
  - `semantic resume matching sentence transformers job description`
