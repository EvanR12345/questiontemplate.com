"""Persistent conservative API spend guard, independent of provider secrets."""
import json
import math
import copy
import hashlib
import errno
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

PRICING_DATE = '2026-10-09'
# Standard short-context pricing. Cache writes are part of input_tokens,
# not additional input tokens: https://developers.openai.com/api/docs/pricing
LUNA_RATES = {'input': .10, 'cachedInput': .01, 'cacheWrite': .125, 'output': .50}
_lock = threading.RLock()


def _tokens(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError('Token usage must contain nonnegative integers.')
    return value


def _nanos(value, up=True):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError('Money must be finite and nonnegative.')
    return int((amount * 10**9).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR))


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise ValueError('Request and owner identifiers must be short opaque identifiers.')
    return value

def luna_cost(usage, service_tier='default'):
    inputs = _tokens(usage.get('input_tokens', 0))
    details = usage.get('input_tokens_details', {})
    if not isinstance(details, dict):
        raise ValueError('Invalid input token details.')
    cached = _tokens(details.get('cached_tokens', 0))
    written = _tokens(details.get('cache_write_tokens', 0))
    outputs = _tokens(usage.get('output_tokens', 0))
    if min(inputs, cached, written, outputs) < 0 or cached + written > inputs:
        raise ValueError('Invalid token usage: cache reads and writes must fit within input tokens.')
    if service_tier not in ('default', 'flex','fast','priority'):
        raise ValueError('Unrecognized Luna billing tier.')
    multiplier = .5 if service_tier == 'flex' else 2 if service_tier in ('fast','priority') else 1
    input_multiplier = 2 if inputs > 272000 else 1
    output_multiplier = 1.5 if inputs > 272000 else 1
    return multiplier * (input_multiplier * ((inputs - cached - written) * LUNA_RATES['input']
            + cached * LUNA_RATES['cachedInput'] + written * LUNA_RATES['cacheWrite']
            ) + output_multiplier * outputs * LUNA_RATES['output']) / 1e6

