from __future__ import annotations

import hashlib
import imaplib
import json
import os
import re
import time
import tomllib
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from job_scraper.config import HttpConfig
from job_scraper.domain.url_resolution import resolve_external_application_url
from job_scraper.models import RawJobRecord
from job_scraper.pipeline.normalize import normalize_whitespace

DEFAULT_SUBJECT_KEYWORDS: tuple[str, ...] = ()

GENERIC_LINK_LABELS = {
    "alle jobs anzeigen",
    "apply",
    "apply now",
    "der letzten 7 tage",
    "details",
    "e-mail einstellungen",
    "e-mail-einstellungen verwalten",
    "email einstellungen",
    "email settings",
    "learn more",
    "meine jobs",
    "more",
    "open",
    "read more",
    "see details",
    "see job",
    "see jobs",
    "seit gestern",
    "show more",
    "view all jobs",
    "view",
    "view job",
    "view jobs",
    "weitere passende jobs anzeigen",
    "jetzt bewerben",
}

BAD_URL_TOKENS = (
    "account",
    "alert",
    "apps.microsoft.com",
    "auth",
    "email-preference",
    "email_preference",
    "help",
    "itunes.apple.com",
    "login",
    "notification",
    "password",
    "play.google.com",
    "preferences",
    "privacy",
    "profile",
    "settings",
    "subscription",
    "terms",
    "unsubscribe",
)

JOB_URL_TOKENS = (
    "ashbyhq.com",
    "careers",
    "efinancialcareers",
    "greenhouse.io",
    "jobs",
    "lever.co",
    "linkedin.com/jobs",
    "personio",
    "position",
    "softgarden",
    "recruitee",
    "smartrecruiters",
    "stepstone",
    "successfactors",
    "instaffo",
    "join.com",
    "myworkdayjobs",
    "workday",
    "workable",
)

ROLE_PATTERN = re.compile(
    r"\b([A-Za-z][\w+/#().-]*(?:\s+[A-Za-z][\w+/#().-]*){0,10}\s+"
    r"(?:engineer|developer|scientist|architect|consultant|specialist|programmer|"
    r"analyst|tester|administrator|technician|manager|coordinator|planner|writer|author))\b",
    flags=re.IGNORECASE,
)

LOCATION_LABELS = {
    "aachen": "Aachen",
    "berlin": "Berlin",
    "bonn": "Bonn",
    "cologne": "Cologne",
    "darmstadt": "Darmstadt",
    "dortmund": "Dortmund",
    "dresden": "Dresden",
    "dusseldorf": "Dusseldorf",
    "duesseldorf": "Dusseldorf",
    "essen": "Essen",
    "frankfurt": "Frankfurt",
    "hamburg": "Hamburg",
    "hannover": "Hannover",
    "heidelberg": "Heidelberg",
    "karlsruhe": "Karlsruhe",
    "koln": "Cologne",
    "koeln": "Cologne",
    "leipzig": "Leipzig",
    "mainz": "Mainz",
    "mannheim": "Mannheim",
    "munich": "Munich",
    "muenchen": "Munich",
    "munchen": "Munich",
    "nuremberg": "Nuremberg",
    "nuernberg": "Nuremberg",
    "stuttgart": "Stuttgart",
    "ulm": "Ulm",
    "wiesbaden": "Wiesbaden",
    "amsterdam": "Amsterdam",
    "rotterdam": "Rotterdam",
    "utrecht": "Utrecht",
    "eindhoven": "Eindhoven",
    "dublin": "Dublin",
    "cork": "Cork",
    "galway": "Galway",
    "copenhagen": "Copenhagen",
    "kobenhavn": "Copenhagen",
    "aarhus": "Aarhus",
    "stockholm": "Stockholm",
    "gothenburg": "Gothenburg",
    "goteborg": "Gothenburg",
    "malmo": "Malmo",
    "lund": "Lund",
    "luxembourg": "Luxembourg",
    "luxemburg": "Luxembourg",
    "vienna": "Vienna",
    "wien": "Vienna",
    "graz": "Graz",
    "linz": "Linz",
    "brussels": "Brussels",
    "bruxelles": "Brussels",
    "antwerp": "Antwerp",
    "antwerpen": "Antwerp",
    "ghent": "Ghent",
    "gent": "Ghent",
}

COUNTRY_LOCATION_LABELS = {
    "remote germany": "Remote Germany",
    "germany": "Germany",
    "deutschland": "Germany",
    "remote netherlands": "Remote Netherlands",
    "netherlands": "Netherlands",
    "nederland": "Netherlands",
    "niederlande": "Netherlands",
    "remote ireland": "Remote Ireland",
    "ireland": "Ireland",
    "irland": "Ireland",
    "remote denmark": "Remote Denmark",
    "denmark": "Denmark",
    "danmark": "Denmark",
    "remote sweden": "Remote Sweden",
    "sweden": "Sweden",
    "sverige": "Sweden",
    "remote luxembourg": "Remote Luxembourg",
    "remote austria": "Remote Austria",
    "austria": "Austria",
    "osterreich": "Austria",
    "remote belgium": "Remote Belgium",
    "belgium": "Belgium",
    "belgie": "Belgium",
    "belgien": "Belgium",
}


