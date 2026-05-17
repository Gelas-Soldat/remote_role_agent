# Remote Role Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-3DA639?style=for-the-badge)](LICENSE)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ryancreates-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/ryancreates)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Gelassoldat-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white)](https://ko-fi.com/gelassoldat)

A local AI assisted job search command center for finding remote roles in the United States.

The app searches configured job sources, filters for United States remote roles, scores fit against a target profile, saves results in SQLite, and gives you a local dashboard where you can save, shortlist, apply, reject, hide, review, and export jobs.

## Current version

V3.1

## What it does

1. Searches job APIs and public company boards.
2. Filters out non United States roles before scoring.
3. Scores roles against your configured target profile.
4. Saves roles locally in SQLite.
5. Shows a Flask dashboard at `http://127.0.0.1:5000`.
6. Lets you mark roles as saved, shortlisted, applied, rejected, or hidden.
7. Exports latest roles and shortlist CSV files.
8. Supports optional AI review per role.
9. Supports optional Telegram alerts for strong new matches.

## Sources

Supported sources:

1. Adzuna
2. USAJOBS
3. Greenhouse
4. Lever
5. Ashby
6. Workday, only when company specific board values are configured

Adzuna and USAJOBS need API keys. Greenhouse, Lever, and Ashby use public company boards. Workday varies by company and should stay disabled unless you know the tenant and site values.

## Quick start on Windows

```powershell
cd C:\Dev
Expand-Archive .\remote_role_agent_github.zip -DestinationPath .\remote_role_agent
cd .\remote_role_agent\remote_role_agent_github
.\run_windows.ps1
```

Then open:

```text
http://127.0.0.1:5000
```

## Quick start on Ubuntu or WSL

```bash
unzip remote_role_agent_github.zip -d remote_role_agent
cd remote_role_agent/remote_role_agent_github
chmod +x run_ubuntu.sh run_ubuntu_fetch_once.sh
./run_ubuntu.sh
```

## Environment setup

Copy `.env.example` to `.env`, then fill in only the keys you want to use.

```text
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
USAJOBS_API_KEY=
USAJOBS_USER_AGENT=your_email@example.com
USER_EMAIL=your_email@example.com
OPENAI_API_KEY=
AI_MODEL=gpt-4o-mini
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Do not commit `.env`. It is blocked by `.gitignore`.

## Running a fetch once

Windows:

```powershell
.\run_windows_fetch_once.ps1
```

Ubuntu or WSL:

```bash
./run_ubuntu_fetch_once.sh
```

## Dashboard actions

Each role can be moved through this workflow:

```text
new
saved
shortlisted
applied
rejected
hidden
```

Hidden roles do not appear in the normal dashboard view.

## Outputs

Generated files live in `output/` and are intentionally ignored by Git.

```text
output/jobs.sqlite
output/jobs_latest.csv
output/jobs_shortlist.csv
output/jobs_rejected_location_audit.csv
```

## Keep it light

The default config is intentionally conservative:

```json
{
  "max_workers_per_source": 1,
  "request_delay_seconds": 0.1,
  "workday_max_pages_per_board": 1,
  "workday_skip_details": true
}
```

Raise those only after the dashboard is running smoothly.

## Project structure

```text
app.py                     Flask dashboard and API routes
job_agent.py               Fetching, filtering, scoring, SQLite, exports
config.json                Search terms, sources, scoring profile, boards
.env.example               Safe environment variable template
requirements.txt           Python dependencies
templates/index.html       Dashboard HTML
static/styles.css          Dashboard styling
static/app.js              Dashboard behavior
docs/                      Setup, privacy, source, and roadmap notes
.github/workflows/         GitHub syntax check
```

## GitHub safety note

This repo is safe to upload as long as you do not add `.env`, `output/jobs.sqlite`, CSV exports, or API keys. The included `.gitignore` blocks those files by default.

## Support

Remote Role Agent is a public portfolio project and local first job search tool. If it helps you or gives you ideas for your own workflow, support is appreciated.

1. Star the repository.
2. Share feedback through GitHub Issues.
3. Support development through GitHub Sponsors, Buy Me a Coffee, or thanks.dev.

Funding links are configured in `.github/FUNDING.yml`, which lets GitHub show the Sponsor button on the repository page.

