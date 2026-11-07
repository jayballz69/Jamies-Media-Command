"""Hash-only Morgin grants and an explicit local-owner enrollment CLI.

This module never imports or constructs the application's Store or Service.
The CLI runs as the Unix owner of the existing private data directory; network
requests only read the grant file and never initialize or repair it.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from urllib.parse import urlsplit
import uuid


CAPABILITIES = {
    'overview': {'id': 'overview', 'title': 'Collection overview', 'description': 'Read saved collection counts and library cache age; no refresh or connection test.', 'input': 'none'},
    'search-collections': {'id': 'search-collections', 'title': 'Find collections', 'description': 'Search saved collection names and descriptions, with bounded metadata only.', 'input': 'search'},
    'collection': {'id': 'collection', 'title': 'Read a collection', 'description': 'Read one exact saved collection summary and counts, without a library export.', 'input': 'id'},
    'requests': {'id': 'requests', 'title': 'Saved request progress', 'description': 'Read existing requested-title and job status; never start or refresh work.', 'input': 'none'},
}
PROTOCOL = 'morgin-read-v1'
STORE_NAME = 'morgin-grants.json'
MAX_STORE_BYTES = 64 * 1024
MAX_GRANTS = 64
MAX_LIFETIME = 30 * 86400
IDENTITY = re.compile(r'[A-Za-z0-9_-]{1,160}\Z')
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')


class GrantFailure(Exception):
    def __init__(self, status=503):
        self.status = status
        super().__init__('Morgin access is unavailable.' if status == 503 else 'Morgin authorization is required.')


class EnrollmentFailure(Exception):
    """Only fixed, nonsecret CLI messages are allowed."""


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def timestamp(value):
    if not isinstance(value, str) or len(value) > 32 or not value.endswith('Z'):
        raise ValueError('Invalid timestamp')
    parsed = datetime.fromisoformat(value[:-1] + '+00:00').timestamp()
    if iso(parsed) != value:
        raise ValueError('Noncanonical timestamp')
    return parsed


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate field')
        result[key] = value
    return result


def _validate(value):
    if not isinstance(value, dict) or set(value) != {'version', 'grants'} or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Invalid grant store')
    rows = value['grants']
    if not isinstance(rows, list) or len(rows) > MAX_GRANTS:
        raise ValueError('Invalid grants')
    identities, hashes = set(), set()
    fields = {'grantId', 'principal', 'installationId', 'tokenHash', 'issuedAt', 'expiresAt', 'revokedAt', 'capabilities'}
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields or row['principal'] != 'morgin':
            raise ValueError('Invalid grant')
        if any(not isinstance(row[key], str) or not IDENTITY.fullmatch(row[key]) for key in ('grantId', 'installationId')):
            raise ValueError('Invalid identity')
        if not isinstance(row['tokenHash'], str) or not HASH.fullmatch(row['tokenHash']):
            raise ValueError('Invalid digest')
        if row['grantId'] in identities or row['tokenHash'] in hashes:
            raise ValueError('Repeated grant')
        identities.add(row['grantId']); hashes.add(row['tokenHash'])
        scopes = row['capabilities']
        if not isinstance(scopes, list) or not scopes or any(not isinstance(scope, str) or scope not in CAPABILITIES for scope in scopes) or len(scopes) != len(set(scopes)):
            raise ValueError('Invalid scopes')
        issued, expiry = timestamp(row['issuedAt']), timestamp(row['expiresAt'])
        if not 0 < expiry - issued <= MAX_LIFETIME or issued <= 0:
            raise ValueError('Invalid lifetime')
        if row['revokedAt'] is not None:
            timestamp(row['revokedAt'])
    return value


class GrantStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / STORE_NAME

    def read(self):
        try:
            if self.path.is_symlink():
                raise ValueError('A regular private grant file is required')
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            with os.fdopen(os.open(self.path, flags), 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STORE_BYTES:
                    raise ValueError('Invalid grant file')
                if os.name == 'posix' and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
                    raise ValueError('Private grant file required')
                raw = handle.read(MAX_STORE_BYTES + 1)
                if len(raw) > MAX_STORE_BYTES:
                    raise ValueError('Large grant file')
            return _validate(json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object))
        except Exception:
            raise GrantFailure() from None

    def authenticate(self, header, now=None):
        if not isinstance(header, str) or not header.startswith('Bearer ') or not TOKEN.fullmatch(header[7:]):
            raise GrantFailure(401)
        digest = hashlib.sha256(header[7:].encode('ascii')).hexdigest()
        matches = [row for row in self.read()['grants'] if hmac.compare_digest(row['tokenHash'], digest)]
        if len(matches) != 1 or matches[0]['revokedAt'] is not None or timestamp(matches[0]['expiresAt']) <= (time.time() if now is None else now):
            raise GrantFailure(401)
        return matches[0]


def _owner_directory(directory):
    # NTFS needs a different enrollment authority/ACL implementation. Refuse to
    # pretend that POSIX mode bits make a Windows plaintext file private.
    if os.name != 'posix':
        raise EnrollmentFailure('Run enrollment as the container Unix owner.')
    path = Path(directory)
    if not path.is_absolute() or path.is_symlink():
        raise EnrollmentFailure('Choose the existing private application data directory.')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise EnrollmentFailure('The data directory must be owner-only and owned by this user.')
    if not (path / 'collection-manager.sqlite3').is_file():
        raise EnrollmentFailure('The existing application data directory is required.')
    return path.resolve()


@contextmanager
def _locked(directory):
    lock = directory / (STORE_NAME + '.lock')
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        yield
    finally:
        os.close(descriptor)
        lock.unlink()


def _write_exclusive(path, payload):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _save(directory, state):
    _validate(state)
    raw = json.dumps(state, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_STORE_BYTES:
        raise EnrollmentFailure('The grant store is full.')
    temporary = directory / ('.morgin-grants-' + uuid.uuid4().hex + '.tmp')
    try:
        _write_exclusive(temporary, raw)
        os.replace(temporary, directory / STORE_NAME)
        if os.name == 'posix':
            descriptor = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def summary(row):
    return {key: row[key] for key in ('grantId', 'principal', 'installationId', 'issuedAt', 'expiresAt', 'revokedAt', 'capabilities')}


def issue(directory, *, installation_id, expires_at, origin, output, capabilities, now=None):
    directory = _owner_directory(directory)
    now = time.time() if now is None else now
    expiry = timestamp(expires_at)
    if not IDENTITY.fullmatch(installation_id) or not 0 < expiry - now <= MAX_LIFETIME:
        raise EnrollmentFailure('Choose an installation ID and expiry within thirty days.')
    if not capabilities or any(scope not in CAPABILITIES for scope in capabilities) or len(set(capabilities)) != len(capabilities):
        raise EnrollmentFailure('Choose only the implemented read capabilities.')
    endpoint = urlsplit(origin)
    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or endpoint.username or endpoint.password or not endpoint.port or not 1024 <= endpoint.port <= 65535 or origin != f'http://127.0.0.1:{endpoint.port}':
        raise EnrollmentFailure('Use the consumer numeric-loopback SSH forward origin.')
    output = Path(output)
    if not output.is_absolute() or output.parent.resolve() != directory or not re.fullmatch(r'morgin-enrollment-[A-Za-z0-9_-]{1,80}\.json', output.name):
        raise EnrollmentFailure('Choose a new morgin-enrollment-NAME.json file inside the private data directory.')
    with _locked(directory):
        store = GrantStore(directory)
        state = store.read() if store.path.exists() or store.path.is_symlink() else {'version': 1, 'grants': []}
        if len(state['grants']) >= MAX_GRANTS:
            raise EnrollmentFailure('The grant store is full.')
        token = secrets.token_urlsafe(32)
        row = {'grantId': uuid.uuid4().hex, 'principal': 'morgin', 'installationId': installation_id,
               'tokenHash': hashlib.sha256(token.encode('ascii')).hexdigest(), 'issuedAt': iso(now),
               'expiresAt': expires_at, 'revokedAt': None, 'capabilities': list(capabilities)}
        receipt = {'version': 1, 'appId': 'media', 'bridgeOrigin': origin, 'grantId': row['grantId'], 'expiresAt': expires_at, 'token': token}
        _write_exclusive(output, json.dumps(receipt, separators=(',', ':')).encode('utf-8'))
        try:
            state['grants'].append(row)
            _save(directory, state)
        except Exception:
            output.unlink(missing_ok=True)
            raise
        return summary(row)


def revoke(directory, grant_id):
    directory = _owner_directory(directory)
    with _locked(directory):
        state = GrantStore(directory).read()
        row = next((row for row in state['grants'] if row['grantId'] == grant_id), None)
        if row is None:
            raise EnrollmentFailure('The grant was not found.')
        row['revokedAt'] = row['revokedAt'] or iso(time.time())
        _save(directory, state)
        return summary(row)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local owner administration of the fixed Morgin read grant.')
    parser.add_argument('--data-dir', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    add = commands.add_parser('issue')
    add.add_argument('--installation-id', required=True)
    add.add_argument('--expires-at', required=True, help='UTC ISO timestamp with milliseconds, ending Z')
    add.add_argument('--origin', required=True)
    add.add_argument('--output', required=True)
    add.add_argument('--capability', action='append', choices=tuple(CAPABILITIES), required=True)
    commands.add_parser('list')
    remove = commands.add_parser('revoke')
    remove.add_argument('--grant-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'issue':
            result = issue(args.data_dir, installation_id=args.installation_id, expires_at=args.expires_at,
                           origin=args.origin, output=args.output, capabilities=args.capability)
        elif args.command == 'revoke':
            result = revoke(args.data_dir, args.grant_id)
        else:
            result = [summary(row) for row in GrantStore(_owner_directory(args.data_dir)).read()['grants']]
        print(json.dumps(result, separators=(',', ':')))
        return 0
    except EnrollmentFailure as error:
        print(str(error), file=sys.stderr)
    except Exception:
        print('Morgin grant administration could not complete. No secret is displayed.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
