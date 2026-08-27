from job_scraper.collectors.arbeitsagentur import ArbeitsagenturDirectCollector
from job_scraper.ports.sources import SourceCapabilities


class ArbeitsagenturDirectSource(ArbeitsagenturDirectCollector):
    """Direct HTTP implementation of the public employment-agency source port."""

    capabilities = SourceCapabilities(
        acquisition_mode="direct",
        platform="arbeitsagentur",
        supports_pagination=True,
        supports_upstream_freshness=False,
        is_metered=False,
    )
