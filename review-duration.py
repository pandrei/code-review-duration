#!/usr/bin/env python3
import os
import sys
import csv
import time
import math
import argparse
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple, DefaultDict
from collections import defaultdict

import requests
from dateutil import parser as dtparse
from urllib.parse import urlparse, quote as urlquote

# ------------------------------
# Helpers
# ------------------------------


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(s: str) -> datetime:
    return dtparse.parse(s)


def parse_user_dt(s: str) -> datetime:
    """
    Parse a user-provided datetime string (e.g. '2025-11-10') and normalize to UTC.
    If no timezone is present, assume UTC.
    """
    dt = parse_dt(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def next_link_from_headers(resp: requests.Response) -> Optional[str]:
    link = resp.headers.get("Link")
    if not link:
        return None
    # Link: <url>; rel="next", ...
    for part in [p.strip() for p in link.split(",")]:
        if 'rel="next"' in part:
            start = part.find("<") + 1
            end = part.find(">", start)
            return part[start:end]
    return None


def sleep_if_rate_limited(resp: requests.Response):
    try:
        remaining = int(resp.headers.get("RateLimit-Remaining", "1000"))
        reset = int(resp.headers.get("RateLimit-Reset", "0"))  # unix ts
        if remaining <= 1 and reset:
            now = int(time.time())
            to_sleep = max(0, reset - now) + 1
            print(f"[rate-limit] Sleeping {to_sleep}s...", file=sys.stderr)
            time.sleep(to_sleep)
    except Exception:
        pass


def s_to_hours_days(seconds: float) -> Tuple[float, float]:
    hours = seconds / 3600.0
    days = hours / 24.0
    return round(hours, 2), round(days, 2)


def percentile(sorted_values: List[float], p: float) -> float:
    """
    p in [0,1]. Uses linear interpolation between closest ranks.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    d0 = sorted_values[f] * (c - k)
    d1 = sorted_values[c] * (k - f)
    return d0 + d1


def read_list_file(path: str) -> List[str]:
    """
    Read a simple line-based file into a list:
    - one item per line
    - blank lines ignored
    - lines starting with '#' ignored
    """
    items: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            items.append(s)
    return items


def business_seconds_between(start: datetime, end: datetime) -> float:
    """
    Compute 'business seconds' between two datetimes:
    - Only counts time between 09:00–17:00 UTC
    - Only Monday–Friday (Mon=0, Sun=6)
    """
    if end <= start:
        return 0.0

    # Normalize to UTC
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)

    total = 0.0
    current_date = start.date()
    end_date = end.date()

    # With the 30-day guard this will at most iterate ~31 days → cheap enough
    while current_date <= end_date:
        # Skip weekends
        if current_date.weekday() < 5:  # 0-4 => Mon-Fri
            day_start = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                9,
                0,
                tzinfo=timezone.utc,
            )
            day_end = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                17,
                0,
                tzinfo=timezone.utc,
            )

            interval_start = max(start, day_start)
            interval_end = min(end, day_end)

            if interval_end > interval_start:
                total += (interval_end - interval_start).total_seconds()

        current_date += timedelta(days=1)

    return total


# ------------------------------
# API
# ------------------------------


class GitLab:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.sess = requests.Session()
        self.sess.headers.update({"PRIVATE-TOKEN": token})

    def get(self, url: str, params: dict = None) -> requests.Response:
        r = self.sess.get(url, params=params, timeout=60)
        r.raise_for_status()
        sleep_if_rate_limited(r)
        return r

    def get_json_paged(self, url: str, params: dict = None) -> List[dict]:
        items = []
        first = True
        while True:
            r = self.get(url if not first else url, params=params if first else None)
            first = False
            batch = r.json()
            if isinstance(batch, list):
                items.extend(batch)
            else:
                raise RuntimeError(f"Expected list from {url}")
            nxt = next_link_from_headers(r)
            if not nxt:
                break
            url = nxt
        return items

    def project(self, project_id_or_path: str) -> dict:
        # Accepts numeric id or path; path must be URL-encoded
        if project_id_or_path.isdigit():
            url = f"{self.base_url}/api/v4/projects/{project_id_or_path}"
        else:
            url = f"{self.base_url}/api/v4/projects/{urlquote(project_id_or_path, safe='')}"
        return self.get(url).json()

    def merged_mrs_since(self, project_id: str, since_dt: datetime) -> List[dict]:
        """
        Use state=merged + updated_after for server-side narrowing,
        then strictly filter by merged_at >= since_dt client-side.
        """
        url = f"{self.base_url}/api/v4/projects/{project_id}/merge_requests"
        params = {
            "state": "merged",
            "per_page": 100,
            "order_by": "updated_at",
            "sort": "desc",
            "updated_after": iso_utc(since_dt),
            "scope": "all",
        }
        items = self.get_json_paged(url, params=params)
        cutoff = since_dt.astimezone(timezone.utc)
        recent = []
        for mr in items:
            merged_at = mr.get("merged_at")
            if not merged_at:
                continue
            merged_dt = parse_dt(merged_at).astimezone(timezone.utc)
            if merged_dt >= cutoff:
                recent.append(mr)
        return recent


# ------------------------------
# Summary helpers
# ------------------------------


def extract_path_from_url_or_path(s: str) -> str:
    if s.startswith("http://") or s.startswith("https://"):
        u = urlparse(s)
        return u.path.lstrip("/").split("/-/")[0].rstrip("/")
    return s.strip("/")


def summarize_rows(rows: List[dict]) -> str:
    if not rows:
        return "No merged MRs found in the window."
    seconds = []
    for r in rows:
        c = parse_dt(r["created_at"]).astimezone(timezone.utc)
        m = parse_dt(r["merged_at"]).astimezone(timezone.utc)
        seconds.append((m - c).total_seconds())
    seconds.sort()
    n = len(seconds)
    avg = sum(seconds) / n
    p50 = percentile(seconds, 0.5)
    p90 = percentile(seconds, 0.9)

    def fmt(sec):
        h, d = s_to_hours_days(sec)
        return f"{h}h ({d}d)"

    return f"Count: {n} | Avg: {fmt(avg)} | P50: {fmt(p50)} | P90: {fmt(p90)}"


# ------------------------------
# Main
# ------------------------------


def main():
    ap = argparse.ArgumentParser(description="Export merged GitLab MRs and compute time-open.")
    ap.add_argument(
        "--url",
        default=None,
        help="GitLab base URL (if not provided, you will be prompted; default: https://gitlab.com)",
    )
    ap.add_argument(
        "--token",
        default=os.environ.get("GITLAB_TOKEN"),
        help="GitLab Personal Access Token (or set GITLAB_TOKEN).",
    )
    ap.add_argument(
        "--projects",
        nargs="*",
        default=None,
        help="One or more project IDs (space-separated).",
    )
    ap.add_argument(
        "--project-paths",
        nargs="*",
        default=None,
        help="One or more project paths or full URLs.",
    )
    ap.add_argument(
        "--project-paths-file",
        default=None,
        help=(
            "File with one project path or full URL per line. "
            "Blank lines and lines starting with '#' are ignored."
        ),
    )
    ap.add_argument(
        "--days",
        type=int,
        default=int(os.environ.get("DAYS_BACK", "14")),
        help="Window in days back from now if --since is not provided (default: 14).",
    )
    ap.add_argument(
        "--since",
        default=None,
        help="Explicit start date (human-friendly, e.g. 2025-11-10). Overrides --days.",
    )
    ap.add_argument(
        "--until",
        default=None,
        help="Explicit end date (human-friendly, e.g. 2025-11-16). Default: now.",
    )
    ap.add_argument(
        "--out",
        default="gitlab_merged_mrs_last_14d.csv",
        help="Output CSV filename (detailed rows).",
    )
    ap.add_argument(
        "--summary-out",
        default=None,
        help=(
            "Output CSV filename for the summary. "
            "If not provided, it will be auto-generated as "
            "review_duration_summary_<since>_<until>.csv"
        ),
    )
    ap.add_argument(
        "--exclude-author",
        action="append",
        default=[],
        help="Author name or username to exclude (can be passed multiple times).",
    )
    ap.add_argument(
        "--exclude-authors-file",
        default=None,
        help=(
            "File with one author name/username per line to exclude. "
            "Blank lines and lines starting with '#' are ignored."
        ),
    )
    args = ap.parse_args()

    # ---------------------------
    # Interactive: repository URL
    # ---------------------------
    base_url = args.url or os.environ.get("GITLAB_URL")

    if not base_url and sys.stdin.isatty():
        print("Where are your repositories?")
        print("  1) GitLab.com (https://gitlab.com)")
        print()
        choice = input("Select option [1]: ").strip() or "1"
        if choice == "1":
            base_url = "https://gitlab.com"

    if not base_url:
        base_url = "https://gitlab.com"

    args.url = base_url

    # ---------------------------
    # Token
    # ---------------------------
    if not args.token:
        print("ERROR: Provide a GitLab token via --token or GITLAB_TOKEN.", file=sys.stderr)
        sys.exit(1)

    # ---------------------------
    # Interactive: dates
    # ---------------------------
    now = datetime.now(timezone.utc)

    # If since not provided, optionally prompt the user (interactive only)
    if not args.since and sys.stdin.isatty():
        print("Enter start date (since) in format YYYY-MM-DD (e.g. 2025-12-08).")
        print(f"Leave blank to use last {args.days} days from now.")
        user_since = input("Start date [YYYY-MM-DD or blank]: ").strip()
        if user_since:
            args.since = user_since

    # If until not provided, optionally prompt the user (interactive only)
    if not args.until and sys.stdin.isatty():
        print("Enter end date (until) in format YYYY-MM-DD (e.g. 2025-12-14).")
        print("Leave blank to use 'now'.")
        user_until = input("End date [YYYY-MM-DD or blank]: ").strip()
        if user_until:
            args.until = user_until

    if args.since:
        since_dt = parse_user_dt(args.since)
    else:
        since_dt = now - timedelta(days=args.days)

    until_dt = parse_user_dt(args.until) if args.until else now

    # Ensure date ordering is valid
    if until_dt < since_dt:
        print("ERROR: until date is before since date.", file=sys.stderr)
        sys.exit(1)

    # Safety check: warn if date window > 30 days
    window_seconds = (until_dt - since_dt).total_seconds()
    window_days = window_seconds / 86400.0

    if window_days > 30:
        print(
            f"WARNING: date range is {window_days:.1f} days (> 30). "
            "This may query many MRs and put load on the repository/API.",
            file=sys.stderr,
        )
        if sys.stdin.isatty():
            answer = input("Type 'yes' to continue: ").strip().lower()
            if answer != "yes":
                print("Aborting at user request.", file=sys.stderr)
                sys.exit(1)
        else:
            print("Non-interactive mode and large date window; aborting.", file=sys.stderr)
            sys.exit(1)

    # ---------------------------
    # Determine summary output file name
    # ---------------------------
    if args.summary_out:
        summary_filename = args.summary_out
    else:
        since_str = since_dt.strftime("%Y-%m-%d")
        until_str = until_dt.strftime("%Y-%m-%d")
        summary_filename = f"review_duration_summary_{since_str}_{until_str}.csv"

    print(f"[info] Date window: {since_dt.strftime('%Y-%m-%d')} → {until_dt.strftime('%Y-%m-%d')}", file=sys.stderr)

    gl = GitLab(args.url, args.token)

    # ---------------------------
    # Projects: collect IDs
    # ---------------------------
    project_ids: List[str] = []
    if args.projects:
        project_ids.extend(args.projects)

    # Collect project paths from CLI + file
    project_paths: List[str] = []
    if args.project_paths:
        project_paths.extend(args.project_paths)

    if args.project_paths_file:
        try:
            project_paths.extend(read_list_file(args.project_paths_file))
        except FileNotFoundError:
            print(f"ERROR: project-paths-file not found: {args.project_paths_file}", file=sys.stderr)
            sys.exit(1)

    if project_paths:
        for p in project_paths:
            path = extract_path_from_url_or_path(p)
            try:
                proj_json = gl.project(path)
                project_ids.append(str(proj_json["id"]))
            except Exception as e:
                print(f"[warn] Could not resolve {p} → id: {e}", file=sys.stderr)

    if not project_ids:
        print("ERROR: Provide at least one project via --projects, --project-paths or --project-paths-file.",
              file=sys.stderr)
        sys.exit(1)

    # Ensure all project IDs are numeric (for sorting)
    try:
        sorted_project_ids = sorted(set(project_ids), key=lambda x: int(x))
    except ValueError:
        print("ERROR: all --projects values must be numeric IDs.", file=sys.stderr)
        sys.exit(1)

    # ---------------------------
    # Excluded authors (CLI + file)
    # ---------------------------
    exclude_authors: List[str] = list(args.exclude_author)

    if args.exclude_authors_file:
        try:
            exclude_authors.extend(read_list_file(args.exclude_authors_file))
        except FileNotFoundError:
            print(f"ERROR: exclude-authors-file not found: {args.exclude_authors_file}", file=sys.stderr)
            sys.exit(1)

    # Normalize / deduplicate
    exclude_authors = sorted({a.strip() for a in exclude_authors if a.strip()})

    out_rows: List[Dict[str, Any]] = []
    per_project_seconds: DefaultDict[Tuple[str, str], List[float]] = defaultdict(list)

    # ---------------------------
    # Fetch
    # ---------------------------
    for pid in sorted_project_ids:
        try:
            proj = gl.project(pid)
            path_ns = proj.get("path_with_namespace", pid)
            print(f"[info] Fetching MRs for {path_ns} (id={pid})...", file=sys.stderr)
        except Exception as e:
            print(f"[warn] Could not read project {pid}: {e}", file=sys.stderr)
            continue

        try:
            mrs = gl.merged_mrs_since(pid, since_dt)
        except Exception as e:
            print(f"[warn] Could not list MRs for {pid}: {e}", file=sys.stderr)
            continue

        strict_mrs = []
        for mr in mrs:
            merged_at = mr.get("merged_at")
            if not merged_at:
                continue
            merged_dt = parse_dt(merged_at).astimezone(timezone.utc)
            if merged_dt <= until_dt:
                strict_mrs.append(mr)

        for mr in strict_mrs:
            # ------------------------------------------
            # Exclude authors passed via CLI / file
            # ------------------------------------------
            author = mr.get("author") or {}
            author_name = (author.get("name") or "").strip()
            author_username = (author.get("username") or "").strip()

            if author_name in exclude_authors or author_username in exclude_authors:
                continue

            c = parse_dt(mr["created_at"]).astimezone(timezone.utc)
            m = parse_dt(mr["merged_at"]).astimezone(timezone.utc)
            delta_sec = (m - c).total_seconds()
            business_delta_sec = business_seconds_between(c, m)

            hrs, dys = s_to_hours_days(delta_sec)
            biz_hrs, biz_dys = s_to_hours_days(business_delta_sec)

            out_rows.append({
                "project_id": pid,
                "project_path_with_namespace": path_ns,
                "iid": mr["iid"],
                "title": mr["title"],
                "author": author_username or author_name,
                "created_at": c.isoformat(),
                "merged_at": m.isoformat(),
                "time_open_hours": hrs,
                "time_open_days": dys,
                "business_time_open_hours": biz_hrs,
                "business_time_open_days": biz_dys,
                "target_branch": mr.get("target_branch", ""),
                "source_branch": mr.get("source_branch", ""),
                "web_url": mr.get("web_url", ""),
            })

            per_project_seconds[(pid, path_ns)].append(delta_sec)

    # ---------------------------
    # Write detailed CSV
    # ---------------------------
    out_rows.sort(key=lambda r: r["merged_at"], reverse=True)
    detail_fields = [
        "project_id",
        "project_path_with_namespace",
        "iid",
        "title",
        "author",
        "created_at",
        "merged_at",
        "time_open_hours",
        "time_open_days",
        "business_time_open_hours",
        "business_time_open_days",
        "target_branch",
        "source_branch",
        "web_url",
    ]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=detail_fields)
        w.writeheader()
        w.writerows(out_rows)
    print(f"[done] Wrote {len(out_rows)} rows to {args.out}")

    # ---------------------------
    # Build & write per-project summary (raw durations only)
    # ---------------------------
    summary_rows: List[Dict[str, Any]] = []
    for (pid, path_ns), secs in sorted(
        per_project_seconds.items(),
        key=lambda item: int(item[0][0]),  # item[0] = (pid, path_ns)
    ):
        secs.sort()
        n = len(secs)
        avg = sum(secs) / n if n else 0.0
        p50 = percentile(secs, 0.5) if n else 0.0
        p90 = percentile(secs, 0.9) if n else 0.0
        mn = secs[0] if n else 0.0
        mx = secs[-1] if n else 0.0
        avg_h, _ = s_to_hours_days(avg)
        p50_h, _ = s_to_hours_days(p50)
        p90_h, _ = s_to_hours_days(p90)
        mn_h, _ = s_to_hours_days(mn)
        mx_h, _ = s_to_hours_days(mx)
        summary_rows.append({
            "project_id": pid,
            "project_path_with_namespace": path_ns,
            "count": n,
            "avg_hours": avg_h,
            "p50_hours": p50_h,
            "p90_hours": p90_h,
            "min_hours": mn_h,
            "max_hours": mx_h,
        })

    summary_fields = [
        "project_id",
        "project_path_with_namespace",
        "count",
        "avg_hours",
        "p50_hours",
        "p90_hours",
        "min_hours",
        "max_hours",
    ]
    with open(summary_filename, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=summary_fields)
        w.writeheader()
        w.writerows(summary_rows)

    print(f"[done] Wrote per-project summary to {summary_filename}")
    print("[stats overall]", summarize_rows(out_rows))


if __name__ == "__main__":
    main()