from __future__ import annotations

import hashlib
import imaplib
import json
import os
import re
import tempfile
import time
import tomllib
import unicodedata
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from job_scraper.adapters.jobposting_jsonld import (
    extract_city,
    extract_country,
    extract_job_locations,
)
from job_scraper.adapters.jobposting_jsonld import (
    extract_jobposting as extract_json_ld_jobposting,
)
from job_scraper.config import HttpConfig
from job_scraper.domain.models import RawJobRecord
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
    "the hague": "The Hague",
    "hague": "The Hague",
    "maastricht": "Maastricht",
    "tilburg": "Tilburg",
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
    "paris": "Paris",
    "lyon": "Lyon",
    "lille": "Lille",
    "strasbourg": "Strasbourg",
    "metz": "Metz",
    "nancy": "Nancy",
    "zurich": "Zurich",
    "zuerich": "Zurich",
    "geneva": "Geneva",
    "geneve": "Geneva",
    "basel": "Basel",
    "bern": "Bern",
    "prague": "Prague",
    "praha": "Prague",
    "brno": "Brno",
    "warsaw": "Warsaw",
    "warszawa": "Warsaw",
    "krakow": "Krakow",
    "wroclaw": "Wroclaw",
    "poznan": "Poznan",
    "chicago": "Chicago",
    "st_louis": "St. Louis",
    "new_york": "New York",
    "atlanta": "Atlanta",
    "austin": "Austin",
    "abu_dhabi": "Abu Dhabi",
}

EFINANCIAL_LOCATION_ALIASES = {
    **LOCATION_LABELS,
    "brussel": "Brussels",
    "bruessel": "Brussels",
    "dusseldorf": "Dusseldorf",
    "florence": "Florence",
    "florenz": "Florence",
    "gurgaon": "Gurgaon",
    "london": "London",
    "mitte": "Mitte",
    "singapore": "Singapore",
    "singapur": "Singapore",
    "sydney": "Sydney",
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
    # Hosts that only ever serve tracking or unsubscribe redirects for this
    # mailbox's senders. Kept in the private workspace config: which bulk-mail
    # infrastructure a user's alerts arrive through is their detail, not a
    # property of the library.
    skipped_link_hosts: tuple[str, ...] = ()
    # Per-platform country widening, e.g. a board that lists a role in one
    # country while recruiting across a region. Private routing policy, so it
    # is configured rather than hard-coded.
    platform_country_scope: dict[str, tuple[str, ...]] = field(default_factory=dict)


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
    title: str = ""
    company_name: str = ""
    location_raw: str = ""
    description: str = ""
    posted_at_text: str = ""
    raw_payload: dict[str, object] = field(default_factory=dict)


# How long a processed-message record stays useful. The mailbox is only ever
# searched back `lookback_days`, so a record older than this can never suppress
# anything again -- keeping it forever just grows the state file without bound.
PROCESSED_MESSAGE_RETENTION = timedelta(days=90)


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

    def prune(self, *, now: datetime | None = None) -> int:
        """Drop records that can no longer suppress a message. Returns the count."""
        cutoff = (now or datetime.now(UTC)) - PROCESSED_MESSAGE_RETENTION
        keep: dict[str, dict[str, object]] = {}
        for message_id, record in self.processed_messages.items():
            stamp = _parse_state_timestamp(record.get("processed_at"))
            # A record with no usable timestamp predates this field; keep it
            # rather than risk reprocessing a message we already published.
            if stamp is None or stamp >= cutoff:
                keep[message_id] = record
        removed = len(self.processed_messages) - len(keep)
        self.processed_messages = keep
        return removed

    def save(self) -> None:
        """Persist atomically so an interrupted write cannot lose the state.

        Truncating this file makes the next run reprocess every message in the
        lookback window, so it is written to a sibling temp file and moved into
        place instead of being overwritten in situ.
        """
        self.prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "processed_messages": self.processed_messages,
        }
        rendered = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise


