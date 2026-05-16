from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
DB_PATH = OUTPUT_DIR / "jobs.sqlite"
USER_AGENT = "remote-role-agent-v3/local"

US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming", "district of columbia"
}

US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"
}


@dataclass
class Job:
    source: str
    title: str
    company: str
    location: str
    remote: str
    salary: str
    posted_at: str
    url: str
    description: str
    score: int = 0
    fit: str = ""
    matched_keywords: str = ""
    warnings: str = ""
    reason: str = ""
    job_key: str = ""
    is_us_remote: bool = False
    geo_reason: str = ""
    reject_reason: str = ""
    is_new: bool = False
    first_seen: str = ""
    last_seen: str = ""
    user_status: str = "new"
    ai_score: Optional[int] = None
    ai_summary: str = ""
    ai_resume_keywords: str = ""
    ai_resume_angle: str = ""
    ai_cover_letter_angle: str = ""
    ai_risks: str = ""


@dataclass
class SourceStatus:
    source: str
    status: str
    detail: str
    fetched: int = 0
    kept: int = 0
    rejected: int = 0
    started_at: str = ""
    finished_at: str = ""
    skipped_by_cooldown: bool = False


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def load_config() -> Dict[str, Any]:
    with (ROOT / "config.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def network_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.get("network", {}) if isinstance(config.get("network"), dict) else {}


def request_timeout(config: Dict[str, Any]) -> int:
    try:
        return int(network_config(config).get("request_timeout_seconds", 12))
    except Exception:
        return 12


def request_delay(config: Dict[str, Any]) -> float:
    try:
        return float(network_config(config).get("request_delay_seconds", 0.1))
    except Exception:
        return 0.1


def description_limit(config: Dict[str, Any]) -> int:
    try:
        return int(network_config(config).get("description_char_limit", 2200))
    except Exception:
        return 2200


def max_results_per_keyword(config: Dict[str, Any]) -> int:
    try:
        return int(network_config(config).get("max_results_per_keyword", config.get("max_results_per_keyword", 15)))
    except Exception:
        return 15


def source_enabled(config: Dict[str, Any], source_name: str) -> bool:
    enabled = config.get("enabled_sources") or []
    if not enabled:
        return True
    return normalize(source_name) in {normalize(str(x)) for x in enabled}


def clean_text(value: Any, limit: Optional[int] = None) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def contains_any(text: str, terms: Iterable[str]) -> List[str]:
    text_l = f" {normalize(text)} "
    found: List[str] = []
    for term in terms:
        term_l = normalize(term)
        if not term_l:
            continue
        if term_l in text_l:
            found.append(term)
    return found


def safe_get(data: Dict[str, Any], path: List[str], default: Any = "") -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def parse_salary(*parts: Any) -> str:
    values = [str(p).strip() for p in parts if p not in (None, "")]
    return " | ".join(values)


def get_json(url: str, config: Dict[str, Any], *, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Any:
    delay = request_delay(config)
    if delay > 0:
        time.sleep(delay)
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    response = requests.get(url, params=params, headers=merged, timeout=request_timeout(config))
    response.raise_for_status()
    return response.json()


def post_json(url: str, config: Dict[str, Any], *, payload: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Any:
    delay = request_delay(config)
    if delay > 0:
        time.sleep(delay)
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"}
    if headers:
        merged.update(headers)
    response = requests.post(url, json=payload or {}, headers=merged, timeout=request_timeout(config))
    response.raise_for_status()
    return response.json()


def make_key(job: Job) -> str:
    url_key = ""
    if job.url:
        parsed = urlparse(job.url)
        url_key = f"{parsed.netloc}{parsed.path}".rstrip("/")
    raw = "|".join([normalize(job.source), normalize(job.company), normalize(job.title), normalize(job.location), url_key])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def location_has_us_signal(location: str) -> bool:
    loc = f" {normalize(location)} "
    if any(term in loc for term in [" united states ", " usa ", " u.s. ", " us ", " remote, us ", " remote us "]):
        return True
    if "anywhere in the united states" in loc or "anywhere in the us" in loc or "anywhere in the u.s." in loc:
        return True
    if any(state in loc for state in US_STATE_NAMES):
        return True
    tokens = set(re.findall(r"\b[A-Z]{2}\b", location or ""))
    return bool(tokens & US_STATE_CODES)


def has_foreign_location_signal(location: str, config: Dict[str, Any]) -> List[str]:
    loc = f" {normalize(location)} "
    terms = config.get("geo_filters", {}).get("non_us_location_terms", [])
    hits: List[str] = []
    for term in terms:
        term_l = normalize(term)
        if not term_l:
            continue
        if term_l == "mexico":
            if " mexico city " in loc or " ciudad de mexico " in loc:
                hits.append(term)
            continue
        if term_l == "uk":
            if re.search(r"\buk\b", loc):
                hits.append(term)
            continue
        if f" {term_l} " in loc:
            hits.append(term)
    return sorted(set(hits))


def description_has_explicit_us_remote_signal(text: str, config: Dict[str, Any]) -> List[str]:
    hits = contains_any(text, config.get("geo_filters", {}).get("explicit_us_remote_phrases", []))
    text_l = normalize(text)
    regexes = [
        r"remote.{0,45}(united states|u\.s\.|\bus\b|usa)",
        r"(united states|u\.s\.|\bus\b|usa).{0,45}remote",
        r"must (be )?(located|based|reside).{0,45}(united states|u\.s\.|\bus\b|usa)",
        r"candidates.{0,60}(united states|u\.s\.|\bus\b|usa)",
    ]
    for rx in regexes:
        if re.search(rx, text_l):
            hits.append("explicit U.S. remote wording")
    return sorted(set(hits))


def classify_us_remote(job: Job, config: Dict[str, Any]) -> Tuple[bool, str, str]:
    loc_text = " ".join([job.location, job.remote])
    full_text = " ".join([job.title, job.location, job.remote, job.description])
    loc_l = normalize(loc_text)

    hard_disqualifiers = [
        "hybrid", "on-site", "onsite", "in office", "office based", "office-based", "relocation required",
        "must be based in london", "must be based in canada", "must be based in europe", "europe only",
        "canada only", "uk only", "emea only", "apac only", "latin america only", "latam only"
    ]
    if contains_any(full_text, hard_disqualifiers):
        return False, "", "rejected because role may be hybrid, onsite, or outside the U.S."

    foreign_hits = has_foreign_location_signal(loc_text, config)
    if foreign_hits:
        return False, "", f"rejected because location field mentions {', '.join(foreign_hits[:4])}"

    remote_signal = bool(contains_any(full_text, ["remote", "work from home", "work-from-home", "distributed", "virtual"]))
    explicit_us_remote = description_has_explicit_us_remote_signal(full_text, config)
    us_in_location = location_has_us_signal(loc_text)

    if job.source.lower() == "usajobs" and remote_signal:
        return True, "USAJOBS remote indicator", ""
    if remote_signal and us_in_location:
        return True, "remote signal plus U.S. location field", ""
    if explicit_us_remote:
        return True, "; ".join(explicit_us_remote[:2]), ""

    exact_remote_locations = {"remote", "remote anywhere", "distributed", "virtual", "work from home"}
    if loc_l in exact_remote_locations:
        if config.get("keep_ambiguous_remote", False):
            return True, "ambiguous remote kept by config", ""
        return False, "", "rejected because remote location is not explicitly U.S."

    if remote_signal:
        return False, "", "rejected because remote role lacks explicit U.S. location signal"
    return False, "", "rejected because role is not clearly remote in the U.S."


def score_job(job: Job, config: Dict[str, Any]) -> Job:
    profile = config.get("target_profile", {})
    blob = " ".join([job.title, job.company, job.location, job.remote, job.description])
    matched_strong = contains_any(blob, profile.get("strong_keywords", []))
    matched_nice = contains_any(blob, profile.get("nice_to_have_keywords", []))
    title_hits = contains_any(job.title, profile.get("preferred_titles", []))
    warnings = contains_any(blob, profile.get("warning_keywords", []))
    remote_ok, geo_reason, reject_reason = classify_us_remote(job, config)

    score = 25
    score += 30 if remote_ok else -30
    score += min(28, len(set(matched_strong)) * 4)
    score += min(10, len(set(matched_nice)) * 2)
    score += min(12, len(set(title_hits)) * 4)

    watchlist = {normalize(x) for x in config.get("company_watchlist", [])}
    if normalize(job.company) in watchlist or any(normalize(job.company) in w or w in normalize(job.company) for w in watchlist):
        score += 7

    if warnings:
        score -= min(40, len(set(warnings)) * 10)

    title_l = normalize(job.title)
    if "intern" in title_l or "student" in title_l:
        score -= 30
    if "engineer" in title_l and not any(x in title_l for x in ["risk", "fraud", "trust", "analyst", "operations"]):
        score -= 22
    if "manager" in title_l and not any(x in title_l for x in ["risk", "fraud", "operations", "compliance", "financial crimes"]):
        score -= 8
    if "director" in title_l or "vp" in title_l or "head of" in title_l:
        score -= 14

    score = max(0, min(100, score))
    fit = "Strong fit" if score >= 78 else "Maybe" if score >= 58 else "Low fit"

    reasons: List[str] = []
    if geo_reason:
        reasons.append(geo_reason)
    if title_hits:
        reasons.append("title aligns")
    if matched_strong:
        reasons.append("matches " + ", ".join(sorted(set(matched_strong))[:7]))
    if warnings:
        reasons.append("check " + ", ".join(sorted(set(warnings))[:5]))

    job.score = score
    job.fit = fit
    job.matched_keywords = ", ".join(sorted(set(matched_strong + matched_nice)))
    job.warnings = ", ".join(sorted(set(warnings)))
    job.reason = "; ".join(reasons) if reasons else "limited profile match"
    job.is_us_remote = remote_ok
    job.geo_reason = geo_reason
    job.reject_reason = reject_reason
    job.job_key = make_key(job)
    return job


def fetch_adzuna(config: Dict[str, Any]) -> Tuple[List[Job], SourceStatus]:
    app_id = os.getenv("ADZUNA_APP_ID", "").strip()
    app_key = os.getenv("ADZUNA_APP_KEY", "").strip()
    if not app_id or not app_key:
        return [], SourceStatus("Adzuna", "skipped", "ADZUNA_APP_ID and ADZUNA_APP_KEY are not set")
    jobs: List[Job] = []
    for keyword in config.get("search_keywords", []):
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "what": keyword,
            "where": "United States",
            "results_per_page": min(50, max_results_per_keyword(config)),
            "sort_by": "date",
            "content-type": "application/json"
        }
        try:
            data = get_json("https://api.adzuna.com/v1/api/jobs/us/search/1", config, params=params)
        except Exception as exc:
            return jobs, SourceStatus("Adzuna", "partial" if jobs else "error", str(exc), fetched=len(jobs))
        for item in data.get("results", []) or []:
            desc = clean_text(item.get("description"), limit=description_limit(config))
            location = safe_get(item, ["location", "display_name"])
            jobs.append(Job(
                source="Adzuna",
                title=clean_text(item.get("title")),
                company=clean_text(safe_get(item, ["company", "display_name"])),
                location=clean_text(location),
                remote="Remote" if "remote" in normalize(" ".join([str(item.get("title", "")), str(location), desc])) else "",
                salary=parse_salary(item.get("salary_min"), item.get("salary_max")),
                posted_at=clean_text(item.get("created")),
                url=clean_text(item.get("redirect_url")),
                description=desc,
            ))
    return jobs, SourceStatus("Adzuna", "ok", "fetched from search API", fetched=len(jobs))


def fetch_usajobs(config: Dict[str, Any]) -> Tuple[List[Job], SourceStatus]:
    api_key = os.getenv("USAJOBS_API_KEY", "").strip()
    user_agent = os.getenv("USAJOBS_USER_AGENT", "").strip() or os.getenv("USER_EMAIL", "").strip()
    if not api_key or not user_agent:
        return [], SourceStatus("USAJOBS", "skipped", "USAJOBS_API_KEY and USAJOBS_USER_AGENT are not set")
    headers = {"Host": "data.usajobs.gov", "User-Agent": user_agent, "Authorization-Key": api_key}
    jobs: List[Job] = []
    for keyword in config.get("search_keywords", []):
        params = {"Keyword": keyword, "RemoteIndicator": "True", "ResultsPerPage": min(100, max_results_per_keyword(config)), "Fields": "Full"}
        try:
            data = get_json("https://data.usajobs.gov/api/search", config, params=params, headers=headers)
        except Exception as exc:
            return jobs, SourceStatus("USAJOBS", "partial" if jobs else "error", str(exc), fetched=len(jobs))
        items = safe_get(data, ["SearchResult", "SearchResultItems"], [])
        for row in items or []:
            d = row.get("MatchedObjectDescriptor", {}) if isinstance(row, dict) else {}
            locs = d.get("PositionLocation", []) or []
            location = ", ".join([x.get("LocationName", "") for x in locs if isinstance(x, dict)]) or "Remote, United States"
            salaries = d.get("PositionRemuneration", []) or []
            salary = " | ".join([f"{x.get('MinimumRange', '')} to {x.get('MaximumRange', '')} {x.get('RateIntervalCode', '')}".strip() for x in salaries if isinstance(x, dict)])
            desc = clean_text(" ".join([str(d.get("QualificationSummary", "")), str(safe_get(d, ["UserArea", "Details", "MajorDuties"], ""))]), limit=description_limit(config))
            jobs.append(Job(
                source="USAJOBS",
                title=clean_text(d.get("PositionTitle")),
                company=clean_text(d.get("OrganizationName")),
                location=clean_text(location),
                remote="Remote",
                salary=clean_text(salary),
                posted_at=clean_text(d.get("PublicationStartDate")),
                url=clean_text(d.get("PositionURI")),
                description=desc,
            ))
    return jobs, SourceStatus("USAJOBS", "ok", "fetched remote federal postings", fetched=len(jobs))


def fetch_one_greenhouse(token: str, config: Dict[str, Any]) -> Tuple[List[Job], bool]:
    token = str(token).strip()
    if not token:
        return [], False
    try:
        data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", config, params={"content": "true"})
    except Exception:
        return [], True
    jobs: List[Job] = []
    for item in data.get("jobs", []) or []:
        offices = item.get("offices") or []
        departments = item.get("departments") or []
        location = safe_get(item, ["location", "name"])
        office_names = ", ".join([o.get("name", "") for o in offices if isinstance(o, dict)])
        department = ", ".join([d.get("name", "") for d in departments if isinstance(d, dict)])
        content = clean_text(item.get("content"), limit=description_limit(config))
        jobs.append(Job(
            source="Greenhouse",
            title=clean_text(item.get("title")),
            company=clean_text(token),
            location=clean_text(location or office_names),
            remote="Remote" if "remote" in normalize(" ".join([str(location), office_names, content])) else "",
            salary="",
            posted_at=clean_text(item.get("updated_at")),
            url=clean_text(item.get("absolute_url")),
            description=clean_text(" ".join([department, content]), limit=description_limit(config)),
        ))
    return jobs, False


def fetch_greenhouse(config: Dict[str, Any]) -> Tuple[List[Job], SourceStatus]:
    companies = [x for x in config.get("greenhouse_companies", []) if str(x).strip()]
    if not companies:
        return [], SourceStatus("Greenhouse", "skipped", "no Greenhouse boards configured")
    jobs: List[Job] = []
    failed = 0
    for token in companies:
        board_jobs, did_fail = fetch_one_greenhouse(str(token), config)
        jobs.extend(board_jobs)
        failed += 1 if did_fail else 0
    return jobs, SourceStatus("Greenhouse", "ok" if jobs else "skipped", f"checked {len(companies)} public boards, {failed} unavailable", fetched=len(jobs))


def parse_lever_date(value: Any) -> str:
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except Exception:
        pass
    return clean_text(value)


def fetch_one_lever(company: str, config: Dict[str, Any]) -> Tuple[List[Job], bool]:
    company = str(company).strip()
    if not company:
        return [], False
    try:
        data = get_json(f"https://api.lever.co/v0/postings/{company}", config, params={"mode": "json"})
    except Exception:
        return [], True
    if not isinstance(data, list):
        return [], False
    jobs: List[Job] = []
    for item in data:
        categories = item.get("categories", {}) if isinstance(item, dict) else {}
        lists = item.get("lists", []) if isinstance(item, dict) else []
        salary_desc = ""
        list_content: List[str] = []
        for section in lists or []:
            if not isinstance(section, dict):
                continue
            heading = normalize(section.get("text", ""))
            content = clean_text(section.get("content"), limit=1000)
            list_content.append(content)
            if "salary" in heading or "compensation" in heading:
                salary_desc = clean_text(content, limit=300)
        desc_plain = clean_text(item.get("descriptionPlain"), limit=description_limit(config))
        loc = categories.get("location", "") if isinstance(categories, dict) else ""
        team = categories.get("team", "") if isinstance(categories, dict) else ""
        commitment = categories.get("commitment", "") if isinstance(categories, dict) else ""
        jobs.append(Job(
            source="Lever",
            title=clean_text(item.get("text")),
            company=clean_text(company),
            location=clean_text(loc),
            remote="Remote" if "remote" in normalize(" ".join([loc, item.get("text", ""), desc_plain])) else "",
            salary=salary_desc,
            posted_at=parse_lever_date(item.get("createdAt", "")),
            url=clean_text(item.get("hostedUrl") or item.get("applyUrl")),
            description=clean_text(" ".join([team, commitment, desc_plain] + list_content), limit=description_limit(config)),
        ))
    return jobs, False


def fetch_lever(config: Dict[str, Any]) -> Tuple[List[Job], SourceStatus]:
    companies = [x for x in config.get("lever_companies", []) if str(x).strip()]
    if not companies:
        return [], SourceStatus("Lever", "skipped", "no Lever companies configured")
    jobs: List[Job] = []
    failed = 0
    for company in companies:
        board_jobs, did_fail = fetch_one_lever(str(company), config)
        jobs.extend(board_jobs)
        failed += 1 if did_fail else 0
    return jobs, SourceStatus("Lever", "ok" if jobs else "skipped", f"checked {len(companies)} public boards, {failed} unavailable", fetched=len(jobs))


def extract_ashby_location(item: Dict[str, Any]) -> str:
    pieces: List[str] = []
    if item.get("location"):
        pieces.append(str(item.get("location")))
    addr = safe_get(item, ["address", "postalAddress"], {})
    if isinstance(addr, dict):
        joined = ", ".join([str(x) for x in [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")] if x])
        if joined:
            pieces.append(joined)
    for sec in item.get("secondaryLocations", []) or []:
        if not isinstance(sec, dict):
            continue
        if sec.get("location"):
            pieces.append(str(sec.get("location")))
        sec_addr = sec.get("address") or {}
        if isinstance(sec_addr, dict):
            joined = ", ".join([str(x) for x in [sec_addr.get("addressLocality"), sec_addr.get("addressRegion"), sec_addr.get("addressCountry")] if x])
            if joined:
                pieces.append(joined)
    return clean_text(" | ".join(dict.fromkeys([p for p in pieces if p])))


def ashby_salary(item: Dict[str, Any]) -> str:
    comp = item.get("compensation") or {}
    if isinstance(comp, dict):
        return clean_text(comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary") or "")
    return ""


def fetch_one_ashby(company: str, config: Dict[str, Any]) -> Tuple[List[Job], bool]:
    company = str(company).strip()
    if not company:
        return [], False
    try:
        data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{company}", config, params={"includeCompensation": "true"})
    except Exception:
        return [], True
    items = data.get("jobs", []) if isinstance(data, dict) else []
    jobs: List[Job] = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("isListed") is False:
            continue
        location = extract_ashby_location(item)
        desc = clean_text(item.get("descriptionPlain") or item.get("descriptionHtml"), limit=description_limit(config))
        workplace = clean_text(item.get("workplaceType"))
        remote = "Remote" if item.get("isRemote") or normalize(workplace) == "remote" or "remote" in normalize(location) else ""
        meta = " ".join([clean_text(item.get("department")), clean_text(item.get("team")), clean_text(item.get("employmentType")), desc])
        jobs.append(Job(
            source="Ashby",
            title=clean_text(item.get("title")),
            company=clean_text(company),
            location=location,
            remote=remote,
            salary=ashby_salary(item),
            posted_at=clean_text(item.get("publishedAt")),
            url=clean_text(item.get("jobUrl") or item.get("applyUrl")),
            description=clean_text(meta, limit=description_limit(config)),
        ))
    return jobs, False


def fetch_ashby(config: Dict[str, Any]) -> Tuple[List[Job], SourceStatus]:
    companies = [x for x in config.get("ashby_companies", []) if str(x).strip()]
    if not companies:
        return [], SourceStatus("Ashby", "skipped", "no Ashby boards configured")
    jobs: List[Job] = []
    failed = 0
    for company in companies:
        board_jobs, did_fail = fetch_one_ashby(str(company), config)
        jobs.extend(board_jobs)
        failed += 1 if did_fail else 0
    return jobs, SourceStatus("Ashby", "ok" if jobs else "skipped", f"checked {len(companies)} public boards, {failed} unavailable", fetched=len(jobs))


def normalize_workday_board(raw: Any) -> Optional[Dict[str, str]]:
    if isinstance(raw, str):
        parts = raw.strip().split(":")
        if len(parts) >= 2:
            return {"tenant": parts[0].strip(), "site": parts[1].strip(), "server": parts[2].strip() if len(parts) > 2 and parts[2].strip() else "wd1", "company": parts[3].strip() if len(parts) > 3 and parts[3].strip() else parts[0].strip()}
        return None
    if isinstance(raw, dict):
        tenant = str(raw.get("tenant", "")).strip()
        site = str(raw.get("site", "")).strip()
        if not tenant or not site:
            return None
        return {"tenant": tenant, "site": site, "server": str(raw.get("server", "wd1")).strip() or "wd1", "company": str(raw.get("company", tenant)).strip() or tenant}
    return None


def workday_job_url(board: Dict[str, str], external_path: str) -> str:
    path = external_path or ""
    if path and not path.startswith("/"):
        path = "/" + path
    return f"https://{board['tenant']}.{board.get('server', 'wd1')}.myworkdayjobs.com/{board['site']}{path}"


def fetch_one_workday(board: Dict[str, str], config: Dict[str, Any]) -> Tuple[List[Job], bool]:
    tenant = board["tenant"]
    site = board["site"]
    server = board.get("server", "wd1")
    company = board.get("company", tenant)
    net = network_config(config)
    page_limit = max(1, int(net.get("workday_max_pages_per_board", 1)))
    page_size = max(10, min(50, int(net.get("workday_page_size", 20))))
    skip_details = bool(net.get("workday_skip_details", True))
    base = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    jobs: List[Job] = []
    try:
        for page in range(page_limit):
            payload = {"appliedFacets": {}, "limit": page_size, "offset": page * page_size, "searchText": ""}
            data = post_json(f"{base}/jobs", config, payload=payload)
            postings = data.get("jobPostings", []) if isinstance(data, dict) else []
            if not postings:
                break
            for item in postings:
                if not isinstance(item, dict):
                    continue
                title = clean_text(item.get("title"))
                loc = clean_text(item.get("locationsText") or " | ".join(item.get("locations", []) or []))
                external_path = clean_text(item.get("externalPath"))
                posted = clean_text(item.get("postedOn") or item.get("startDate") or "")
                bullet_fields = " ".join([str(x) for x in item.get("bulletFields", []) or []])
                desc_parts = [bullet_fields]
                if external_path and not skip_details:
                    try:
                        detail = get_json(f"{base}{external_path}", config)
                        info = detail.get("jobPostingInfo", {}) if isinstance(detail, dict) else {}
                        desc_parts.append(clean_text(info.get("jobDescription"), limit=description_limit(config)))
                    except Exception:
                        pass
                text_for_remote = " ".join([title, loc, bullet_fields])
                jobs.append(Job(
                    source="Workday",
                    title=title,
                    company=clean_text(company),
                    location=loc,
                    remote="Remote" if "remote" in normalize(text_for_remote) else "",
                    salary="",
                    posted_at=posted,
                    url=workday_job_url(board, external_path),
                    description=clean_text(" ".join(desc_parts), limit=description_limit(config)),
                ))
    except Exception:
        return jobs, True
    return jobs, False


def fetch_workday(config: Dict[str, Any]) -> Tuple[List[Job], SourceStatus]:
    boards = [normalize_workday_board(x) for x in config.get("workday_boards", [])]
    boards = [b for b in boards if b]
    if not boards:
        return [], SourceStatus("Workday", "skipped", "no Workday boards configured")
    jobs: List[Job] = []
    failed = 0
    for board in boards:
        board_jobs, did_fail = fetch_one_workday(board, config)
        jobs.extend(board_jobs)
        failed += 1 if did_fail else 0
    return jobs, SourceStatus("Workday", "ok" if jobs else "skipped", f"checked {len(boards)} configured boards, {failed} unavailable", fetched=len(jobs))


FETCHERS = {
    "Adzuna": fetch_adzuna,
    "USAJOBS": fetch_usajobs,
    "Greenhouse": fetch_greenhouse,
    "Lever": fetch_lever,
    "Ashby": fetch_ashby,
    "Workday": fetch_workday,
}


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = connect_db()
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_key TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            remote TEXT,
            salary TEXT,
            posted_at TEXT,
            url TEXT,
            description TEXT,
            score INTEGER,
            fit TEXT,
            matched_keywords TEXT,
            warnings TEXT,
            reason TEXT,
            is_us_remote INTEGER,
            geo_reason TEXT,
            reject_reason TEXT,
            first_seen TEXT,
            last_seen TEXT,
            is_new INTEGER,
            user_status TEXT DEFAULT 'new',
            ai_score INTEGER,
            ai_summary TEXT,
            ai_resume_keywords TEXT,
            ai_resume_angle TEXT,
            ai_cover_letter_angle TEXT,
            ai_risks TEXT
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS rejected_jobs (
            job_key TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT,
            reject_reason TEXT,
            seen_at TEXT
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS source_state (
            source TEXT PRIMARY KEY,
            last_checked TEXT,
            last_status TEXT,
            last_detail TEXT,
            fetched INTEGER DEFAULT 0,
            kept INTEGER DEFAULT 0,
            rejected INTEGER DEFAULT 0
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS run_history (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT,
            fetched INTEGER,
            kept INTEGER,
            rejected INTEGER,
            strong INTEGER,
            maybe INTEGER,
            low INTEGER
        )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(user_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source)")
        conn.commit()
    finally:
        conn.close()


def add_missing_columns() -> None:
    conn = connect_db()
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        columns = {
            "ai_score": "INTEGER",
            "ai_summary": "TEXT",
            "ai_resume_keywords": "TEXT",
            "ai_resume_angle": "TEXT",
            "ai_cover_letter_angle": "TEXT",
            "ai_risks": "TEXT",
            "user_status": "TEXT DEFAULT 'new'"
        }
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}")
        conn.commit()
    finally:
        conn.close()


def existing_job_meta(conn: sqlite3.Connection, job_key: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT first_seen, user_status, ai_score, ai_summary, ai_resume_keywords, ai_resume_angle, ai_cover_letter_angle, ai_risks FROM jobs WHERE job_key = ?", (job_key,)).fetchone()


def upsert_jobs(accepted: List[Job], rejected: List[Job], statuses: List[SourceStatus]) -> Dict[str, int]:
    init_db()
    add_missing_columns()
    conn = connect_db()
    run_at = now_utc_iso()
    new_count = 0
    try:
        for job in accepted:
            existing = existing_job_meta(conn, job.job_key)
            if existing:
                first_seen = existing["first_seen"] or run_at
                user_status = existing["user_status"] or "seen"
                is_new = False
                ai_score = existing["ai_score"]
                ai_summary = existing["ai_summary"] or ""
                ai_resume_keywords = existing["ai_resume_keywords"] or ""
                ai_resume_angle = existing["ai_resume_angle"] or ""
                ai_cover_letter_angle = existing["ai_cover_letter_angle"] or ""
                ai_risks = existing["ai_risks"] or ""
            else:
                first_seen = run_at
                user_status = "new"
                is_new = True
                new_count += 1
                ai_score = job.ai_score
                ai_summary = job.ai_summary
                ai_resume_keywords = job.ai_resume_keywords
                ai_resume_angle = job.ai_resume_angle
                ai_cover_letter_angle = job.ai_cover_letter_angle
                ai_risks = job.ai_risks
            job.first_seen = first_seen
            job.last_seen = run_at
            job.is_new = is_new
            job.user_status = user_status
            conn.execute("""
            INSERT OR REPLACE INTO jobs (
                job_key, source, title, company, location, remote, salary, posted_at, url, description,
                score, fit, matched_keywords, warnings, reason, is_us_remote, geo_reason, reject_reason,
                first_seen, last_seen, is_new, user_status, ai_score, ai_summary, ai_resume_keywords,
                ai_resume_angle, ai_cover_letter_angle, ai_risks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.job_key, job.source, job.title, job.company, job.location, job.remote, job.salary, job.posted_at, job.url,
                job.description, job.score, job.fit, job.matched_keywords, job.warnings, job.reason, 1 if job.is_us_remote else 0,
                job.geo_reason, job.reject_reason, first_seen, run_at, 1 if is_new else 0, user_status, ai_score,
                ai_summary, ai_resume_keywords, ai_resume_angle, ai_cover_letter_angle, ai_risks
            ))
        for job in rejected:
            conn.execute("""
            INSERT OR REPLACE INTO rejected_jobs (job_key, source, title, company, location, url, reject_reason, seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (job.job_key, job.source, job.title, job.company, job.location, job.url, job.reject_reason, run_at))
        for status in statuses:
            conn.execute("""
            INSERT OR REPLACE INTO source_state (source, last_checked, last_status, last_detail, fetched, kept, rejected)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (status.source, status.finished_at or run_at, status.status, status.detail, status.fetched, status.kept, status.rejected))
        strong = sum(1 for j in accepted if j.fit == "Strong fit")
        maybe = sum(1 for j in accepted if j.fit == "Maybe")
        low = sum(1 for j in accepted if j.fit == "Low fit")
        conn.execute("INSERT INTO run_history (run_at, fetched, kept, rejected, strong, maybe, low) VALUES (?, ?, ?, ?, ?, ?, ?)", (run_at, len(accepted) + len(rejected), len(accepted), len(rejected), strong, maybe, low))
        conn.commit()
        return {"new": new_count, "kept": len(accepted), "rejected": len(rejected), "fetched": len(accepted) + len(rejected)}
    finally:
        conn.close()


def source_recently_checked(source: str, config: Dict[str, Any]) -> Tuple[bool, str]:
    init_db()
    cooldowns = config.get("source_cooldown_minutes", {}) if isinstance(config.get("source_cooldown_minutes"), dict) else {}
    minutes = int(cooldowns.get(source, 0) or 0)
    if minutes <= 0:
        return False, ""
    conn = connect_db()
    try:
        row = conn.execute("SELECT last_checked FROM source_state WHERE source = ?", (source,)).fetchone()
        if not row or not row["last_checked"]:
            return False, ""
        last = parse_iso(row["last_checked"])
        if not last:
            return False, ""
        next_allowed = last + timedelta(minutes=minutes)
        if datetime.now(timezone.utc) < next_allowed:
            return True, f"cooldown active until {next_allowed.strftime('%Y-%m-%d %H:%M UTC')}"
        return False, ""
    finally:
        conn.close()


def dedupe_jobs(jobs: List[Job]) -> List[Job]:
    seen: Dict[str, Job] = {}
    for job in jobs:
        job.job_key = make_key(job)
        current = seen.get(job.job_key)
        if not current or job.score > current.score:
            seen[job.job_key] = job
    return list(seen.values())


def run_sources(force: bool = False, only_sources: Optional[List[str]] = None) -> Dict[str, Any]:
    load_dotenv(ROOT / ".env")
    config = load_config()
    init_db()
    add_missing_columns()
    all_raw: List[Job] = []
    statuses: List[SourceStatus] = []
    sources_to_run = only_sources or list(FETCHERS.keys())

    for source_name in sources_to_run:
        if source_name not in FETCHERS:
            continue
        if not source_enabled(config, source_name):
            statuses.append(SourceStatus(source_name, "skipped", "source disabled in config", skipped_by_cooldown=False))
            continue
        if not force:
            recent, detail = source_recently_checked(source_name, config)
            if recent:
                statuses.append(SourceStatus(source_name, "skipped", detail, skipped_by_cooldown=True, finished_at=now_utc_iso()))
                continue
        status = SourceStatus(source_name, "running", "started", started_at=now_utc_iso())
        try:
            fetched, fetch_status = FETCHERS[source_name](config)
            all_raw.extend(fetched)
            status = fetch_status
        except Exception as exc:
            status = SourceStatus(source_name, "error", str(exc))
        status.started_at = status.started_at or now_utc_iso()
        status.finished_at = now_utc_iso()
        statuses.append(status)

    scored: List[Job] = [score_job(j, config) for j in all_raw if j.title and j.url]
    scored = dedupe_jobs(scored)
    min_score = int(config.get("min_score_to_show", 50))
    strict = bool(config.get("strict_us_remote_only", True))
    accepted: List[Job] = []
    rejected: List[Job] = []
    for job in scored:
        if strict and not job.is_us_remote:
            rejected.append(job)
            continue
        if job.score < min_score:
            job.reject_reason = job.reject_reason or f"below minimum score {min_score}"
            rejected.append(job)
            continue
        accepted.append(job)
    accepted.sort(key=lambda j: (j.score, j.posted_at or ""), reverse=True)

    status_by_source: Dict[str, SourceStatus] = {s.source: s for s in statuses}
    for job in accepted:
        s = status_by_source.get(job.source)
        if s:
            s.kept += 1
    for job in rejected:
        s = status_by_source.get(job.source)
        if s:
            s.rejected += 1

    stats = upsert_jobs(accepted, rejected, statuses)
    write_csv_from_db(OUTPUT_DIR / "jobs_latest.csv")
    write_csv_from_db(OUTPUT_DIR / "jobs_shortlist.csv", user_status="shortlisted")
    write_rejected_csv(OUTPUT_DIR / "jobs_rejected_location_audit.csv")
    maybe_send_alerts(config)
    return {"ok": True, "stats": stats, "statuses": [asdict(s) for s in statuses]}


def rows_to_jobs(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


def query_jobs(filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    init_db()
    filters = filters or {}
    where = []
    params: List[Any] = []
    status = filters.get("status")
    if status and status != "all":
        where.append("user_status = ?")
        params.append(status)
    fit = filters.get("fit")
    if fit:
        where.append("fit = ?")
        params.append(fit)
    source = filters.get("source")
    if source:
        where.append("source = ?")
        params.append(source)
    q = normalize(filters.get("q", ""))
    if q:
        where.append("LOWER(title || ' ' || company || ' ' || location || ' ' || matched_keywords || ' ' || description) LIKE ?")
        params.append(f"%{q}%")
    include_hidden = bool(filters.get("include_hidden"))
    if not include_hidden:
        where.append("COALESCE(user_status, '') != 'hidden'")
    sql = "SELECT * FROM jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sort = filters.get("sort", "score")
    if sort == "new":
        sql += " ORDER BY is_new DESC, first_seen DESC, score DESC"
    elif sort == "company":
        sql += " ORDER BY company ASC, score DESC"
    elif sort == "posted":
        sql += " ORDER BY posted_at DESC, score DESC"
    else:
        sql += " ORDER BY score DESC, first_seen DESC"
    limit = int(filters.get("limit") or network_config(load_config()).get("dashboard_page_size", 60))
    sql += " LIMIT ?"
    params.append(limit)
    conn = connect_db()
    try:
        return rows_to_jobs(conn.execute(sql, params).fetchall())
    finally:
        conn.close()


def get_dashboard_stats() -> Dict[str, Any]:
    init_db()
    conn = connect_db()
    try:
        total = conn.execute("SELECT COUNT(*) c FROM jobs WHERE COALESCE(user_status, '') != 'hidden'").fetchone()["c"]
        strong = conn.execute("SELECT COUNT(*) c FROM jobs WHERE fit = 'Strong fit' AND COALESCE(user_status, '') != 'hidden'").fetchone()["c"]
        new = conn.execute("SELECT COUNT(*) c FROM jobs WHERE user_status = 'new'").fetchone()["c"]
        shortlisted = conn.execute("SELECT COUNT(*) c FROM jobs WHERE user_status = 'shortlisted'").fetchone()["c"]
        applied = conn.execute("SELECT COUNT(*) c FROM jobs WHERE user_status = 'applied'").fetchone()["c"]
        rejected = conn.execute("SELECT COUNT(*) c FROM rejected_jobs").fetchone()["c"]
        sources = [dict(row) for row in conn.execute("SELECT * FROM source_state ORDER BY source").fetchall()]
        runs = [dict(row) for row in conn.execute("SELECT * FROM run_history ORDER BY run_id DESC LIMIT 8").fetchall()]
        source_names = [row["source"] for row in conn.execute("SELECT DISTINCT source FROM jobs ORDER BY source").fetchall()]
        return {"total": total, "strong": strong, "new": new, "shortlisted": shortlisted, "applied": applied, "rejected": rejected, "sources": sources, "runs": runs, "source_names": source_names}
    finally:
        conn.close()


def update_job_status(job_key: str, status: str) -> bool:
    allowed = {"new", "seen", "shortlisted", "applied", "rejected", "hidden", "saved"}
    if status not in allowed:
        return False
    init_db()
    conn = connect_db()
    try:
        cur = conn.execute("UPDATE jobs SET user_status = ?, is_new = CASE WHEN ? = 'new' THEN is_new ELSE 0 END WHERE job_key = ?", (status, status, job_key))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_job(job_key: str) -> Optional[Dict[str, Any]]:
    init_db()
    conn = connect_db()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_recent_rejected(limit: int = 100) -> List[Dict[str, Any]]:
    init_db()
    conn = connect_db()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM rejected_jobs ORDER BY seen_at DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        conn.close()


def write_csv_from_db(path: Path, user_status: Optional[str] = None) -> None:
    filters = {"limit": 10000, "include_hidden": True}
    if user_status:
        filters["status"] = user_status
    rows = query_jobs(filters)
    fieldnames = list(rows[0].keys()) if rows else list(asdict(Job("", "", "", "", "", "", "", "", "")).keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_rejected_csv(path: Path) -> None:
    rows = get_recent_rejected(limit=10000)
    fieldnames = list(rows[0].keys()) if rows else ["job_key", "source", "title", "company", "location", "url", "reject_reason", "seen_at"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def run_ai_review(job_key: str) -> Dict[str, Any]:
    load_dotenv(ROOT / ".env")
    config = load_config()
    ai_cfg = config.get("ai_review", {}) if isinstance(config.get("ai_review"), dict) else {}
    job = get_job(job_key)
    if not job:
        return {"ok": False, "error": "job not found"}
    api_key = os.getenv("OPENAI_API_KEY", "").strip() or os.getenv("AI_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY or AI_API_KEY is not set in .env"}
    api_url = os.getenv("AI_API_URL", "").strip() or ai_cfg.get("api_url") or "https://api.openai.com/v1/chat/completions"
    model = os.getenv("AI_MODEL", "").strip() or ai_cfg.get("model") or "gpt-4o-mini"
    prompt = f"""
You are reviewing a remote U.S. job for Ryan, who is targeting AI analytics, risk, fraud, fintech, trust and safety, crypto operations, product operations, and data analyst roles.
Return strict JSON only with these keys: ai_score, summary, resume_keywords, resume_angle, cover_letter_angle, risks.
Score from 0 to 100. Be honest and do not oversell.

Title: {job.get('title')}
Company: {job.get('company')}
Location: {job.get('location')}
Salary: {job.get('salary')}
Current keyword score: {job.get('score')}
Matched keywords: {job.get('matched_keywords')}
Description: {str(job.get('description') or '')[:4500]}
""".strip()
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "You are a precise job fit reviewer. Return valid JSON only."},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"}
    }
    try:
        response = requests.post(api_url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    ai_score = parsed.get("ai_score")
    try:
        ai_score = int(ai_score)
    except Exception:
        ai_score = None
    conn = connect_db()
    try:
        conn.execute("""
        UPDATE jobs SET ai_score = ?, ai_summary = ?, ai_resume_keywords = ?, ai_resume_angle = ?, ai_cover_letter_angle = ?, ai_risks = ?
        WHERE job_key = ?
        """, (
            ai_score,
            clean_text(parsed.get("summary"), limit=1500),
            clean_text(parsed.get("resume_keywords"), limit=1200),
            clean_text(parsed.get("resume_angle"), limit=1500),
            clean_text(parsed.get("cover_letter_angle"), limit=1500),
            clean_text(parsed.get("risks"), limit=1500),
            job_key,
        ))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "review": parsed}


def send_telegram_message(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=10)
        return True
    except Exception:
        return False


def maybe_send_alerts(config: Dict[str, Any]) -> None:
    threshold = int(config.get("alert_score_threshold", 82))
    rows = query_jobs({"status": "new", "limit": 10, "sort": "score"})
    good = [r for r in rows if int(r.get("score") or 0) >= threshold]
    if not good:
        return
    lines = ["Remote Role Agent found strong new roles:"]
    for row in good[:5]:
        lines.append(f"{row.get('score')} | {row.get('title')} | {row.get('company')} | {row.get('url')}")
    send_telegram_message("\n".join(lines))


def cli() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Remote Role Agent v3")
    parser.add_argument("--run", action="store_true", help="run job search once")
    parser.add_argument("--force", action="store_true", help="ignore source cooldowns")
    parser.add_argument("--source", action="append", help="run only this source, can be repeated")
    args = parser.parse_args()
    if args.run:
        result = run_sources(force=args.force, only_sources=args.source)
        print(json.dumps(result, indent=2))
    else:
        print("Use app.py for the local dashboard or pass --run to fetch jobs once.")


if __name__ == "__main__":
    cli()
