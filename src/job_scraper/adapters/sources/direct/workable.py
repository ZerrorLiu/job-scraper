from job_scraper.collectors.workable import WorkableDirectCollector
from job_scraper.ports.sources import SourceCapabilities


class WorkableDirectSource(WorkableDirectCollector):
    """Direct HTTP implementation of the token-free applicant-tracking search port."""

    capabilities = SourceCapabilities(
        acquisition_mode="direct",
        platform="workable",
        supports_pagination=True,
        supports_upstream_freshness=False,
        is_metered=False,
    )
