# Customization Prompt For Codex Or Claude Code

Use this prompt when adapting Job Getter for another person.

```text
You are customizing this local job-search repo for me.

First, read:
- README.md
- job_getter.toml
- sources.json
- every Markdown resume in resumes/

Then ask me concise questions before editing:
1. What job families do you want? Examples: SWE, infrastructure, network, systems, cloud, DevOps/SRE, security, solutions engineer, sales engineer, operations.
2. What cities, metro areas, states, or remote rules should count as target locations?
3. What experience range should count as realistic?
4. What skills, tools, projects, industries, or resume strengths should get extra points?
5. What titles or job types should be excluded by default?
6. What companies should get a preferred-company bonus?
7. Which API sources do I have credentials for?

After I answer:
- Edit job_getter.toml for my roles, locations, strengths, negative terms, companies, and apply thresholds.
- Edit sources.json so enabled sources and queries match my search.
- Do not put API keys in source files. Use .env only.
- Run tests.
- Run a local rescore/export/report from existing data if present.
- If I approve API usage, run source-specific syncs first before a full sync.

Example request:
"My friend wants SWE and adjacent jobs around Portland, Oregon."

For that person, start from examples/portland_swe.toml, then adjust sources.json queries toward:
- software engineer
- backend engineer
- frontend engineer
- full stack engineer
- platform engineer
- cloud engineer
- devops engineer
- site reliability engineer
- junior software engineer
- new grad software engineer
```
