from job_scraper.collectors.data_integration_adapter import _to_raw_job


def test_indeed_records_preserve_reference_url_for_application_inspection() -> None:
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
    assert raw.application_url == raw.source_url
