from job_scraper.collectors.arbeitnow import ArbeitnowDirectCollector
from job_scraper.ports.sources import SourceCapabilities


class ArbeitnowDirectSource(ArbeitnowDirectCollector):
    """Direct HTTP implementation of the national job-board feed source port."""

    capabilities = SourceCapabilities(
        acquisition_mode="direct",
        platform="arbeitnow",
        supports_pagination=True,
        supports_upstream_freshness=False,
        is_metered=False,
    )
