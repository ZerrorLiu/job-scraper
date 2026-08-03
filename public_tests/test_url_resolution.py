from job_scraper.adapters.sinks.notion_payload import build_children, build_job_title


def test_notion_uses_the_platform_source_link() -> None:
    job = type(
        "FakeJob",
        (),
        {
            "title": "Example",
            "source_url": "https://de.indeed.com/viewjob?jk=fictional",
            "canonical_url": "https://de.indeed.com/viewjob?jk=fictional",
            "raw_payload": {},
            "posted_at": None,
            "location_raw": "Berlin",
            "city": "Berlin",
            "description_language": "English",
            "job_description": "Fictional description",
        },
    )()

    assert build_job_title(job)["title"][0]["text"]["link"]["url"] == job.source_url
    children = build_children(job)
    rendered = str(children)
    assert "Job URL" in rendered
    assert job.source_url in rendered
    assert "Apply URL" not in rendered
    assert "careers.example.test" not in rendered
