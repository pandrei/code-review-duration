Here’s a polished, generic README.md you can drop in as-is 👇

# GitLab Merge Request Review Duration Reporter

This script exports **merged GitLab Merge Requests (MRs)** for selected repositories over a date range and calculates:

- **Raw time open** (wall-clock duration)
- **Business-hours time open**  
  (Mon–Fri, 09:00–17:00 UTC)

It produces:

1. A **detailed CSV** with one row per merged MR
2. A **per-project summary CSV** (counts, averages, percentiles, min/max)
3. A short overall statistics line printed to the console

The script supports both **interactive** and **non-interactive** usage.

---

##  Prerequisites

### 1. Python

- Python **3.9+** is recommended.

### 2. Dependencies

Install dependencies in a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install requests python-dateutil

3. GitLab Personal Access Token

You need a GitLab Personal Access Token with at least:
	•	read_api
	•	read_repository

Set it as an environment variable:

export GITLAB_TOKEN="your-token-here"

You can also pass it via --token.

⸻

🔧 Environment Variables

These are optional helpers; all can be overridden by CLI arguments.

Variable	Purpose	Example
GITLAB_TOKEN	GitLab Personal Access Token	export GITLAB_TOKEN=glpat-xxxx
GITLAB_URL	Default GitLab instance URL	export GITLAB_URL=https://gitlab.com
DAYS_BACK	Default lookback window when --since omitted	export DAYS_BACK=14


⸻

📄 Input Files

1. Project paths file (--project-paths-file)

A plain text file with one GitLab project path or URL per line, for example:

company/backend/payment-service
company/backend/user-service
company/frontend/web-portal

	•	Blank lines are ignored
	•	Lines starting with # are treated as comments and ignored

You can also use full URLs:

https://gitlab.com/company/backend/payment-service
https://gitlab.com/company/backend/user-service


⸻

2. Excluded authors file (--exclude-authors-file)

A plain text file with one author identifier per line: either username or display name, for example:

# System / automation accounts
ci-bot
release-bot
Code Review Helper

These authors will be excluded from the output (and from metrics).

⸻

🚀 Running the Script

1. Most common usage (with date range)

python review-duration.py \
  --project-paths-file projects.txt \
  --exclude-authors-file exclude_authors.txt \
  --since 2025-11-24 \
  --until 2025-12-07 \
  --out mr_details_2025-11-24_2025-12-07.csv

If you do not specify --summary-out, the script will automatically name the summary file:

review_duration_summary_2025-11-24_2025-12-07.csv


⸻

2. Interactive mode

Just run:

python export_gitlab_mrs_daterange.py

In interactive mode, the script will:
	1.	Ask where your repositories are (GitLab.com by default)
	2.	Ask for start date (since) in YYYY-MM-DD format
	3.	Ask for end date (until) in YYYY-MM-DD format
	4.	Warn and ask for confirmation if the date range is greater than 30 days

⸻

3. Using numeric GitLab project IDs

If you already know project IDs:

python export_gitlab_mrs_daterange.py \
  --projects 12345 67890 112233 \
  --since 2025-11-01 \
  --until 2025-11-30


⸻

4. Using a relative window (DAYS_BACK)

If --since is omitted, the script will use:

since = now - DAYS_BACK

Example:

export DAYS_BACK=10
python export_gitlab_mrs_daterange.py \
  --project-paths-file projects.txt \
  --exclude-authors-file exclude_authors.txt

This will fetch merged MRs from the last 10 days until “now”.

⸻

Output Files

1. Detailed MR CSV (--out)

The detailed CSV contains one row per merged MR with columns such as:
	•	project_id
	•	project_path_with_namespace
	•	iid (internal MR ID)
	•	title
	•	author
	•	created_at
	•	merged_at
	•	time_open_hours
	•	time_open_days
	•	business_time_open_hours
	•	business_time_open_days
	•	target_branch
	•	source_branch
	•	web_url

