"""Catalogue identity matching without guessing among Plex editions."""

import re


def external_ids(row):
    """Read validated provider IDs, including previously saved lookup metadata."""
    result = {}
    values = row.get('external_ids', {})
    if not isinstance(values, dict):
        values = {}
    metadata = row.get('metadata')
    if isinstance(metadata, dict) and row.get('metadata_status') == 'verified':
        result.update(external_ids(metadata))
    source = row.get('external_source')
    if source in {'tmdb', 'tvdb', 'imdb'} and str(row.get('id', '')).startswith('external:'):
        values = {source: str(row['id']).split(':', 1)[1], **values}
    for provider in ('tmdb', 'tvdb', 'imdb'):
        value = values.get(provider)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        value = str(value).strip().lower()
        if provider == 'imdb':
            if re.fullmatch(r'tt\d{7,10}', value):
                result[provider] = value
        elif re.fullmatch(r'[0-9]{1,12}', value) and int(value) > 0:
            result[provider] = str(int(value))
    return result


def id_relation(left, right):
    """Return match, conflict or unknown; any contradictory shared ID wins."""
    a, b = external_ids(left), external_ids(right)
    shared = a.keys() & b.keys()
    if any(a[key] != b[key] for key in shared):
        return 'conflict'
    return 'match' if shared else 'unknown'


def library_matches(row, library, media_type):
    """Prefer catalogue identity, retaining every edition for explicit review."""
    from .integrations import clean
    pool = [item for item in library if item.get('media_type') == media_type]
    exact = [item for item in pool if id_relation(row, item) == 'match']
    if exact:
        return exact
    return [item for item in pool if id_relation(row, item) != 'conflict'
            and clean(item['title']) == clean(row['title']) and item['year'] == row['year']]
