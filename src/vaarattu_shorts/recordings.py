"""Provider-aware source access for clips, using the existing VOD adapters."""
from . import highlight_sources, youtube
from .contracts import source_url


def metadata(settings, value, folder, check=lambda: None):
    provider, identity, url = source_url(value)
    if provider == "youtube":
        return {**youtube.metadata(settings, identity, folder, check), "provider": provider, "url": url}
    return highlight_sources.metadata(settings, url, folder, check)


def acquire(settings, value, folder, check, interval=None):
    provider, identity, url = source_url(value)
    if provider == "youtube":
        return youtube.acquire(settings, identity, folder, check, interval)
    return highlight_sources.acquire(settings, {"url": url}, folder, check, interval)