def _parse_state_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class ImapEmailClient:
    def __init__(self, config: EmailIngestConfig, *, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        self.config = config
        self._timeout_seconds = timeout_seconds

    def fetch_recent_messages(self) -> list[MailMessage]:
        if not self.config.username or not self.config.password:
            raise RuntimeError(
                "Email credentials are missing. Set the configured username/password environment variables."
            )
        if self.config.use_ssl:
            connection: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                self.config.host,
                self.config.port,
                timeout=self._timeout_seconds,
            )
        else:
            connection = imaplib.IMAP4(
                self.config.host,
                self.config.port,
                timeout=self._timeout_seconds,
            )
        try:
            connection.login(self.config.username, self.config.password)
            status, _ = connection.select(format_imap_mailbox(self.config.folder), readonly=True)
            if status != "OK":
                raise RuntimeError(f"Could not open IMAP folder {self.config.folder!r}")
            cutoff = datetime.now(UTC) - timedelta(days=self.config.lookback_days)
            since = cutoff.strftime("%d-%b-%Y")
            # IMAP's UID SEARCH takes an optional charset before the criteria.
            # None means "no charset", which is what the RFC expects here; the
            # typeshed stub only models the charset-supplied form.
            status, search_data = connection.uid(
                "SEARCH",
                cast(str, None),
                "SINCE",
                since,
            )
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
        skipped_link_hosts=tuple(
            host
            for value in mailbox.get("skipped_link_hosts", [])
            if (host := str(value).strip().lower())
        ),
        platform_country_scope=load_platform_country_scope(raw.get("platform_country_scope", {})),
    )


