from job_scraper.collectors.ats import AtsDirectCollector
from job_scraper.ports.sources import SourceCapabilities


class AtsDirectSource(AtsDirectCollector):
    """Direct HTTP implementation of the employer applicant-tracking-board source port."""

    capabilities = SourceCapabilities(
        acquisition_mode="direct",
        platform="ats",
        supports_pagination=False,
        supports_upstream_freshness=False,
    )