This file is suitable for further analysis (Excel, BI tools, Python/R notebooks, etc.).

⸻

2. Summary CSV (--summary-out or auto-generated)

Per project, the summary CSV includes:
	•	project_id
	•	project_path_with_namespace
	•	count (number of merged MRs)
	•	avg_hours (average time open)
	•	p50_hours (median)
	•	p90_hours
	•	min_hours
	•	max_hours

By default, this filename is auto-generated as:

review_duration_summary_<since>_<until>.csv

unless --summary-out is explicitly provided.

⸻

3. Console output

During execution, the script logs progress to stderr, for example:

[info] Date window: 2025-11-24 → 2025-12-07
[info] Fetching MRs for company/backend/payment-service (id=12345)...
[done] Wrote 42 rows to mr_details_2025-11-24_2025-12-07.csv
[done] Wrote per-project summary to review_duration_summary_2025-11-24_2025-12-07.csv
[stats overall] Count: 42 | Avg: 15.2h (0.63d) | P50: 4.0h (0.17d) | P90: 48.7h (2.03d)


⸻

Business Hours Calculation

The script computes two kinds of durations:
	1.	Raw duration:
merged_at - created_at (full wall-clock time, including nights and weekends)
	2.	Business-hour duration:
Only time within:
	•	Monday–Friday (weekday 0–4)
	•	Between 09:00–17:00 UTC

Some examples:

Open → Close	Raw duration	Business duration
Fri 18:00 → Mon 10:00	~64 hours	1 hour (Mon 09:00–10:00)
Sat 12:00 → Sun 13:00	~25 hours	0 hours
Tue 08:00 → Tue 11:00	3 hours	2 hours (09:00–11:00)
Wed 16:30 → Wed 18:30	2 hours	0.5 hours (16:30–17:00)

Both values are exported in the detailed CSV as:
	•	time_open_hours, time_open_days
	•	business_time_open_hours, business_time_open_days

⸻

Safety & Validation

To avoid accidental heavy queries:
	•	If the date window (until - since) exceeds 30 days, the script:
	•	Prints a warning
	•	In interactive mode: asks you to type yes to continue
	•	In non-interactive mode: exits with an error

Additional safeguards:
	•	If until is before since, the script exits with an error.
	•	If no projects can be resolved, the script exits with an error.
	•	If --projects values are non-numeric, the script exits with an error.
	•	Unresolvable project paths are logged as warnings, and processing continues for others.

⸻

🛠️ Troubleshooting

1. “Provide a GitLab token” error

Make sure you have either:

export GITLAB_TOKEN="your-token-here"

or pass --token your-token.

Also check that the token has read_api permissions.

⸻

2. “project-paths-file not found”

Confirm paths are relative to where you run the script:

ls
# Should show: export_gitlab_mrs_daterange.py, projects.txt, exclude_authors.txt, etc.

Then:

python export_gitlab_mrs_daterange.py --project-paths-file projects.txt ...


⸻

3. No rows in CSV

Possible reasons:
	•	No MRs were merged in that date range
	•	All MRs were authored by excluded users
	•	Wrong project paths or IDs
	•	Wrong since / until (e.g. future dates)

⸻

4. Rate limiting

The script inspects GitLab’s rate limit headers and will automatically:
	•	Detect low remaining quota
	•	Sleep until reset time if needed

You may still see log messages like:

[rate-limit] Sleeping 12s...

This is expected behavior.

⸻

Ideas for Future Enhancements

Potential future improvements:
	•	Flags like --last-week, --this-week, --last-n-days
	•	Make business-hours window configurable (e.g. --business-hours 08:00-18:00)
	•	Export JSON/Parquet as alternative formats
	•	Post summary directly to chat tools (Slack, MS Teams, etc.)
	•	Generate basic charts (histograms, boxplots) automatically

Contributions or tweaks are welcome.