class SpendLedger:
    """Atomic JSON reservations with an OS lock and explicit request ownership.

    Legacy reserve/settle calls retain their one-pending-request contract.
    Explicit request IDs retain UNKNOWN liability until a valid receipt arrives.
    allow_multiple is opt-in accounting support; it does not start worker lanes.
    Existing receipt dictionaries are retained without rewriting their prices.
    """
    def __init__(self, path, cap, *, allow_multiple=False):
        if isinstance(cap, bool) or not isinstance(allow_multiple, bool):
            raise ValueError('API budget and reservation mode must have valid types.')
        self.path = Path(path)
        self.cap = float(cap)
        if not math.isfinite(self.cap) or self.cap <= 0:
            raise ValueError('API budget must be positive and finite.')
        self.cap_nanos = _nanos(cap, up=False)
        self.allow_multiple = allow_multiple

    @contextmanager
    def _guard(self):
        # Atomic rename alone does not stop two processes spending the same cap.
        # Kernel locks are released on process exit; no stale-lock deletion needed.
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(self.path.suffix + '.lock').open('a+b') as handle:
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b'\0'); handle.flush()
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError as error:
                            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                                raise
                            if time.monotonic() >= deadline:
                                raise RuntimeError('API ledger is busy; no request was submitted.') from None
                            time.sleep(.01)
                    try:
                        yield
                    finally:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _view(state):
        outstanding = list(state['requests'].values())
        if len(outstanding) == 1:
            state['pending'] = copy.deepcopy(outstanding[0])
        elif outstanding:
            state['pending'] = {'count': len(outstanding), 'requestIds': list(state['requests']),
                'reservedUSD': sum(r['reservedNanoUSD'] for r in outstanding) / 1e9}
        else:
            state['pending'] = None
        state['reservedUSD'] = sum(r['reservedNanoUSD'] for r in outstanding) / 1e9
        state['liabilityUSD'] = (state['spentNanoUSD'] + sum(r['reservedNanoUSD'] for r in outstanding)) / 1e9
        return state

    def _read(self):
        state = json.loads(self.path.read_text(encoding='utf-8-sig')) if self.path.exists() else {'spentUSD': 0, 'receipts': [], 'pending': None}
        version_two = state.get('schemaVersion') == 2 if isinstance(state, dict) else False
        previous_pending = state.get('pending') if isinstance(state, dict) else None
        if not isinstance(state, dict) or state.get('schemaVersion', 1) not in (1, 2) or not isinstance(state.get('receipts'), list):
            raise ValueError('Invalid or unsupported API ledger; reconcile before dispatch.')
        if state.get('schemaVersion') == 2 and 'requests' not in state:
            raise ValueError('API ledger reservations are missing; reconcile before dispatch.')
        spent = state.get('spentNanoUSD', _nanos(state['spentUSD']))
        if not isinstance(spent, int) or isinstance(spent, bool) or spent < 0:
            raise ValueError('Invalid API spend total.')
        if spent != _nanos(state['spentUSD']):
            raise ValueError('Inconsistent API spend totals; reconcile before dispatch.')
        state['spentNanoUSD'] = spent
        if 'requests' not in state:
            state['requests'] = {}
            if state.get('pending'):
                previous = state['pending']
                rid = 'legacy-' + hashlib.sha256(json.dumps(previous, sort_keys=True).encode()).hexdigest()[:32]
                state['requests'][rid] = previous | {'requestId': rid, 'owner': 'legacy', 'status': 'UNKNOWN',
                    'reservedNanoUSD': _nanos(previous['reservedUSD']), 'legacy': True}
        if not isinstance(state['requests'], dict):
            raise ValueError('Invalid API reservations.')
        for rid, row in state['requests'].items():
            _identifier(rid); _identifier(row['owner'])
            if row.get('requestId') != rid or row.get('status') not in ('HELD', 'SUBMITTED', 'UNKNOWN'):
                raise ValueError('Invalid API request state.')
            maximum = row.get('reservedNanoUSD')
            if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
                raise ValueError('Invalid API request reservation.')
            if _nanos(row['reservedUSD']) != maximum:
                raise ValueError('Inconsistent API request reservation; reconcile before dispatch.')
        state['schemaVersion'] = 2
        view = self._view(state)
        if version_two and previous_pending != view['pending']:
            # An older helper can preserve `requests` yet overwrite only its
            # legacy pending slot. Never silently hide that extra liability.
            raise ValueError('Inconsistent API pending reservation; reconcile possible older-writer changes before dispatch.')
        return view

    def load(self):
        with self._guard():
            return self._read()

    def _save(self, state):
        state = self._view(state)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                    prefix=self.path.name + '.', suffix='.writing', delete=False) as handle:
                name = Path(handle.name)
                # A buffered compact snapshot avoids thousands of tiny encoder
                # writes as receipt history grows. Receipt values stay intact;
                # flush/fsync and atomic replacement still precede dispatch.
                handle.write(json.dumps(state, separators=(',', ':'), allow_nan=False))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            if name is not None:
                name.unlink(missing_ok=True)

    @staticmethod
    def estimate(body):
        # Includes schema and developer instructions, and a vision allowance.
        # GPT-6 Luna standard short-context prices; no free-token assumption.
        maximum = _tokens(body['max_output_tokens'])
        if maximum <= 0 or body['model'] != 'gpt-6-luna':
            raise ValueError('This API ledger requires Luna and a positive output limit.')
        image_count = sum(item.get('type') == 'input_image'
                          for message in body['input'] if isinstance(message.get('content'), list)
                          for item in message['content'])
        # Data URLs make len(encoded) a safe but excessively large bound. Image
        # bytes are excluded and the generous image allowance is used instead.
        clean = copy.deepcopy(body)
        for message in clean['input']:
            if isinstance(message.get('content'), list):
                for item in message['content']:
                    if item.get('type') == 'input_image':
                        item['image_url'] = '[image]'
        input_bound = len(json.dumps(clean).encode('utf-8')) + image_count * 8192
        # Reserve at the more expensive cache-write rate even when cache reads
        # are expected. A cache miss must not escape the budget reservation.
        input_multiplier = 2 if input_bound > 272000 else 1
        output_multiplier = 1.5 if input_bound > 272000 else 1
        tier=body.get('service_tier','default')
        if tier not in ('default','flex','fast','priority'):raise ValueError('Unrecognized requested Luna billing tier.')
        premium=2 if tier in ('fast','priority') else 1
        bound = premium * (input_multiplier * input_bound * LUNA_RATES['cacheWrite']
                 + output_multiplier * maximum * LUNA_RATES['output']) / 1e6
        return _nanos(bound) / 1e9

    def reserve(self, body, *, request_id=None, owner=None):
        legacy = request_id is None
        if legacy and owner is not None:
            raise ValueError('An explicit owner requires an explicit request ID.')
        rid = _identifier(request_id) if not legacy else 'legacy-' + uuid.uuid4().hex
        owner = _identifier(owner) if not legacy else 'legacy'
        bound = self.estimate(body)
        with self._guard():
            state = self._read()
            if rid in state['requests'] or any(r.get('requestId') == rid for r in state['receipts']):
                raise ValueError('Request already exists; reconcile rather than resubmit.')
            if state.get('halted'):
                raise RuntimeError('API ledger halted after a conflicting receipt or exceeded reservation; reconcile before dispatch.')
            if self.allow_multiple and state.get('capNanoUSD', self.cap_nanos) != self.cap_nanos:
                raise RuntimeError('API budget cap changed between owners; reconcile the shared allowance before dispatch.')
            if state['requests'] and (legacy or not self.allow_multiple):
                raise RuntimeError('A previous API request has unresolved usage. Review its spend receipt before continuing.')
            reserved = sum(r['reservedNanoUSD'] for r in state['requests'].values())
            if state['spentNanoUSD'] + reserved + _nanos(bound) > self.cap_nanos:
                raise RuntimeError(f'API budget guard: next request could exceed ${self.cap:.3f}. Saved work is preserved.')
            state['capNanoUSD'] = self.cap_nanos
            state['requests'][rid] = {'requestId': rid, 'owner': owner, 'legacy': legacy,
                'status': 'HELD', 'reservedUSD': bound, 'reservedNanoUSD': _nanos(bound),
                'started': time.time(), 'model': body['model'],
                'requestHash': hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
                'requestedServiceTier': body.get('service_tier', 'default')}
            self._save(state)
        return bound

    def _find(self, state, request_id, owner):
        if request_id is None:
            if not state['requests']:
                return None, None
            if len(state['requests']) != 1 or not next(iter(state['requests'].values())).get('legacy'):
                raise RuntimeError('Explicit request ID and owner are required; unrelated reservations are preserved.')
            rid, row = next(iter(state['requests'].items()))
            return rid, row
        _identifier(request_id); _identifier(owner)
        row = state['requests'].get(request_id)
        if row is None:
            row = next((r for r in state['receipts'] if r.get('requestId') == request_id), None)
        if row is None:
            raise KeyError('Unknown API request ID.')
        if row.get('owner') != owner:
            raise PermissionError('Only the request owner may change its reservation.')
        return request_id, row

    def mark_submitted(self, request_id, *, owner):
        with self._guard():
            state = self._read(); rid, row = self._find(state, request_id, owner)
            if state.get('halted'):
                raise RuntimeError('API dispatch is halted; held requests have not been submitted.')
            if state.get('capNanoUSD', self.cap_nanos) != self.cap_nanos:
                raise RuntimeError('API budget cap changed; reconcile before submitting this held request.')
            if row['status'] != 'HELD':
                raise RuntimeError('Request already submitted or uncertain; reconcile rather than resubmit.')
            row.update(status='SUBMITTED', submitted=time.time())
            self._save(state)

    def bind_response(self, request_id, response_id, *, owner):
        _identifier(response_id)
        with self._guard():
            state = self._read(); rid, row = self._find(state, request_id, owner)
            if row.get('responseId') == response_id:
                return
            if rid not in state['requests'] or row.get('responseId') or any(r.get('responseId') == response_id for r in
                    list(state['requests'].values()) + state['receipts']):
                state['halted'] = 'CONFLICTING_RESPONSE_ID'
                self._save(state)
                raise ValueError('Conflicting provider response ID; reservations retained for reconciliation.')
            row['responseId'] = response_id
            if row['status'] == 'HELD':
                row.update(status='SUBMITTED', submitted=time.time())
            self._save(state)

    def request_state(self, request_id, *, owner):
        with self._guard():
            state = self._read()
            _, row = self._find(state, request_id, owner)
            return copy.deepcopy(row)

    def mark_unknown(self, request_id, *, owner):
        with self._guard():
            state = self._read(); rid, row = self._find(state, request_id, owner)
            if rid not in state['requests']:
                return False
            if row['status'] != 'UNKNOWN':
                row.update(status='UNKNOWN', uncertaintySince=time.time())
                self._save(state)
            return True

    def cancel(self, request_id, *, owner):
        """Only a reservation never dispatched can be cancelled without liability."""
        with self._guard():
            state = self._read(); rid, row = self._find(state, request_id, owner)
            if rid not in state['requests']:
                return False
            if row['status'] != 'HELD' or row.get('responseId'):
                row.update(status='UNKNOWN', uncertaintySince=time.time())
                self._save(state)
                return False
            self._finish(state, rid, row, None, 0, False, True, 'default')
            return True

    def reject(self, request_id, http_status, *, owner):
        # Timeouts, conflicts and server errors are not proof of zero usage.
        if http_status not in (400, 401, 403, 404, 413, 415, 422, 429):
            self.mark_unknown(request_id, owner=owner)
            return False
        with self._guard():
            state = self._read(); rid, row = self._find(state, request_id, owner)
            if rid not in state['requests']:
                return False
            if row.get('responseId'):
                row.update(status='UNKNOWN', uncertaintySince=time.time())
                self._save(state)
                return False
            row['rejectionHTTPStatus'] = http_status
            self._finish(state, rid, row, None, 0, False, True, 'default')
            return True

    def _finish(self, state, rid, pending, usage, cost, confirmed, uncharged, service_tier):
        state['spentNanoUSD'] += _nanos(cost)
        state['spentUSD'] = state['spentNanoUSD'] / 1e9
        state['receipts'].append(pending | {'status': 'SETTLED', 'finished': time.time(), 'usage': usage,
            'estimatedUSD': cost, 'usageConfirmed': confirmed, 'pricingDate': PRICING_DATE,
            'uncharged': uncharged, 'serviceTier': service_tier})
        del state['requests'][rid]
        if _nanos(cost) > pending['reservedNanoUSD']:
            state['halted'] = 'RESERVATION_EXCEEDED'
        self._save(state)

    def settle(self, usage=None, uncharged=False, service_tier='default', *, request_id=None, owner=None):
        with self._guard():
            state = self._read(); rid, pending = self._find(state, request_id, owner)
            if pending is None:
                return 0
            if rid not in state['requests']:
                if (pending.get('usage') != usage or pending['serviceTier'] != service_tier
                        or pending['uncharged'] != uncharged):
                    state['halted'] = 'CONFLICTING_RECEIPT'; self._save(state)
                    raise ValueError('Conflicting duplicate receipt; no historical charge was changed.')
                return pending['estimatedUSD']
            confirmed = isinstance(usage, dict) and all(
                isinstance(usage.get(field), int) and not isinstance(usage.get(field), bool)
                and usage[field] >= 0 for field in ('input_tokens', 'output_tokens'))
            if uncharged:
                if request_id is not None and (pending['status'] != 'HELD' or pending.get('responseId')):
                    raise ValueError('Submitted requests need rejection evidence or usage; cancellation is not free.')
                cost = 0
            elif confirmed:
                try:
                    cost = luna_cost(usage, service_tier)
                except (ValueError, TypeError, AttributeError):
                    confirmed = False
                    cost = pending['reservedUSD']
            else:
                # A successful stream with absent/incomplete usage is not free.
                cost = pending['reservedUSD']
            if request_id is not None and not confirmed and not uncharged:
                pending.update(status='UNKNOWN', uncertaintySince=time.time())
                self._save(state)
                return cost
            cost = _nanos(cost) / 1e9
            self._finish(state, rid, pending, usage, cost, confirmed, uncharged, service_tier)
            if request_id is not None and _nanos(cost) > pending['reservedNanoUSD']:
                raise RuntimeError('Provider exceeded the API reservation; charge persisted and dispatch halted.')
            return cost
