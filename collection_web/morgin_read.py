"""Fixed, bounded projections of saved app state for the owner Morgin runtime."""

import json
import math
import re
import time

from flask import Response

from .morgin_grants import CAPABILITIES, PROTOCOL, GrantFailure, GrantStore, IDENTITY, iso, unique_object


PREFIX = '/api/integrations/morgin/v1'
MAX_BODY = 4 * 1024
MAX_RESULT = 24 * 1024
STALE_AFTER = 3600
COLLECTION_STATES = {'draft', 'kept', 'published', 'archived'}
JOB_STATES = {'queued', 'running', 'succeeded', 'failed'}
JOB_KINDS = {'Generate Drift', 'Improve collection', 'Publish collection', 'Sync library',
             'Request missing titles', 'Test connection', 'Scheduled Drift', 'Scheduled library sync',
             'Scheduled rotation', 'Scheduled family shelf', 'Scheduled new arrivals',
             'Accept reviewed additions', 'Review arrival suggestion', 'Refresh request progress',
             'Check suggestion metadata', 'Explain collection', 'Explain collections',
             'Apply improvements', 'Create collection idea', 'Review new arrivals',
             'Review permanent collections', 'Configure family shelf', 'Refresh family shelf',
             'Rotate Plex Home', 'Switch Drift shelves', 'Activate Drift', 'Import desktop collections',
             'Import Trakt list', 'Add owned suggestions', 'Retire collection', 'Manage rotation'}
REQUEST_PROGRESS = {'Requested', 'Requesting', 'Request failed', 'Requested; waiting for catalog confirmation',
                    'Needs attention in Radarr', 'Needs attention in Sonarr', 'Importing', 'Queued / paused',
                    'Downloaded; waiting for Plex', 'Episodes available; waiting for Plex',
                    'Requested; waiting for a release', 'In collection', 'In draft', 'In Plex',
                    'In Plex; awaiting collection review'}
SENSITIVE_TEXT = re.compile(r'(?:https?|file)://\S+|(?:[A-Za-z]:[\\/]|\\\\)\S*|(?<!\w)/(?:[^\s/]+/)+[^\s]*', re.I)
CONTROLS = re.compile(r'[\x00-\x1f\x7f-\x9f\ud800-\udfff]')


