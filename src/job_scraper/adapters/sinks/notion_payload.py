from __future__ import annotations

from datetime import date

from job_scraper.domain.models import JobRecord
from job_scraper.integrations.notion import normalize_status_name, notion_status_name


def build_daily_properties(
    job: JobRecord,
    property_types: dict[str, str] | None = None,
    *,
    found_date: date | None = None,
) -> dict:
    property_types = property_types or {}
    language_type = property_types.get("Language", "select")
    status_type = property_types.get("Status", "select")
    source_type = property_types.get("Source", "multi_select")
    properties = {
        "Status": {status_type: {"name": notion_status_name("new")}},
        "Job": build_job_title(job),
        "Company": rich_text_property(na_value(job.company_name)),
        "Location": rich_text_property(na_value(job.city)),
        "Language": (
            {"select": {"name": notion_language_name(job.description_language)}}
            if language_type == "select"
            else rich_text_property(notion_language_name(job.description_language))
        ),
    }
    source_names = notion_source_names(job)
    if source_type == "multi_select":
        properties["Source"] = {
            "multi_select": [{"name": source_name} for source_name in source_names]
        }
    elif source_type == "select":
        properties["Source"] = {"select": {"name": source_names[0]}}
    else:
        properties["Source"] = rich_text_property(", ".join(source_names))
    if found_date is not None:
        properties["Date"] = {"date": {"start": found_date.isoformat()}}
    return properties


def build_job_title(job: JobRecord) -> dict:
    url = job.application_url
    text: dict[str, object] = {"content": na_value(job.title)[:200]}
    if url:
        text["link"] = {"url": url}
    return {"title": [{"type": "text", "text": text}]}


def build_children(job: JobRecord) -> list[dict]:
    query = na_value(str(job.raw_payload.get("query", "")))
    email_subject = na_value(str(job.raw_payload.get("email_subject", "")))
    posted_at = job.posted_at.date().isoformat() if job.posted_at else "N/A"
    source_line = f"Email Subject: {email_subject}" if email_subject != "N/A" else f"Query: {query}"
    lines = [
        source_line,
        f"Posted At: {posted_at}",
        f"Location: {render_location(job)}",
        f"Language: {notion_language_name(job.description_language)}",
    ]
    blocks: list[dict] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Summary"}}]},
        }
    ]
    for line in lines:
        blocks.append(
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line}}]},
            }
        )
    blocks.append(
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": na_value(job.job_description)[:1800]},
                    }
                ],
            },
        }
    )
    for label, url in (
        ("Apply URL", job.application_url),
        ("Job URL", job.source_url),
    ):
        blocks.append(_url_block(label, url))
    return blocks


def rich_text_property(value: str) -> dict:
    return {"rich_text": [{"text": {"content": value[:200]}}]}


def notion_language_name(value: str) -> str:
    normalized = na_value(value)
    allowed = {"English", "German", "Mixed", "Unknown", "N/A"}
    return normalized if normalized in allowed else "N/A"


def notion_page_status(page: dict) -> str:
    properties = page.get("properties", {})
    status_property = properties.get("Status", {}) or {}
    raw_status = (status_property.get("select") or {}).get("name", "") or (
        status_property.get("status") or {}
    ).get("name", "")
    return normalize_status_name(raw_status)


def notion_source_names(job: JobRecord) -> list[str]:
    raw_sources = job.raw_payload.get("acquisition_sources")
    if isinstance(raw_sources, (list, tuple)):
        values = [str(value).strip().lower() for value in raw_sources if str(value).strip()]
    else:
        values = []
    if not values:
        values = [job.source.strip().lower()]
    labels = {
        "linkedin": "LinkedIn",
        "linkedin_direct": "LinkedIn",
        "indeed": "Indeed",
        "indeed_brightdata": "Indeed",
        "email": "Email",
        "email_imap": "Email",
    }
    result: list[str] = []
    for value in values:
        label = labels.get(value, value.title() or "Unknown")
        if label not in result:
            result.append(label)
    return result or ["Unknown"]


def notion_page_job_title_and_url(page: dict) -> tuple[str, str]:
    properties = page.get("properties", {})
    pieces = (properties.get("Job", {}) or {}).get("title") or []
    title = "".join(str(piece.get("plain_text", "")) for piece in pieces).strip()
    for piece in pieces:
        text_data = piece.get("text", {})
        nested_link = text_data.get("link", {})
        if isinstance(nested_link, dict) and nested_link.get("url"):
            return title, str(nested_link["url"])
        if piece.get("href"):
            return title, str(piece["href"])
    return title, ""


def notion_page_company_name(page: dict) -> str:
    properties = page.get("properties", {})
    pieces = (properties.get("Company", {}) or {}).get("rich_text") or []
    return "".join(str(piece.get("plain_text", "")) for piece in pieces).strip()


def na_value(value: str | None) -> str:
    cleaned = " ".join((value or "").split())
    return cleaned if cleaned else "N/A"


def render_location(job: JobRecord) -> str:
    options = location_options(job)
    if not options:
        return "N/A"
    if len(options) == 1:
        return options[0]
    return f"Multiple locations: {', '.join(options)}"


def location_options(job: JobRecord) -> list[str]:
    raw_options = job.raw_payload.get("location_options", [])
    if isinstance(raw_options, list):
        options = [str(value).strip() for value in raw_options if str(value).strip()]
        if options:
            return options
    if job.city and job.city != "Multiple locations":
        return [job.city]
    return []


def _url_block(label: str, url: str) -> dict:
    if not url:
        rich_text = [{"type": "text", "text": {"content": f"{label}: N/A"}}]
    else:
        rich_text = [
            {"type": "text", "text": {"content": f"{label}: "}},
            {
                "type": "text",
                "text": {"content": url, "link": {"url": url}},
            },
        ]
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": rich_text},
    }
