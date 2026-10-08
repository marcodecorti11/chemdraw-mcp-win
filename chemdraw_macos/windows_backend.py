"""Windows document read/append backend: the add-in contract over COM.

The macOS JavaScript add-in reads and appends to the *active* document through a private
loopback channel. Windows ChemDraw exposes the same operations through COM, so this
backend presents the same `read`/`append`/`channel.request` interface and reuses the
shared `read_document`/`append_document` checks unchanged: freshness tokens, at-most-once
writes, uncertain-write reporting and post-write verification.
"""
from .addin import AddinReadError, append_document, read_document, source_token
from .core import document_row, validate_cdxml


class ComChannel:
    """Same request contract as AddinChannel; one native call per request, never replayed."""

    def __init__(self, bridge):
        self.bridge = bridge

    def request(self, operation, *, timeout=20, **data):
        if operation not in ('read', 'append', 'close') or set(data) - {'expected', 'cdxml'}:
            raise ValueError('Unsupported add-in operation')
        if operation == 'append' and (not data.get('expected') or not data.get('cdxml')):
            raise ValueError('Append requires an expected snapshot and CDXML')
        if operation != 'append' and data:
            raise ValueError('Unexpected operation arguments')
        if operation == 'close':
            return {'write_attempted': False}
        if operation == 'read':
            return self.bridge._run('channel_read')
        return self.bridge._run('channel_append', data['expected'], data['cdxml'])


class ComBackend:
    transport = 'windows_com'

    def __init__(self, bridge):
        self.bridge = bridge
        self.channel = ComChannel(bridge)
        self.closed = False
        self.opened = True

    def connect(self):
        return {'status': 'connected', 'transport': self.transport}

    def _ready(self):
        return None

    def read(self, document_id):
        result = read_document(self.bridge, self.channel, document_id)
        result['transport'] = self.transport
        return result

    def append(self, document_id, cdxml, expected_source_token, *, allow_page_expansion=False):
        result = append_document(self.bridge, self.channel, document_id, cdxml, expected_source_token,
                                 allow_page_expansion=allow_page_expansion)
        result['transport'] = self.transport
        result['note'] = ('Same-document append at supplied coordinates. The document is marked modified so '
                          'ChemDraw prompts before discarding it.')
        return result

    def read_explicit(self, document_id):
        return read_explicit(self.bridge, document_id)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def read_explicit(bridge, document_id):
    """Read an explicit document without activating it (no tab, focus or view change)."""
    try:
        did = bridge._id(document_id)
    except (ValueError, TypeError, OverflowError) as exc:
        raise AddinReadError('invalid_document_id', 'document_id') from exc
    with bridge.lock:
        row, cdxml, selection, metadata = bridge._run('read_document', did)
        try:
            validate_cdxml(cdxml)
        except (ValueError, TypeError) as exc:
            raise AddinReadError('invalid_cdxml', 'cdxml_validation') from exc
        if row[0] != did:
            raise AddinReadError('document_changed', 'document_identity')
        return {'document': document_row(row), 'cdxml': cdxml, 'selection_cdxml': selection,
                'source_token': source_token(cdxml), 'api_version': 'COM ChemDraw_x64.Application',
                'transport': 'windows_com', 'selection_changed': False,
                'metadata_annotations_retained': len(metadata)}
