from job_scraper.pipeline.normalize import canonicalize_url


def test_canonicalize_url_keeps_indeed_job_key_distinct() -> None:
    # Indeed's detail path is always /viewjob; the job identity lives entirely
    # in the jk query parameter. Stripping it (as the generic rule does for
    # every other source) collapsed every Indeed job onto the same canonical
    # URL and made the storage layer's canonical_url-based lookup silently
    # merge unrelated postings into one row.
    first = canonicalize_url("https://de.indeed.com/viewjob?jk=abc123def456")
    second = canonicalize_url("https://de.indeed.com/viewjob?jk=e119aca6cb4e74d6")
    assert first != second
    assert first == "https://de.indeed.com/viewjob?jk=abc123def456"
    assert second == "https://de.indeed.com/viewjob?jk=e119aca6cb4e74d6"


def test_canonicalize_url_drops_non_identity_indeed_tracking_params() -> None:
    tracked = "https://de.indeed.com/viewjob?jk=abc123def456&from=serp&tk=xyz"
    plain = "https://de.indeed.com/viewjob?jk=abc123def456"
    assert canonicalize_url(tracked) == canonicalize_url(plain)


def test_canonicalize_url_still_strips_query_for_non_indeed_sources() -> None:
    url = "https://www.linkedin.com/jobs/view/1234567?refId=xyz&trackingId=abc"
    assert canonicalize_url(url) == "https://www.linkedin.com/jobs/view/1234567"
