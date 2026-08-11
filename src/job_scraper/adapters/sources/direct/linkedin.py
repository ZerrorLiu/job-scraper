from job_scraper.collectors.linkedin import LinkedInCollector
from job_scraper.ports.sources import SourceCapabilities


class LinkedInDirectSource(LinkedInCollector):
    """Direct HTTP implementation of the LinkedIn source port."""

    capabilities = SourceCapabilities(
        acquisition_mode="direct",
        platform="linkedin",
        supports_pagination=True,
        supports_upstream_freshness=True,
    )