class ReadFailure(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def _json(value, status=200):
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return Response(raw, status=status, content_type='application/json; charset=utf-8', headers={'Cache-Control': 'no-store'})


def _error(status, code, message):
    return _json({'error': {'code': code, 'message': message}}, status)


def _date(value, now):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0 or value > now:
        return None
    try:
        return iso(value)
    except (ValueError, OverflowError, OSError):
        return None


def _rows(value):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError('Unavailable saved data')
    return value


def _identity(value):
    return value if isinstance(value, str) and IDENTITY.fullmatch(value) else None


class Projection:
    def __init__(self, capability, now, observed=None):
        self.shortened = False
        self.now = now
        self.value = {'appId': 'media', 'capability': capability, 'status': 'ok', 'sourceMode': 'cached',
                      'source': 'Collection Manager saved state', 'observedAt': observed, 'retrievedAt': iso(now),
                      'summary': '', 'facts': [], 'items': [],
                      'limitations': ['Saved app data only; no Plex or Arr request, refresh, test, repair, download or other task was started.']}

    def text(self, value, limit):
        if not isinstance(value, str):
            return ''
        clean = CONTROLS.sub(' ', SENSITIVE_TEXT.sub('[private link or path omitted]', value)).strip()
        if clean != value.strip() or len(clean) > limit:
            self.shortened = True
        return clean[:limit]

    def fact(self, label, value):
        if len(self.value['facts']) >= 30:
            self.shortened = True
            return
        self.value['facts'].append({'label': self.text(label, 100), 'value': self.text(str(value), 600)})

    def item(self, identity, title, description):
        if len(self.value['items']) >= 20:
            self.shortened = True
            return
        self.value['items'].append({'id': self.text(identity, 160), 'title': self.text(title, 160), 'description': self.text(description, 1200)})

    def limitation(self, message):
        if len(self.value['limitations']) < 11:
            self.value['limitations'].append(self.text(message, 400))
        else:
            self.shortened = True

    def cache(self, observed):
        if observed is None:
            self.value['status'] = 'partial'
            self.limitation('The original observation time is unavailable. Retrieval time does not establish freshness.')
        else:
            self.fact('Cache age in seconds', int(max(0, self.now - observed)))
            if self.now - observed > STALE_AFTER:
                self.value['status'] = 'partial'
                self.limitation('The saved observation is more than one hour old and may be stale.')

    def finish(self):
        self.value['summary'] = self.text(self.value['summary'], 1200)
        while len(json.dumps(self.value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) >= MAX_RESULT - 512:
            self.shortened = True
            if self.value['items']:
                self.value['items'].pop()
            elif self.value['facts']:
                self.value['facts'].pop()
            else:
                raise ValueError('Unbounded result')
        if self.shortened:
            if self.value['status'] == 'ok':
                self.value['status'] = 'partial'
            self.value['limitations'].append('Some saved text or rows were omitted to enforce the read limits and private-field boundary; this is not a complete export.')
        return self.value


def _collection_description(row, output):
    status = row.get('status') if row.get('status') in COLLECTION_STATES else 'unknown'
    media_type = row.get('media_type') if row.get('media_type') in {'movie', 'show'} else 'unknown'
    items, missing = _rows(row.get('items', [])), _rows(row.get('missing', []))
    description = output.text(row.get('description', ''), 850)
    return f'{media_type}; saved status: {status}; {len(items)} saved members; {len(missing)} missing suggestions. {description}'.strip()


def project(state, capability, input_value, now=None):
    now = time.time() if now is None else now
    if not isinstance(state, dict):
        raise ValueError('Unavailable saved data')
    collections = _rows(state.get('collections'))
    observed_raw = state.get('synced_at')
    observed = _date(observed_raw, now)
    output = Projection(capability, now, observed)
    if capability != 'requests':
        output.cache(observed_raw if observed else None)
        output.limitation('Observation time describes the saved library sync. Collection changes are saved records and are not a fresh verification of Plex membership.')
    if capability == 'overview':
        library = _rows(state.get('library'))
        output.value['summary'] = f'{len(collections)} saved collection records and {len(library)} cached library titles.'
        output.fact('Saved collections', len(collections))
        output.fact('Cached movies', sum(row.get('media_type') == 'movie' for row in library))
        output.fact('Cached shows', sum(row.get('media_type') == 'show' for row in library))
        for status in sorted(COLLECTION_STATES):
            output.fact('Collections: ' + status, sum(row.get('status') == status for row in collections))
        output.limitation('App storage is readable. This does not establish live Plex, Arr, download-client or model-service availability.')
    elif capability == 'search-collections':
        query = input_value.casefold()
        selected = [row for row in collections if query in output.text(row.get('name', ''), 160).casefold() or query in output.text(row.get('description', ''), 1200).casefold()]
        output.value['summary'] = f'{len(selected)} saved collection matches; showing at most 20.'
        for row in selected:
            identity = _identity(row.get('id'))
            if not identity:
                output.shortened = True
                continue
            output.item(identity, row.get('name', 'Saved collection'), _collection_description(row, output))
        output.limitation('Search covers saved collection names and descriptions only, not titles in the full library.')
    elif capability == 'collection':
        selected = [row for row in collections if row.get('id') == input_value]
        if len(selected) != 1:
            output.value.update(status='unavailable', summary='No unique saved collection was found for that identifier.')
            output.limitation('No live lookup was attempted. Use an exact collection ID returned by search.')
        else:
            row = selected[0]
            output.value['summary'] = output.text(row.get('name', 'Saved collection'), 160) + ': ' + _collection_description(row, output)
            output.fact('Collection ID', input_value)
            for label, field in [('Managed by this app', 'managed'), ('Saved Home flag', 'home'), ('Rotation enabled', 'rotation_enabled'), ('Permanent collection', 'permanent')]:
                output.fact(label, 'yes' if row.get(field) is True else 'no' if row.get(field) is False else 'unknown')
            for label, field in [('Record created', 'created_at'), ('Saved description updated', 'context_updated_at')]:
                output.fact(label, _date(row.get(field), now) or 'unknown')
            output.limitation('Member counts and collection metadata only. Provider identifiers, member records, viewing information and raw library data are excluded.')
    elif capability == 'requests':
        jobs = _rows(state.get('jobs'))
        tracked = [(collection, index, row) for collection in collections if collection.get('status') != 'archived'
                   for field in ('missing', 'available') for index, row in enumerate(_rows(collection.get(field, [])))
                   if _date(row.get('requested_at'), now)]
        observations = [row.get('request_checked_at') for _, _, row in tracked if _date(row.get('request_checked_at'), now)]
        observed_raw = min(observations) if observations else None
        output.value['observedAt'] = _date(observed_raw, now)
        output.cache(observed_raw)
        if len(observations) != len(tracked):
            output.value['status'] = 'partial'
            output.limitation('Some requested titles have no saved observation time; their progress is unknown.')
        output.value['summary'] = f'{len(tracked)} saved title requests and {len(jobs)} retained jobs; showing at most 20 combined entries.'
        output.fact('Saved title requests', len(tracked))
        output.fact('Retained jobs', len(jobs))
        if len(jobs) > 10 and tracked:
            output.shortened = True
        for index, job in enumerate(jobs[:10] if tracked else jobs):
            kind = job.get('kind') if job.get('kind') in JOB_KINDS else 'Other task'
            status = job.get('status') if job.get('status') in JOB_STATES else 'unknown'
            output.item('job:' + (_identity(job.get('id')) or str(index)), kind,
                        f'Saved job status: {status}; started: {_date(job.get("started_at"), now) or "unknown"}; finished: {_date(job.get("finished_at"), now) or "unknown"}.')
        for sequence, (collection, index, row) in enumerate(tracked):
            progress = row.get('request_progress')
            if not isinstance(progress, str) or (progress not in REQUEST_PROGRESS and not re.fullmatch(r'Downloading(?: (?:100|[0-9]{1,2})%)?(?: \(episodes\))?', progress)):
                progress = 'Progress unknown'
            if row.get('request_error'):
                output.value['status'] = 'partial'
                progress += '; last check unavailable, retained progress may be stale'
            year = row.get('year')
            title = output.text(row.get('title', 'Requested title'), 140) + (f' ({year})' if type(year) is int and 1800 <= year <= 2200 else '')
            output.item('request:' + str(sequence), title,
                        f'Saved progress: {progress}; observed: {_date(row.get("request_checked_at"), now) or "unknown"}; requested record: {_date(row.get("requested_at"), now) or "unknown"}.')
        output.limitation('Job start/finish times describe app records, not live provider observations. Raw messages, errors, paths, provider IDs and credentials are excluded.')
    else:
        raise ValueError('Unsupported projection')
    return output.finish()


def _request_body(request):
    if request.mimetype != 'application/json' or request.headers.get('Content-Encoding', 'identity') != 'identity':
        raise ReadFailure(400, 'invalid_request', 'Send a bounded JSON query.')
    if request.content_length is not None and request.content_length > MAX_BODY:
        raise ReadFailure(400, 'invalid_request', 'The query exceeds its size limit.')
    raw = request.stream.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise ReadFailure(400, 'invalid_request', 'The query exceeds its size limit.')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
    except Exception:
        raise ReadFailure(400, 'invalid_request', 'Send a valid JSON query.') from None
    if not isinstance(value, dict) or set(value) != {'capability', 'input'} or not isinstance(value['capability'], str) or value['capability'] not in CAPABILITIES:
        raise ReadFailure(400, 'invalid_request', 'Choose an implemented read capability.')
    kind, text = CAPABILITIES[value['capability']]['input'], value['input']
    if kind == 'none':
        valid = text is None
    else:
        valid = isinstance(text, str) and bool(text.strip()) and len(text) <= 160 and not CONTROLS.search(text)
        if valid and kind == 'id':
            valid = bool(IDENTITY.fullmatch(text))
    if not valid:
        raise ReadFailure(400, 'invalid_request', 'Use null or the required short search or exact identifier.')
    return value


def handle(request, store):
    """Runs before browser authorization; every bridge outcome ends this request."""
    try:
        paths = {PREFIX + '/connection': 'GET', PREFIX + '/query': 'POST'}
        if request.path not in paths:
            raise ReadFailure(404, 'not_found', 'This integration route is unavailable.')
        raw_uri = request.environ.get('RAW_URI') or request.environ.get('REQUEST_URI') or request.path
        if request.query_string or raw_uri != request.path:
            raise ReadFailure(400, 'invalid_request', 'Use the exact integration route without a query string.')
        if request.method != paths[request.path]:
            raise ReadFailure(405, 'method_not_allowed', 'This integration method is unavailable.')
        authorization = request.headers.getlist('Authorization')
        if len(authorization) != 1:
            raise GrantFailure(401)
        grants = GrantStore(store.directory)
        grant = grants.authenticate(authorization[0])
        if request.headers.get('Origin') is not None or request.headers.get('Sec-Fetch-Site') not in (None, 'none'):
            raise ReadFailure(403, 'forbidden', 'This grant is for the enrolled server runtime.')
        if request.method == 'GET':
            if request.content_length not in (None, 0) or request.headers.get('Transfer-Encoding'):
                raise ReadFailure(400, 'invalid_request', 'The connection check has no request body.')
            return _json({'protocol': PROTOCOL, 'appId': 'media', 'grantId': grant['grantId'], 'expiresAt': grant['expiresAt'],
                          'sourceMode': 'cached', 'capabilities': [CAPABILITIES[scope] for scope in grant['capabilities']]})
        value = _request_body(request)
        if value['capability'] not in grant['capabilities']:
            raise ReadFailure(403, 'forbidden', 'This capability is not granted.')
        # Existing Store.read uses sqlite's normal connection mode. Never let a
        # missing data file turn this read into creation of an empty database.
        if not store.path.is_file():
            raise ValueError('Saved data is missing')
        result = project(store.read(), value['capability'], value['input'])
        # A long local read cannot complete under a revoked or expired grant.
        latest = grants.authenticate(authorization[0])
        if latest['grantId'] != grant['grantId'] or value['capability'] not in latest['capabilities']:
            raise ReadFailure(403, 'forbidden', 'This capability is not granted.')
        return _json(result)
    except ReadFailure as error:
        return _error(error.status, error.code, error.message)
    except GrantFailure as error:
        return _error(error.status, 'unauthorized' if error.status == 401 else 'unavailable', str(error))
    except Exception:
        # No raw exceptions and no diagnostic persistence from read failures.
        return _error(503, 'unavailable', 'Saved app data is unavailable. No task was started.')
