"""Bounded support evidence without exception text, locals or user content."""
import hashlib
import json
from pathlib import Path
import platform
import re
import time
import uuid
from importlib.metadata import version, PackageNotFoundError

ROOT = Path(__file__).resolve().parent
KINDS = {'Generate Drift', 'Improve collection', 'Publish collection', 'Sync library',
         'Request missing titles', 'Test connection', 'Scheduled Drift', 'Scheduled library sync',
         'Scheduled rotation', 'Scheduled family shelf', 'Scheduled new arrivals',
         'Accept reviewed additions', 'Review arrival suggestion', 'Refresh request progress',
         'Check suggestion metadata', 'Explain collection', 'Explain collections',
         'Apply improvements', 'Create collection idea', 'Review new arrivals',
         'Review permanent collections', 'Configure family shelf', 'Refresh family shelf',
         'Rotate Plex Home', 'Switch Drift shelves', 'Activate Drift', 'Import desktop collections',
         'Import Trakt list', 'Add owned suggestions', 'Retire collection', 'Manage rotation'}


def exception_evidence(error):
    """Keep code locations only; deliberately never format the exception itself."""
    frames = []
    trace = error.__traceback__
    while trace:
        code = trace.tb_frame.f_code
        path = Path(code.co_filename).resolve()
        if path.is_relative_to(ROOT):
            frames.append({'file': path.relative_to(ROOT).as_posix(), 'function': code.co_name,
                           'line': trace.tb_lineno})
        trace = trace.tb_next
    name = type(error).__name__
    return {'type': name if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,79}', name) else 'Exception',
            'frames': frames[-20:]}


def record_error(store, error, *, job_id=None, context='task'):
    """Persist up to 100 safe failures, independently of the short job history."""
    report = dict(exception_evidence(error), id=uuid.uuid4().hex, time=time.time(),
                  context=context if context in {'task', 'api', 'scheduler'} else 'task',
                  job_id=job_id if isinstance(job_id, str) and re.fullmatch(r'[a-f0-9]{32}', job_id) else None)
    causes, seen = [], {id(error)}
    cause = error.__cause__ or error.__context__
    while cause is not None and id(cause) not in seen and len(causes) < 4:
        seen.add(id(cause))
        causes.append(exception_evidence(cause))
        cause = cause.__cause__ or cause.__context__
    report['causes'] = causes
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS support_errors (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('INSERT INTO support_errors(payload) VALUES (?)', (json.dumps(report),))
        db.execute('DELETE FROM support_errors WHERE sequence NOT IN (SELECT sequence FROM support_errors ORDER BY sequence DESC LIMIT 100)')
    return report['id']


def export_report(store):
    """Build a shareable allowlist; no state blobs, activity text or raw logs."""
    state = store.read()
    files = {}
    for path in sorted(ROOT.rglob('*')):
        if path.is_file() and path.suffix in {'.py', '.js', '.css', '.html'}:
            text = path.read_text(encoding='utf-8').replace('# See AI_CODING_BASELINE_RULES.md for required practices.\n', '')
            files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(text.encode()).hexdigest()
    dependencies = {}
    for name in ('Flask', 'Werkzeug', 'requests', 'PlexAPI', 'waitress'):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = 'not installed'
    with store.connect() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='support_errors'").fetchone()
        errors = [json.loads(row[0]) for row in db.execute('SELECT payload FROM support_errors ORDER BY sequence DESC LIMIT 100')] if exists else []
    jobs = []
    for row in state.get('jobs', [])[:40]:
        jobs.append({'id': row['id'] if re.fullmatch(r'[a-f0-9]{32}', str(row.get('id', ''))) else None,
                     'kind': row.get('kind') if row.get('kind') in KINDS else 'Other task',
                     'status': row.get('status') if row.get('status') in {'running', 'queued', 'failed', 'succeeded'} else 'unknown',
                     **{key: row.get(key, 0) for key in ('started_at', 'finished_at') if type(row.get(key, 0)) in (int, float)}})
    settings = state.get('settings', {})
    model = settings.get('llm_model')
    from . import __version__
    return {'schema': 1, 'exported_at': time.time(), 'app_version': __version__,
            'code_fingerprint': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            'source_files': files, 'python': platform.python_version(), 'dependencies': dependencies,
            'jobs': jobs, 'errors': errors,
            'counts': {'library': len(state.get('library', [])), 'collections': len(state.get('collections', []))},
            'connections_configured': {name: bool(settings.get(name + '_url')) for name in ('plex', 'radarr', 'sonarr', 'tautulli', 'llm')},
            'curator': model if model in {'gpt-6-luna', 'gpt-6-sol', 'gpt-6-astra'} else 'custom' if model else 'not configured',
            'note': 'Older failures have no captured stack. Reproduce once after updating, then export again. No exception messages, locals, credentials, addresses, titles or raw logs are included.'}
