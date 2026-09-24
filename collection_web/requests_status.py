"""Read-only Arr queue snapshots. No download commands or title lookups."""
from . import integrations as providers
from .store import DomainError


def snapshot(settings, kind):
    service = "sonarr" if kind == "show" else "radarr"
    endpoint = "/series" if kind == "show" else "/movie"
    catalog = providers.arr_request(settings, service, "GET", endpoint)
    queue = []
    for page in range(1, 21):
        response = providers.arr_request(settings, service, "GET", "/queue", params={"page":page,"pageSize":100,"includeUnknownSeriesItems":False,"includeUnknownMovieItems":False})
        records = response.get("records", [])
        queue.extend(records)
        if page * 100 >= response.get("totalRecords", 0):
            break
    return catalog, queue


def progress_for(item, kind, catalog, queue):
    matches = [row for row in catalog if providers.catalog_title_matches(row, item)]
    if len(matches) != 1:
        return "Requested; waiting for catalog confirmation"
    row = matches[0]
    identity = "seriesId" if kind == "show" else "movieId"
    jobs = [job for job in queue if job.get(identity) == row.get("id")]
    if any(str(j.get("trackedDownloadState", "")).lower() in {"importblocked", "importfailed"} or str(j.get("trackedDownloadStatus", "")).lower() in {"warning", "error"} for j in jobs):
        return "Needs attention in " + service_name(kind)
    if any(str(j.get("status", "")).lower() == "downloading" for j in jobs):
        total = sum(float(j.get("size") or 0) for j in jobs)
        left = sum(float(j.get("sizeleft") or 0) for j in jobs)
        percent = max(0, min(100, round(100*(total-left)/total))) if total else None
        return "Downloading" + (f" {percent}%" if percent is not None else "") + (" (episodes)" if kind == "show" else "")
    if any(str(j.get("status", "")).lower() == "completed" or str(j.get("trackedDownloadState", "")).lower() in {"importpending", "importing", "downloadfinished"} for j in jobs):
        return "Importing"
    if jobs:
        return "Queued / paused"
    has_file = row.get("hasFile") if kind == "movie" else row.get("statistics", {}).get("episodeFileCount", 0) > 0
    if has_file:
        return "Downloaded; waiting for Plex" if kind == "movie" else "Episodes available; waiting for Plex"
    return "Requested; waiting for a release"


def service_name(kind):
    return "Sonarr" if kind == "show" else "Radarr"
