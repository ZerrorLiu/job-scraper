from job_scraper.collectors.data_integration_adapter import _to_raw_job, normalize_upstream_entry


def test_indeed_records_use_the_platform_listing_url() -> None:
    raw = _to_raw_job(
        {
            "record_id": "indeed-fictional-1",
            "reference_url": "https://de.indeed.com/viewjob?jk=fictional-1",
            "external_application_url": "https://careers.example.test/fictional-1",
            "position_title": "Fictional Software Engineer",
            "organization": "Example GmbH",
            "region": "Berlin, Germany",
        },
        "software engineer",
        "Berlin",
        transport="fixture",
    )

    assert raw.source == "indeed"
    assert raw.source_url == "https://de.indeed.com/viewjob?jk=fictional-1"
    assert raw.canonical_url == raw.source_url
    assert "external_application_url" not in raw.raw_payload


def test_brightdata_normalization_discards_external_application_fields() -> None:
    normalized = normalize_upstream_entry(
        {
            "jobid": "fictional-2",
            "url": "https://de.indeed.com/viewjob?jk=fictional-2",
            "apply_link": "https://careers.example.test/fictional-2",
            "application": {"url": "https://jobs.example.test/fictional-2"},
            "metadata": {"externalApplicationUrl": "https://workday.example.test/fictional-2"},
            "jobApplyDestination": "https://ats.example.test/fictional-2",
            "hiringOrganization": {
                "name": "Example GmbH",
                "sameAs": "https://example.test",
            },
        }
    )

    assert normalized is not None
    assert normalized["reference_url"] == "https://de.indeed.com/viewjob?jk=fictional-2"
    assert "apply_link" not in normalized["raw_payload"]
    assert "application" not in normalized["raw_payload"]
    assert "externalApplicationUrl" not in normalized["raw_payload"]["metadata"]
    assert "jobApplyDestination" not in normalized["raw_payload"]
    assert normalized["raw_payload"]["hiringOrganization"] == {"name": "Example GmbH"}


def test_brightdata_replaces_non_indeed_reference_with_platform_url() -> None:
    normalized = normalize_upstream_entry(
        {
            "jobid": "fictional-3",
            "reference_url": "https://careers.example.test/fictional-3",
            "title": "Fictional Engineer",
        }
    )

    assert normalized is not None
    assert normalized["reference_url"] == "https://www.indeed.com/viewjob?jk=fictional-3"