def load_platform_country_scope(value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ValueError("platform_country_scope must be a TOML table")
    scope: dict[str, tuple[str, ...]] = {}
    for platform, countries in value.items():
        if not isinstance(countries, list):
            raise ValueError(f"platform_country_scope.{platform} must be a TOML array")
        codes = tuple(code for entry in countries if (code := str(entry).strip().upper()))
        if codes:
            scope[str(platform).strip().casefold()] = codes
    return scope


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


def extract_message_bodies(message: EmailMessage | Message) -> tuple[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    for part in message.walk() if message.is_multipart() else [message]:
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", "")).lower()
        if "attachment" in disposition:
            continue
        if content_type not in {"text/plain", "text/html"}:
            continue
        # BytesParser(policy=default) yields EmailMessage parts, which decode
        # transfer encodings and charsets for us. Fall back to manual decoding
        # for a part the policy could not handle (a broken charset label, a
        # truncated body) rather than dropping it.
        try:
            content = part.get_content() if isinstance(part, EmailMessage) else None
        except (LookupError, ValueError):
            content = None
        if content is None:
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                content = payload.decode(charset, errors="replace")
            except LookupError:
                content = payload.decode("utf-8", errors="replace")
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


def extract_job_candidates(
    message: MailMessage,
    skipped_hosts: Sequence[str] = (),
) -> list[EmailJobCandidate]:
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
        if not url or should_skip_url(url, skipped_hosts):
            continue
        context = normalize_whitespace(link.context or full_text)
        title, company_from_title = infer_title(link.label, context, url, message.subject)
        if not title:
            continue
        if is_efinancialcareers_url(url):
            _url_title, url_company, url_location = efinancial_url_metadata(url)
            card_company, card_location = infer_efinancial_card_metadata(
                title, context, url_location
            )
            company = card_company or company_from_title or infer_company(context, title, "")
            if is_publisher_company(company):
                company = ""
            company = company or url_company
            # The surrounding recommendation cards may contain unrelated
            # locations. Prefer the selected job URL and detail page only.
            location = card_location or url_location
        else:
            linkedin_company, linkedin_location = infer_linkedin_card_metadata(
                title, context, link.label
            )
            company = (
                linkedin_company
                or company_from_title
                or infer_company(context, title, message.sender)
            )
            location = linkedin_location or infer_location(context, full_text)
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


def is_efinancialcareers_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold()
    roots = ("efinancialcareers.com", "efinancialcareers.de", "efinancialcareers.test")
    return any(host == root or host.endswith(f".{root}") for root in roots)


def is_publisher_company(value: str) -> bool:
    normalized = normalize_whitespace(value).casefold()
    return normalized in {"efinancialcareers", "emails", "unknown"}


def efinancial_url_metadata(url: str) -> tuple[str, str, str]:
    if not is_efinancialcareers_url(url):
        return "", "", ""
    path = unquote(urlsplit(url).path)
    slug = re.sub(r"\.id\d+$", "", path.rsplit("/", 1)[-1], flags=re.IGNORECASE)
    slug = re.sub(r"^jobs[-_]", "", slug, flags=re.IGNORECASE)
    folded_slug = fold_url_text(slug)
    aliases = sorted(
        {
            fold_url_text(alias): label for alias, label in EFINANCIAL_LOCATION_ALIASES.items()
        }.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    if not aliases:
        return "", "", ""
    location_pattern_text = "|".join(re.escape(alias) for alias, _label in aliases)
    match = re.search(
        rf"(?<![a-z0-9])(?P<location>{location_pattern_text})(?![a-z0-9])",
        folded_slug,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", "", ""
    location = next(
        label for alias, label in aliases if alias.casefold() == match.group("location").casefold()
    )
    title_slug = slug[match.end() :].lstrip("-_")
    title_hint = normalize_whitespace(re.sub(r"_+", " ", title_slug))
    title_hint, company_hint = split_title_company(title_hint, location_hint=location)
    return title_hint, company_hint, location


def fold_url_text(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    return "".join(
        character for character in folded if not unicodedata.combining(character)
    ).casefold()


def infer_efinancial_card_metadata(title: str, context: str, url_location: str) -> tuple[str, str]:
    """Split the selected eFinancial card's company/location row.

    The email card commonly renders as ``title company city, country ...``
    before the apply link. The URL slug gives us a trustworthy city anchor;
    using that anchor prevents the company parser from absorbing the city.
    """
    normalized_title = normalize_whitespace(title)
    normalized_context = normalize_whitespace(context)
    normalized_location = normalize_whitespace(url_location)
    if not normalized_title or not normalized_context or not normalized_location:
        return "", ""

    title_match = re.search(re.escape(normalized_title), normalized_context, flags=re.IGNORECASE)
    if not title_match:
        title_match = ROLE_PATTERN.search(normalized_context)
    if not title_match:
        return "", ""
    remainder = normalized_context[title_match.end() :].lstrip(" -:|鈥?•·")
    location_tokens = [
        token for token in re.split(r"[^\w]+", fold_url_text(normalized_location)) if token
    ]
    if not location_tokens:
        return "", ""
    location_pattern = (
        r"\b" + r"[\s.,/_-]+".join(re.escape(token) for token in location_tokens) + r"\b"
    )
    location_match = re.search(location_pattern, fold_url_text(remainder), flags=re.IGNORECASE)
    if not location_match:
        return "", ""

    company = cleanup_company(remainder[: location_match.start()])
    location_tail = remainder[location_match.start() :]
    location = re.split(
        r"\s*(?:•|·|\||鈥?)\s*|\s+(?:Festanstellung|Vollzeit|Teilzeit|Competitive|Jetzt\s+bewerben)\b",
        location_tail,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    card_location_match = re.match(
        r"(?P<location>.*?)(?:\s+(?:Festanstellung|Vollzeit|Teilzeit|Competitive|Jetzt\s+bewerben)\b|\s*[\u2022\u00b7|]\s*|$)",
        location_tail,
        flags=re.IGNORECASE,
    )
    if card_location_match:
        location = card_location_match.group("location")
    return company, normalize_whitespace(location).strip(" -,:")


def infer_linkedin_card_metadata(title: str, context: str, label: str = "") -> tuple[str, str]:
    """Read company/location from the selected LinkedIn email card.

    LinkedIn job-detail requests can fail or return a guest page without the
    structured posting fields. Its email cards still commonly contain the
    selected job followed by ``Company · City``. The title anchor is
    intentional: parsing the whole email would borrow metadata from adjacent
    recommendation cards.
    """
    normalized_title = _normalize_linkedin_card_text(title)
    if not normalized_title:
        return "", ""

    # Some cards put the company and city directly in the anchor label, so
    # parse that form before looking for the title in the wider context.
    company, location = _split_linkedin_card_label(normalized_title)
    if company or location:
        return company, location

    for source in (label, context):
        normalized_source = _normalize_linkedin_card_text(source)
        if not normalized_source:
            continue
        title_match = re.search(re.escape(normalized_title), normalized_source, flags=re.IGNORECASE)
        if not title_match:
            continue
        remainder = normalized_source[title_match.end() :]
        company, location = _split_linkedin_card_suffix(remainder)
        if company or location:
            return company, location
    return "", ""


def _normalize_linkedin_card_text(value: str) -> str:
    without_invisible = re.sub(r"[\u034f\u200b-\u200d\ufeff]", " ", value or "")
    return normalize_whitespace(without_invisible)


def _split_linkedin_card_label(value: str) -> tuple[str, str]:
    cleaned = _normalize_linkedin_card_text(value)
    if not cleaned:
        return "", ""

    at_match = re.search(r"\s+(?:at|@)\s+(?P<company>.+?)\s*$", cleaned, flags=re.IGNORECASE)
    if at_match:
        return _valid_linkedin_company(at_match.group("company")), ""

    if not re.search(r"[\u2022\u00b7]", cleaned):
        return "", ""
    left, location = re.split(r"\s*[\u2022\u00b7]\s*", cleaned, maxsplit=1)
    location = _clean_linkedin_card_location(location)
    if not location:
        return "", ""

    # LinkedIn's visible card label is commonly: title (m/w/d) Company · City.
    # A trailing parenthesized employment marker gives us a stable boundary
    # even when the company name itself contains multiple words.
    parenthesized = re.match(
        r"(?P<title>.+\([^)]{1,24}\)\*?)\s+(?P<company>[^\u2022\u00b7]+)$",
        left,
    )
    if parenthesized:
        company = _valid_linkedin_company(parenthesized.group("company"))
        if company:
            return company, location

    # For a simpler title, use the final role match and only accept a short
    # suffix as the company. This does not attempt to infer from the whole
    # email body.
    role_matches = list(ROLE_PATTERN.finditer(left))
    if role_matches:
        company = _valid_linkedin_company(left[role_matches[-1].end() :])
        if company and len(company.split()) <= 6:
            return company, location
    return "", ""


def _split_linkedin_card_suffix(value: str) -> tuple[str, str]:
    cleaned = _normalize_linkedin_card_text(value).lstrip(" -:|\u2022\u00b7")
    if not cleaned:
        return "", ""

    at_match = re.match(
        r"(?:at|@)\s+(?P<company>.+?)(?:\s+[|\u2022\u00b7].*)?$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if at_match:
        return _valid_linkedin_company(at_match.group("company")), ""

    separator = re.search(r"\s*[\u2022\u00b7]\s*", cleaned)
    if not separator:
        return "", ""
    company_candidate = cleaned[: separator.start()]
    # A role at the start of this suffix means the selected card had no
    # metadata; the text belongs to the next recommendation card.
    if ROLE_PATTERN.match(company_candidate):
        return "", ""
    company = _valid_linkedin_company(company_candidate)
    location = _clean_linkedin_card_location(cleaned[separator.end() :])
    return company, location


def _valid_linkedin_company(value: str) -> str:
    company = cleanup_company(value)
    if not company or is_publisher_company(company) or looks_like_location(company):
        return ""
    return company


def _clean_linkedin_card_location(value: str) -> str:
    location = normalize_whitespace(value)
    location = re.split(r"\s*[\u2022\u00b7|]\s*", location, maxsplit=1)[0]
    location = re.split(
        r"\s+\((?:on-site|hybrid|remote|vollzeit|teilzeit)[^)]*\)",
        location,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    next_role = _next_linkedin_role_start(location)
    if next_role is not None:
        location = location[:next_role]
    return cleanup_location(location)


def _next_linkedin_role_start(value: str) -> int | None:
    role_starters = (
        "embedded",
        "platform",
        "software",
        "senior",
        "junior",
        "backend",
        "frontend",
        "firmware",
        "system",
        "systems",
        "cloud",
        "data",
        "machine",
        "computer",
        "devops",
    )
    starter_pattern = "|".join(re.escape(value) for value in role_starters)
    for match in re.finditer(rf"\b(?:{starter_pattern})\b", value, flags=re.IGNORECASE):
        suffix = value[match.start() :]
        if re.search(r"\b(?:engineer|developer|entwickler)\b", suffix, flags=re.IGNORECASE):
            return match.start()
    return None


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
    if detail.posted_at_text:
        raw.posted_at_text = detail.posted_at_text
    raw.raw_payload.update(detail.raw_payload)
    final_url = clean_url(str(detail.raw_payload.get("final_url") or ""))
    if final_url:
        raw.canonical_url = final_url
        raw.source_job_id = stable_source_job_id_for_values(
            final_url,
            raw.title,
            raw.company_name,
        )
    raw.raw_payload["detail_status"] = (
        "ok"
        if has_usable_detail_text(detail.description) and has_known_metadata(raw)
        else "email_fallback"
        if can_use_email_fallback(candidate, raw)
        else "too_sparse"
    )
    return raw


def has_usable_detail_text(value: str) -> bool:
    return len(normalize_whitespace(value)) >= 120


def has_known_metadata(raw: RawJobRecord) -> bool:
    return all(
        normalize_whitespace(value).casefold() not in {"", "unknown", "n/a"}
        for value in (raw.title, raw.company_name, raw.location_raw)
    )


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
    resolved_url = clean_url(final_url) or fetch_url
    detail = parse_job_detail_html(html, resolved_url)
    detail.raw_payload.update(
        {
            "detail_fetch_url": fetch_url,
            "final_url": resolved_url,
        }
    )
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


# A job posting is text; anything larger is either the wrong page or a server
# dripping bytes to keep the connection open. Capping the read closes the one
# case a socket timeout cannot: a response that never ends but never stalls.
MAX_DETAIL_RESPONSE_BYTES = 4 * 1024 * 1024


class DetailFetchTimeout(RuntimeError):
    """The total budget for one detail page was exhausted."""


def fetch_text(url: str, http_config: HttpConfig) -> tuple[str, str]:
    """Fetch one detail page under a hard total wall-clock budget.

    Every attempt is bounded three ways -- a socket timeout, a response-size
    cap, and a shared deadline across retries -- so this returns or raises
    without needing an external watchdog around it.
    """
    last_error: Exception | None = None
    max_retries = http_config.max_retries
    timeout_seconds = min(http_config.timeout_seconds, 6)
    deadline = monotonic() + max(float(http_config.timeout_seconds), timeout_seconds)
    for attempt in range(max_retries + 1):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise DetailFetchTimeout(f"detail fetch exceeded its budget for {url}")
        request = Request(url, headers={"User-Agent": http_config.user_agent})
        try:
            with urlopen(request, timeout=min(timeout_seconds, remaining)) as response:
                raw = response.read(MAX_DETAIL_RESPONSE_BYTES)
                body = raw.decode("utf-8", errors="replace")
                final_url = response.geturl()
            lowered = body.lower()
            if "captcha" in lowered or "unusual traffic" in lowered:
                raise RuntimeError("detail page returned a blocking page")
            return body, final_url
        except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            backoff = (attempt + 1) * http_config.base_delay_seconds
            if monotonic() + backoff >= deadline:
                break
            time.sleep(backoff)
    if last_error:
        raise last_error
    raise RuntimeError("detail fetch failed without an explicit error")


def parse_job_detail_html(html: str, url: str = "") -> JobDetail:
    url_title, url_company, url_location = efinancial_url_metadata(url)
    payload = extract_json_ld_jobposting(html)
    if payload:
        detail = detail_from_json_ld(payload)
        html_company, html_location = extract_html_header_metadata(html)
        detail.title = detail.title or url_title
        detail.company_name = detail.company_name or html_company or url_company
        detail.location_raw = detail.location_raw or html_location or url_location
        if url_location and not detail.raw_payload.get("location_options"):
            detail.raw_payload["location_options"] = [url_location]
        return detail

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
    title, company = split_title_company(title, location_hint=url_location)
    html_company, html_location = extract_html_header_metadata(html)
    title = title or url_title
    company = company or html_company or url_company
    location = html_location or url_location
    raw_payload: dict[str, object] = {"detail_parser": "html"}
    if location:
        raw_payload["location_options"] = [location]
    return JobDetail(
        title=title,
        company_name=company,
        location_raw=location,
        description=description,
        raw_payload=raw_payload,
    )


def detail_from_json_ld(payload: dict) -> JobDetail:
    locations = extract_job_locations(payload)
    organization = (
        payload.get("hiringOrganization")
        if isinstance(payload.get("hiringOrganization"), dict)
        else {}
    )
    company_name = str((organization or {}).get("name") or "").strip()
    posted_at = str(payload.get("datePosted") or "").strip()
    description = html_to_text(str(payload.get("description") or ""))
    return JobDetail(
        title=normalize_whitespace(str(payload.get("title") or "")),
        company_name=normalize_whitespace(company_name),
        location_raw=" | ".join(locations),
        description=description,
        posted_at_text=posted_at,
        raw_payload={
            "detail_parser": "json_ld",
            "location_options": locations,
            "location_country": extract_country(payload),
            "location_city": extract_city(payload),
        },
    )


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


def extract_html_header_metadata(html: str) -> tuple[str, str]:
    """Read the visible company/location row immediately below a job heading."""

    heading = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.IGNORECASE | re.DOTALL)
    if not heading:
        return "", ""
    nearby_html = remove_noise_html(html[heading.end() : heading.end() + 6000])
    nearby_text = html_to_text(nearby_html)
    for separator in ("•", "·", "|"):
        parts = [normalize_whitespace(part) for part in nearby_text.split(separator)]
        if len(parts) < 2:
            continue
        company = cleanup_company(parts[0])
        location = cleanup_location(
            re.split(
                r"\s+(?:Festanstellung|Vollzeit|Teilzeit|Competitive|Posted|Gehaltsspanne)\b",
                parts[1],
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
        )
        if company and location and _looks_like_visible_location(location):
            return company, location
    return "", ""


def _looks_like_visible_location(value: str) -> bool:
    normalized = normalize_whitespace(value).casefold()
    if "," in normalized:
        return True
    return any(
        re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", normalized)
        for key in (*LOCATION_LABELS.keys(), *COUNTRY_LOCATION_LABELS.keys())
    )


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
    return stable_source_job_id_for_values(candidate.url, candidate.title, candidate.company_name)


def stable_source_job_id_for_values(url: str, title: str, company: str) -> str:
    basis = "|".join(
        [
            canonical_link_key(url),
            normalize_whitespace(title).lower(),
            normalize_whitespace(company).lower(),
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


def should_skip_url(url: str, skipped_hosts: Sequence[str] = ()) -> bool:
    lowered = url.lower()
    if not lowered.startswith(("http://", "https://")):
        return True
    host = urlsplit(lowered).netloc
    if any(host == skipped or host.endswith(f".{skipped}") for skipped in skipped_hosts):
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
    url_title, url_company, _url_location = efinancial_url_metadata(url)
    label_title = cleanup_title(label)
    if label_title and not is_generic_label(label_title):
        title, company = split_title_company(label_title)
        if title:
            return title, company

    if url_title:
        return url_title, url_company

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


def split_title_company(value: str, location_hint: str = "") -> tuple[str, str]:
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
        left, right = (part.strip() for part in cleaned.split(separator, 1))
        if looks_like_role(left) and right and not looks_like_location(right):
            return cleanup_title(left), cleanup_company(right)
        if looks_like_role(left) and location_hint:
            location_prefix = re.match(
                rf"{re.escape(location_hint)}\s*[-:|]\s*(?P<company>.+)$",
                right,
                flags=re.IGNORECASE,
            )
            if location_prefix:
                return cleanup_title(left), cleanup_company(location_prefix.group("company"))
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