@dataclass(slots=True)
class EmailIngestConfig:
    host: str
    port: int
    username: str
    password: str
    folder: str
    use_ssl: bool
    lookback_days: int
    max_messages: int
    subject_keywords: list[str]
    sender_allowlist: list[str]
    state_path: Path
    track_config_paths: list[Path]
    username_env: str = ""
    password_env: str = ""


@dataclass(slots=True)
class MailMessage:
    uid: str
    message_id: str
    subject: str
    sender: str
    received_at: datetime
    text: str
    html: str


@dataclass(slots=True)
class LinkCandidate:
    url: str
    label: str
    context: str


@dataclass(slots=True)
class EmailJobCandidate:
    url: str
    title: str
    company_name: str
    location_raw: str
    context: str
    message_id: str
    email_subject: str
    email_from: str
    email_date: datetime
    anchor_text: str = ""


@dataclass(slots=True)
class JobDetail:
    final_url: str
    title: str = ""
    company_name: str = ""
    location_raw: str = ""
    description: str = ""
    application_url: str = ""
    company_url: str = ""
    posted_at_text: str = ""
    raw_payload: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class EmailIngestState:
    path: Path
    processed_messages: dict[str, dict[str, object]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> EmailIngestState:
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls(path=path)
        messages = raw.get("processed_messages", {})
        if not isinstance(messages, dict):
            messages = {}
        return cls(path=path, processed_messages=messages)

    def is_processed(self, message_id: str) -> bool:
        return normalize_message_id(message_id) in self.processed_messages

    def mark_processed(self, message: MailMessage, accepted_jobs: int) -> None:
        message_id = normalize_message_id(message.message_id)
        if not message_id:
            return
        self.processed_messages[message_id] = {
            "processed_at": datetime.now(UTC).isoformat(),
            "accepted_jobs": accepted_jobs,
            "subject": message.subject[:200],
            "from": message.sender[:200],
            "email_date": message.received_at.isoformat(),
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "processed_messages": self.processed_messages,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8"
        )


class ImapEmailClient:
    def __init__(self, config: EmailIngestConfig) -> None:
        self.config = config

    def fetch_recent_messages(self) -> list[MailMessage]:
        if not self.config.username or not self.config.password:
            raise RuntimeError(
                "Email credentials are missing. Set the configured username/password environment variables."
            )
        if self.config.use_ssl:
            connection: imaplib.IMAP4 = imaplib.IMAP4_SSL(self.config.host, self.config.port)
        else:
            connection = imaplib.IMAP4(self.config.host, self.config.port)
        try:
            connection.login(self.config.username, self.config.password)
            status, _ = connection.select(format_imap_mailbox(self.config.folder), readonly=True)
            if status != "OK":
                raise RuntimeError(f"Could not open IMAP folder {self.config.folder!r}")
            cutoff = datetime.now(UTC) - timedelta(days=self.config.lookback_days)
            since = cutoff.strftime("%d-%b-%Y")
            status, search_data = connection.uid("SEARCH", None, "SINCE", since)
            if status != "OK" or not search_data:
                return []
            uids = search_data[0].split()
            if self.config.max_messages > 0:
                uids = uids[-self.config.max_messages :]

            messages: list[MailMessage] = []
            for uid in uids:
                status, fetch_data = connection.uid(
                    "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])"
                )
                if status != "OK":
                    continue
                header_bytes = extract_fetch_bytes(fetch_data)
                if not header_bytes:
                    continue
                header_message = parse_mail_headers(
                    header_bytes, uid.decode("ascii", errors="replace")
                )
                if not message_matches_filters(
                    header_message,
                    self.config.subject_keywords,
                    self.config.sender_allowlist,
                ):
                    continue
                status, fetch_data = connection.uid("FETCH", uid, "(BODY.PEEK[])")
                if status != "OK":
                    continue
                raw_bytes = extract_fetch_bytes(fetch_data)
                if not raw_bytes:
                    continue
                message = parse_mail_message(raw_bytes, uid.decode("ascii", errors="replace"))
                if message.received_at < cutoff:
                    continue
                messages.append(message)
            return messages
        finally:
            with suppress(Exception):
                connection.logout()


def format_imap_mailbox(value: str) -> str:
    mailbox = str(value or "INBOX").strip() or "INBOX"
    if mailbox.startswith('"') and mailbox.endswith('"'):
        return mailbox
    if re.search(r"\s", mailbox):
        escaped = mailbox.replace("\\", "\\\\").replace('"', r"\"")
        return f'"{escaped}"'
    return mailbox


def load_email_ingest_config(path: str | Path) -> EmailIngestConfig:
    config_path = Path(path)
    config_root = config_path.parent
    raw = load_toml_with_local_overrides(config_path)
    mailbox = raw.get("mailbox", {})
    tracks = raw.get("tracks", {})

    username = str(mailbox.get("username", "")).strip()
    password = str(mailbox.get("password", "")).strip()
    username_env = str(mailbox.get("username_env", "")).strip()
    password_env = str(mailbox.get("password_env", "")).strip()
    if not username and username_env:
        username = os.getenv(username_env, "").strip()
    if not password and password_env:
        password = os.getenv(password_env, "").strip()

    track_values = tracks.get("config_paths", [])
    track_config_paths = [resolve_config_path(config_root, value) for value in track_values]
    subject_keywords = [
        str(value).strip().lower()
        for value in mailbox.get("subject_keywords", DEFAULT_SUBJECT_KEYWORDS)
    ]
    sender_allowlist = [str(value).strip().lower() for value in mailbox.get("sender_allowlist", [])]

    return EmailIngestConfig(
        host=str(mailbox.get("host", "")).strip(),
        port=int(mailbox.get("port", 993)),
        username=username,
        password=password,
        folder=str(mailbox.get("folder", "INBOX")).strip() or "INBOX",
        use_ssl=bool(mailbox.get("use_ssl", True)),
        lookback_days=int(mailbox.get("lookback_days", 7)),
        max_messages=int(mailbox.get("max_messages", 50)),
        subject_keywords=[value for value in subject_keywords if value],
        sender_allowlist=[value for value in sender_allowlist if value],
        state_path=resolve_config_path(
            config_root, mailbox.get("state_path", "../data/email_ingest_state.json")
        ),
        track_config_paths=track_config_paths,
        username_env=username_env,
        password_env=password_env,
    )


def load_toml_with_local_overrides(config_path: Path) -> dict:
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    local_path = config_path.with_name(f"{config_path.stem}.local{config_path.suffix}")
    if not local_path.exists():
        return raw
    with local_path.open("rb") as handle:
        local_raw = tomllib.load(handle)
    return merge_nested_dicts(raw, local_raw)


def merge_nested_dicts(base: dict, overrides: dict) -> dict:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_nested_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_config_path(config_root: Path, value: object) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return (config_root / path).resolve()


def extract_fetch_bytes(fetch_data: object) -> bytes:
    if not isinstance(fetch_data, list):
        return b""
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return b""


def parse_mail_message(raw_bytes: bytes, uid: str) -> MailMessage:
    parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    message_id = normalize_message_id(str(parsed.get("Message-ID", ""))) or f"imap-uid:{uid}"
    subject = normalize_whitespace(str(parsed.get("Subject", "")))
    sender = normalize_whitespace(str(parsed.get("From", "")))
    received_at = parse_email_date(str(parsed.get("Date", "")))
    text, html = extract_message_bodies(parsed)
    return MailMessage(
        uid=uid,
        message_id=message_id,
        subject=subject,
        sender=sender,
        received_at=received_at,
        text=text,
        html=html,
    )


def parse_mail_headers(raw_bytes: bytes, uid: str) -> MailMessage:
    parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    message_id = normalize_message_id(str(parsed.get("Message-ID", ""))) or f"imap-uid:{uid}"
    return MailMessage(
        uid=uid,
        message_id=message_id,
        subject=normalize_whitespace(str(parsed.get("Subject", ""))),
        sender=normalize_whitespace(str(parsed.get("From", ""))),
        received_at=parse_email_date(str(parsed.get("Date", ""))),
        text="",
        html="",
    )


def normalize_message_id(value: str) -> str:
    return str(value or "").strip().strip("<>").strip()


def parse_email_date(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def extract_message_bodies(message: Message) -> tuple[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    for part in message.walk() if message.is_multipart() else [message]:
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", "")).lower()
        if "attachment" in disposition:
            continue
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                continue
            charset = part.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="replace")
        if content_type == "text/plain":
            text_parts.append(str(content))
        else:
            html_parts.append(str(content))
    return "\n".join(text_parts), "\n".join(html_parts)


def message_matches_filters(
    message: MailMessage, subject_keywords: list[str], sender_allowlist: list[str]
) -> bool:
    sender = message.sender.lower()
    if sender_allowlist and not any(allowed in sender for allowed in sender_allowlist):
        return False
    if not subject_keywords:
        return True
    searchable = message.subject.lower()
    return any(keyword in searchable for keyword in subject_keywords)


def extract_job_candidates(message: MailMessage) -> list[EmailJobCandidate]:
    full_text = normalize_whitespace(
        "\n".join(
            part for part in [message.subject, message.text, html_to_text(message.html)] if part
        )
    )
    links = extract_links(message.text, message.html)
    candidates: list[EmailJobCandidate] = []
    seen_urls: set[str] = set()
    for link in links:
        url = clean_url(link.url)
        if not url or should_skip_url(url):
            continue
        context = normalize_whitespace(link.context or full_text)
        title, company_from_title = infer_title(link.label, context, url, message.subject)
        if not title:
            continue
        company = company_from_title or infer_company(context, title, message.sender)
        location = infer_location(context, full_text)
        if not is_jobish_candidate(url, title, context):
            continue
        dedupe_url = canonical_link_key(url)
        if dedupe_url in seen_urls:
            continue
        seen_urls.add(dedupe_url)
        candidates.append(
            EmailJobCandidate(
                url=url,
                title=title,
                company_name=company,
                location_raw=location,
                context=context[:1800],
                message_id=message.message_id,
                email_subject=message.subject,
                email_from=message.sender,
                email_date=message.received_at,
                anchor_text=normalize_whitespace(link.label),
            )
        )
    return candidates


def email_candidate_to_raw_job(
    candidate: EmailJobCandidate, scraped_at: datetime | None = None
) -> RawJobRecord:
    observed_at = scraped_at or datetime.now(UTC)
    source_job_id = stable_source_job_id(candidate)
    return RawJobRecord(
        source="email",
        source_job_id=source_job_id,
        source_url=candidate.url,
        canonical_url=candidate.url,
        title=candidate.title,
        company_name=candidate.company_name or "Unknown",
        location_raw=candidate.location_raw,
        posted_at_text=candidate.email_date.isoformat(),
        scraped_at=observed_at,
        job_description="",
        application_url="",
        raw_payload={
            "freshness_basis": "email_received",
            "acquisition_mode": "email",
            "source_platforms": [job_platform_from_url(candidate.url)],
            "detail_status": "not_fetched",
            "description_source": "none",
            "title_source": "email_card",
            "email_message_id": candidate.message_id,
            "email_subject": candidate.email_subject,
            "email_from": candidate.email_from,
            "email_date": candidate.email_date.isoformat(),
            "anchor_text": candidate.anchor_text,
            "card_context": candidate.context,
            "email_candidate_title": candidate.title,
            "email_candidate_company": candidate.company_name,
            "email_candidate_location": candidate.location_raw,
            "canonical_link_key": canonical_link_key(candidate.url),
        },
    )


def job_platform_from_url(url: str) -> str:
    lowered = (url or "").lower()
    for platform in (
        "linkedin",
        "indeed",
        "efinancialcareers",
        "stepstone",
        "xing",
        "glassdoor",
    ):
        if platform in lowered:
            return platform
    return "unknown"


def enrich_email_candidate_to_raw_job(
    candidate: EmailJobCandidate,
    http_config: HttpConfig,
    scraped_at: datetime | None = None,
) -> RawJobRecord:
    raw = email_candidate_to_raw_job(candidate, scraped_at=scraped_at)
    try:
        detail = fetch_job_detail(candidate.url, http_config)
    except Exception as exc:
        raw.raw_payload["detail_status"] = "fetch_failed"
        raw.raw_payload["detail_error"] = str(exc)
        if can_use_email_fallback(candidate, raw):
            raw.raw_payload["detail_status"] = "email_fallback"
        return raw

    if detail.title:
        raw.title = detail.title
        raw.raw_payload["title_source"] = "job_detail"
    if detail.company_name:
        raw.company_name = detail.company_name
    if detail.location_raw:
        raw.location_raw = detail.location_raw
    if has_usable_detail_text(detail.description):
        raw.job_description = detail.description
        raw.raw_payload["description_source"] = "job_detail"
    if detail.application_url:
        raw.application_url = resolve_external_application_url(
            candidate.url, detail.application_url
        )
    if detail.company_url:
        raw.company_url = detail.company_url
    if detail.posted_at_text:
        raw.posted_at_text = detail.posted_at_text
    if detail.final_url:
        raw.canonical_url = detail.final_url
        raw.raw_payload["detail_final_url"] = detail.final_url
    raw.raw_payload["detail_status"] = (
        "ok"
        if has_usable_detail_text(detail.description)
        else "email_fallback"
        if can_use_email_fallback(candidate, raw)
        else "too_sparse"
    )
    raw.raw_payload.update(detail.raw_payload)
    return raw


def has_usable_detail_text(value: str) -> bool:
    return len(normalize_whitespace(value)) >= 120


def can_use_email_fallback(candidate: EmailJobCandidate, raw: RawJobRecord) -> bool:
    if not candidate.title or is_generic_label(candidate.title):
        return False
    if not is_jobish_candidate(candidate.url, candidate.title, candidate.context):
        return False
    anchor_title = cleanup_title(candidate.anchor_text)
    url_title = cleanup_title(title_from_url(candidate.url))
    return bool(
        (anchor_title and not is_generic_label(anchor_title))
        or (url_title and not is_generic_label(url_title))
    )


def fetch_job_detail(url: str, http_config: HttpConfig) -> JobDetail:
    fetch_url = detail_fetch_url(url)
    html, final_url = fetch_text(fetch_url, http_config)
    detail = parse_job_detail_html(html, final_url or url)
    if not detail.application_url:
        detail.application_url = final_url or url
    return detail


def detail_fetch_url(url: str) -> str:
    cleaned = clean_url(url)
    job_id = extract_linkedin_job_id(cleaned)
    if job_id:
        return f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    indeed_url = indeed_detail_url(cleaned)
    if indeed_url:
        return indeed_url
    return cleaned


def indeed_detail_url(url: str) -> str:
    if not is_indeed_url(url):
        return ""
    split = urlsplit(url)
    query = dict(parse_qsl(split.query, keep_blank_values=False))
    job_key = str(query.get("jk") or "").strip()
    if not job_key:
        return ""
    scheme = split.scheme or "https"
    host = split.netloc or "de.indeed.com"
    return urlunsplit((scheme, host, "/viewjob", urlencode({"jk": job_key}), ""))


def is_indeed_url(url: str) -> bool:
    return "indeed.com" in url.lower()


def extract_linkedin_job_id(url: str) -> str:
    if "linkedin.com" not in url.lower():
        return ""
    for pattern in (r"/jobs/view/[^/?-]*-?(\d+)", r"[?&]currentJobId=(\d+)", r"[?&]jobId=(\d+)"):
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


def platform_job_reference(url: str) -> tuple[str, str]:
    cleaned = clean_url(url)
    if is_indeed_url(cleaned):
        query = dict(parse_qsl(urlsplit(cleaned).query, keep_blank_values=False))
        return "indeed", str(query.get("jk") or "").strip()
    linkedin_id = extract_linkedin_job_id(cleaned)
    if linkedin_id:
        return "linkedin", linkedin_id
    return "", ""


def fetch_text(url: str, http_config: HttpConfig) -> tuple[str, str]:
    last_error: Exception | None = None
    max_retries = 0
    timeout_seconds = min(http_config.timeout_seconds, 6)
    for attempt in range(max_retries + 1):
        request = Request(url, headers={"User-Agent": http_config.user_agent})
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                final_url = response.geturl()
            lowered = body.lower()
            if "captcha" in lowered or "unusual traffic" in lowered:
                raise RuntimeError("detail page returned a blocking page")
            return body, final_url
        except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep((attempt + 1) * http_config.base_delay_seconds)
    if last_error:
        raise last_error
    raise RuntimeError("detail fetch failed without an explicit error")


def parse_job_detail_html(html: str, final_url: str) -> JobDetail:
    payload = extract_json_ld_jobposting(html)
    if payload:
        return detail_from_json_ld(payload, final_url)

    title = first_non_empty(
        extract_meta_content(html, "og:title"),
        extract_meta_content(html, "twitter:title"),
        extract_title_tag(html),
        extract_first_heading(html),
    )
    description = first_non_empty(
        extract_meta_content(html, "description"),
        extract_meta_content(html, "og:description"),
        html_to_text(remove_noise_html(html)),
    )
    title, company = split_title_company(title)
    return JobDetail(
        final_url=final_url,
        title=title,
        company_name=company,
        description=description,
        application_url=final_url,
        raw_payload={"detail_parser": "html"},
    )


def extract_json_ld_jobposting(html: str) -> dict | None:
    for payload in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(unescape(payload).strip())
        except json.JSONDecodeError:
            continue
        found = find_jobposting_payload(data)
        if found:
            return found
    return None


def find_jobposting_payload(value: object) -> dict | None:
    if isinstance(value, list):
        for item in value:
            found = find_jobposting_payload(item)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    raw_type = value.get("@type")
    types = raw_type if isinstance(raw_type, list) else [raw_type]
    if any(str(item).lower() == "jobposting" for item in types):
        return value
    graph = value.get("@graph")
    if graph is not None:
        return find_jobposting_payload(graph)
    return None


def detail_from_json_ld(payload: dict, final_url: str) -> JobDetail:
    locations = extract_job_locations(payload)
    organization = (
        payload.get("hiringOrganization")
        if isinstance(payload.get("hiringOrganization"), dict)
        else {}
    )
    company_name = str((organization or {}).get("name") or "").strip()
    company_url = str(
        (organization or {}).get("sameAs") or (organization or {}).get("url") or ""
    ).strip()
    application_url = str(payload.get("url") or payload.get("sameAs") or final_url).strip()
    posted_at = str(payload.get("datePosted") or "").strip()
    description = html_to_text(str(payload.get("description") or ""))
    return JobDetail(
        final_url=final_url,
        title=normalize_whitespace(str(payload.get("title") or "")),
        company_name=normalize_whitespace(company_name),
        location_raw=" | ".join(locations),
        description=description,
        application_url=application_url,
        company_url=company_url,
        posted_at_text=posted_at,
        raw_payload={
            "detail_parser": "json_ld",
            "location_options": locations,
            "location_country": extract_country(payload),
            "location_city": extract_city(payload),
        },
    )


def extract_job_locations(payload: dict) -> list[str]:
    job_locations = payload.get("jobLocation")
    options: list[str] = []
    for entry in iter_places(job_locations):
        location_text = format_place(entry)
        if location_text:
            options.append(location_text)
    if not options:
        city = extract_city(payload)
        country = extract_country(payload)
        parts = [part for part in [city, country_name(country)] if part]
        if parts:
            options.append(", ".join(parts))
    unique: list[str] = []
    seen: set[str] = set()
    for value in options:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def extract_country(payload: dict) -> str:
    for value in (payload.get("jobLocation"), payload.get("applicantLocationRequirements")):
        for entry in iter_places(value):
            address = entry.get("address") if isinstance(entry, dict) else {}
            country = str((address or {}).get("addressCountry") or "").strip()
            if country:
                return country
    return ""


def extract_city(payload: dict) -> str:
    for entry in iter_places(payload.get("jobLocation")):
        address = entry.get("address") if isinstance(entry, dict) else {}
        city = str((address or {}).get("addressLocality") or "").strip()
        if city:
            return city
    return ""


def iter_places(value: object) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def format_place(place: dict) -> str:
    address = place.get("address") if isinstance(place, dict) else {}
    if not isinstance(address, dict):
        return ""
    city = str(address.get("addressLocality") or "").strip()
    region = str(address.get("addressRegion") or "").strip()
    country = country_name(str(address.get("addressCountry") or "").strip())
    parts = [part for part in [city, region, country] if part]
    return ", ".join(parts)


def country_name(value: str) -> str:
    normalized = value.strip().upper()
    mapping = {
        "DE": "Germany",
        "GB": "United Kingdom",
        "UK": "United Kingdom",
        "RO": "Romania",
        "IN": "India",
        "PL": "Poland",
        "NL": "Netherlands",
        "CZ": "Czech Republic",
        "IT": "Italy",
        "CA": "Canada",
        "PT": "Portugal",
        "FR": "France",
        "CH": "Switzerland",
        "DK": "Denmark",
        "IE": "Ireland",
        "AT": "Austria",
        "BE": "Belgium",
        "LU": "Luxembourg",
        "SE": "Sweden",
        "VN": "Vietnam",
    }
    return mapping.get(normalized, value.strip())


def extract_meta_content(html: str, key: str) -> str:
    escaped_key = re.escape(key)
    patterns = [
        rf'<meta[^>]+(?:property|name)=["\']{escaped_key}["\'][^>]+content=["\'](?P<content>.*?)["\']',
        rf'<meta[^>]+content=["\'](?P<content>.*?)["\'][^>]+(?:property|name)=["\']{escaped_key}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return normalize_whitespace(unescape(match.group("content")))
    return ""


def extract_title_tag(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    return normalize_whitespace(unescape(match.group(1))) if match else ""


def extract_first_heading(html: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.IGNORECASE | re.DOTALL)
    return html_to_text(match.group(1)) if match else ""


def remove_noise_html(html: str) -> str:
    cleaned = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    cleaned = re.sub(r"<style[\s\S]*?</style>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<noscript[\s\S]*?</noscript>", " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def first_non_empty(*values: str) -> str:
    for value in values:
        cleaned = normalize_whitespace(value)
        if cleaned:
            return cleaned
    return ""


def stable_source_job_id(candidate: EmailJobCandidate) -> str:
    basis = "|".join(
        [
            canonical_link_key(candidate.url),
            normalize_whitespace(candidate.title).lower(),
            normalize_whitespace(candidate.company_name).lower(),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def extract_links(text: str, html: str) -> list[LinkCandidate]:
    links: list[LinkCandidate] = []
    if html:
        links.extend(extract_html_links(html))
    if text:
        links.extend(extract_plain_text_links(text))
    return links


def extract_html_links(html: str) -> list[LinkCandidate]:
    parser = LinkCollectingHTMLParser()
    parser.feed(html)
    return parser.link_candidates()


def extract_plain_text_links(text: str) -> list[LinkCandidate]:
    lines = text.splitlines()
    candidates: list[LinkCandidate] = []
    for index, line in enumerate(lines):
        for match in re.finditer(r"https?://[^\s<>\"]+", line):
            url = match.group(0).rstrip(").,;]")
            context_start = max(0, index - 3)
            context_end = min(len(lines), index + 4)
            context = "\n".join(lines[context_start:context_end])
            label = normalize_whitespace(line.replace(match.group(0), " "))
            candidates.append(LinkCandidate(url=url, label=label, context=context))
    return candidates


class LinkCollectingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_chunks: list[str] = []
        self._active_href = ""
        self._active_text: list[str] = []
        self._active_start = 0
        self._links: list[tuple[str, str, int, int]] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag.lower() != "a" or self._active_href:
            return
        href = dict(attrs).get("href") or ""
        self._active_href = href
        self._active_text = []
        self._active_start = len(self.text_chunks)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag.lower() != "a" or not self._active_href:
            return
        label = normalize_whitespace(" ".join(self._active_text))
        self._links.append((self._active_href, label, self._active_start, len(self.text_chunks)))
        self._active_href = ""
        self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        cleaned = normalize_whitespace(data)
        if not cleaned:
            return
        self.text_chunks.append(cleaned)
        if self._active_href:
            self._active_text.append(cleaned)

    def link_candidates(self) -> list[LinkCandidate]:
        candidates: list[LinkCandidate] = []
        for href, label, start, end in self._links:
            context_start = max(0, start - 12)
            context_end = min(len(self.text_chunks), end + 12)
            context = " ".join(self.text_chunks[context_start:context_end])
            candidates.append(LinkCandidate(url=href, label=label, context=context))
        return candidates


def html_to_text(html: str) -> str:
    if not html:
        return ""
    parser = LinkCollectingHTMLParser()
    parser.feed(html)
    return normalize_whitespace(" ".join(parser.text_chunks))


def clean_url(value: str) -> str:
    url = unescape(str(value or "")).strip()
    if not url.startswith(("http://", "https://")):
        return ""
    url = url.rstrip(").,;]")
    split = urlsplit(url)
    query_items = parse_qsl(split.query, keep_blank_values=False)
    for key, nested in query_items:
        if key.lower() in {"url", "u", "target", "targeturl", "redirect", "redirect_url", "q"}:
            nested_url = unquote(nested)
            if nested_url.startswith(("http://", "https://")):
                return clean_url(nested_url)
    filtered_query = [
        (key, val)
        for key, val in query_items
        if not key.lower().startswith("utm_")
        and key.lower()
        not in {
            "trk",
            "trackingid",
            "ref",
            "refid",
            "source",
            "sourceid",
            "email",
            "mid",
            "campaign",
        }
    ]
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(filtered_query), ""))


def should_skip_url(url: str) -> bool:
    lowered = url.lower()
    if not lowered.startswith(("http://", "https://")):
        return True
    host = urlsplit(lowered).netloc
    if host.endswith("post.spmailtechnolo.com") or host.endswith("emails.efinancialcareers.com"):
        return True
    if (
        "linkedin.com" in lowered
        and "/jobs/view/" not in lowered
        and "/comm/jobs/view/" not in lowered
    ):
        return True
    if is_indeed_url(lowered):
        return not is_indeed_job_url(lowered)
    return any(token in lowered for token in BAD_URL_TOKENS)


def is_indeed_job_url(url: str) -> bool:
    split = urlsplit(url)
    path = split.path.lower()
    query = dict(parse_qsl(split.query, keep_blank_values=False))
    if "jk" in query:
        return path.startswith(("/rc/clk", "/pagead/clk", "/viewjob"))
    if path.startswith("/pagead/clk"):
        return "ad" in query
    return False


def infer_title(label: str, context: str, url: str, subject: str) -> tuple[str, str]:
    label_title = cleanup_title(label)
    if label_title and not is_generic_label(label_title):
        title, company = split_title_company(label_title)
        if title:
            return title, company

    for source in (context, subject, title_from_url(url)):
        match = ROLE_PATTERN.search(source or "")
        if match:
            title, company = split_title_company(cleanup_title(match.group(1)))
            if title:
                return title, company

    url_title = cleanup_title(title_from_url(url))
    if url_title and not is_generic_label(url_title):
        return split_title_company(url_title)
    return "", ""


def cleanup_title(value: str) -> str:
    cleaned = normalize_whitespace(value)
    cleaned = re.sub(r"\s*\|\s*LinkedIn\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\|\s*Indeed\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" -|:•")
    if len(cleaned) > 180:
        cleaned = cleaned[:180].rsplit(" ", 1)[0]
    return cleaned


def split_title_company(value: str) -> tuple[str, str]:
    cleaned = cleanup_title(value)
    if not cleaned:
        return "", ""
    match = re.match(
        r"(?P<title>.+?)\s+(?:at|@)\s+(?P<company>[A-Za-z0-9][\w .,&+'/-]{1,80})$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if match:
        return cleanup_title(match.group("title")), cleanup_company(match.group("company"))
    for separator in (" - ", " | "):
        if separator not in cleaned:
            continue
        left, right = [part.strip() for part in cleaned.split(separator, 1)]
        if looks_like_role(left) and right and not looks_like_location(right):
            return cleanup_title(left), cleanup_company(right)
    return cleaned, ""


def is_generic_label(value: str) -> bool:
    normalized = normalize_whitespace(value).lower()
    return normalized in GENERIC_LINK_LABELS or len(normalized) < 5


def looks_like_role(value: str) -> bool:
    return ROLE_PATTERN.search(value or "") is not None


def title_from_url(url: str) -> str:
    split = urlsplit(url)
    path = unquote(split.path)
    parts = [part for part in re.split(r"[/_-]+", path) if part and not part.isdigit()]
    noise = {"apply", "careers", "job", "jobs", "position", "positions", "view"}
    parts = [part for part in parts if part.lower() not in noise]
    if not parts:
        return ""
    return normalize_whitespace(" ".join(parts[-10:])).title()


def infer_company(context: str, title: str, sender: str) -> str:
    if title:
        after_title = re.search(
            rf"{re.escape(normalize_whitespace(title))}\s+(?P<company>[A-Z][A-Za-z0-9][\w .,&+'/-]{{1,80}}?)"
            rf"(?:\s+\d(?:\.\d)?|\s+-\s+|\s+(?:{location_pattern()})\b|[,|•]|$)",
            normalize_whitespace(context or ""),
        )
        if after_title:
            company = cleanup_company(after_title.group("company"))
            if company:
                return company
    patterns = [
        r"\bCompany\s*:\s*(?P<company>[A-Za-z0-9][\w .,&+'/-]{1,80})",
        r"\bat\s+(?P<company>[A-Z][A-Za-z0-9][\w .,&+'/-]{1,80})(?:\s+(?:in|is|has|and)\b|[,|•-]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, context or "", flags=re.IGNORECASE)
        if match:
            company = cleanup_company(match.group("company"))
            if company and company.lower() not in {
                "germany",
                "berlin",
                "munich",
                "netherlands",
                "ireland",
                "denmark",
                "sweden",
                "luxembourg",
                "austria",
                "belgium",
            }:
                return company
    sender_domain = re.search(r"@([A-Za-z0-9.-]+)", sender or "")
    if sender_domain:
        domain = sender_domain.group(1).split(".")[0]
        if domain and domain.lower() not in {
            "mail",
            "email",
            "jobs",
            "notifications",
            "linkedin",
            "indeed",
        }:
            return domain.title()
    _ = title
    return "Unknown"


def cleanup_company(value: str) -> str:
    cleaned = normalize_whitespace(value)
    cleaned = re.split(r"\s+(?:in|is|has|and)\s+", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = re.split(r"\s*[|•]\s*", cleaned, maxsplit=1)[0]
    return cleaned.strip(" -,:")


def infer_location(context: str, full_text: str) -> str:
    for searchable in (context, full_text):
        explicit = re.search(
            r"\b(Location|Ort)\s*:\s*(?P<location>[A-Za-z ,/-]{2,80})",
            searchable or "",
            flags=re.IGNORECASE,
        )
        if explicit:
            location = cleanup_location(explicit.group("location"))
            if location:
                return location
        lowered = (searchable or "").lower()
        for key, label in LOCATION_LABELS.items():
            if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", lowered):
                return label
        for key, label in COUNTRY_LOCATION_LABELS.items():
            if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", lowered):
                return label
    return ""


def location_pattern() -> str:
    values = sorted(
        [*LOCATION_LABELS.keys(), *COUNTRY_LOCATION_LABELS.keys()], key=len, reverse=True
    )
    return "|".join(re.escape(value) for value in values)


def cleanup_location(value: str) -> str:
    cleaned = normalize_whitespace(value)
    cleaned = re.split(r"\s*[|•]\s*", cleaned, maxsplit=1)[0]
    cleaned = re.split(r"\s{2,}", cleaned, maxsplit=1)[0]
    return cleaned.strip(" -,:")


def looks_like_location(value: str) -> bool:
    lowered = normalize_whitespace(value).lower()
    return any(key in lowered for key in COUNTRY_LOCATION_LABELS) or any(
        key in lowered for key in LOCATION_LABELS
    )


def is_jobish_candidate(url: str, title: str, context: str) -> bool:
    lowered_url = url.lower()
    if any(token in lowered_url for token in JOB_URL_TOKENS):
        return True
    if looks_like_role(title):
        return True
    return ROLE_PATTERN.search(context or "") is not None


def canonical_link_key(url: str) -> str:
    split = urlsplit(clean_url(url) or url)
    return urlunsplit(
        (split.scheme.lower(), split.netloc.lower(), split.path.rstrip("/"), split.query, "")
    )
