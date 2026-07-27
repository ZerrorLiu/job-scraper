from job_scraper.collectors.data_integration_adapter import _to_raw_job, normalize_upstream_entry


def test_indeed_records_keep_source_url_separate_from_unresolved_application_url() -> None:
    raw = _to_raw_job(
        {
            "record_id": "indeed-fictional-1",
            "reference_url": "https://de.indeed.com/viewjob?jk=fictional-1",
            "position_title": "Fictional Software Engineer",
            "organization": "Example GmbH",
            "region": "Berlin, Germany",
        },
        "software engineer",
        "Berlin",
        transport="fixture",
    )

    assert raw.source == "indeed"
    assert raw.application_url == ""
    assert raw.source_url == "https://de.indeed.com/viewjob?jk=fictional-1"


def test_indeed_records_accept_explicit_external_application_url() -> None:
    raw = _to_raw_job(
        {
            "record_id": "indeed-fictional-2",
            "reference_url": "https://de.indeed.com/viewjob?jk=fictional-2",
            "external_application_url": "https://careers.example.test/jobs/fictional-2",
        },
        "software engineer",
        "Berlin",
        transport="fixture",
    )

    assert raw.application_url == "https://careers.example.test/jobs/fictional-2"


def test_brightdata_keeps_listing_url_separate_from_apply_link() -> None:
    normalized = normalize_upstream_entry(
        {
            "jobid": "fictional-3",
            "url": "https://de.indeed.com/viewjob?jk=fictional-3",
            "apply_link": "https://careers.example.test/jobs/fictional-3",
        }
    )

    assert normalized is not None
    assert normalized["reference_url"] == "https://de.indeed.com/viewjob?jk=fictional-3"
    assert normalized["external_application_url"] == (
        "https://careers.example.test/jobs/fictional-3"
    )


def test_brightdata_accepts_explicit_nested_application_url() -> None:
    normalized = normalize_upstream_entry(
        {
            "jobid": "fictional-4",
            "url": "https://de.indeed.com/viewjob?jk=fictional-4",
            "application": {"url": "https://jobs.example.test/apply/fictional-4"},
        }
    )

    assert normalized is not None
    assert normalized["external_application_url"] == ("https://jobs.example.test/apply/fictional-4")
