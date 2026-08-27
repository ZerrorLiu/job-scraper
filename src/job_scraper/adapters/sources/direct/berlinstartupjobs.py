from job_scraper.collectors.berlinstartupjobs import BerlinStartupJobsDirectCollector
from job_scraper.ports.sources import SourceCapabilities


class BerlinStartupJobsDirectSource(BerlinStartupJobsDirectCollector):
    """Direct HTTP implementation of the regional startup-board source port."""

    capabilities = SourceCapabilities(
        acquisition_mode="direct",
        platform="berlinstartupjobs",
        supports_pagination=True,
        supports_upstream_freshness=False,
        is_metered=False,
    )
