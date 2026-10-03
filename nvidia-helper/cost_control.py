"""Persistent conservative API spend guard, independent of provider secrets."""
import json
import math
import threading
import time
from pathlib import Path

PRICING_DATE = '2026-10-02'
LUNA_RATES = {'input': .10, 'cachedInput': .01, 'output': .50}  # USD / 1M tokens
_lock = threading.RLock()

def luna_cost(usage):
    inputs = int(usage.get('input_tokens', 0))
    cached = int(usage.get('input_tokens_details', {}).get('cached_tokens', 0))
    outputs = int(usage.get('output_tokens', 0))
    return (max(0, inputs - cached) * .10 + min(inputs, cached) * .01 + outputs * .50) / 1e6

class SpendLedger:
    def __init__(self, path, cap):
        self.path = Path(path)
        self.cap = float(cap)
        if not math.isfinite(self.cap) or self.cap <= 0:
            raise ValueError('API budget must be positive and finite.')

    def load(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'spentUSD': 0, 'receipts': [], 'pending': None}

    def save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.writing')
        temp.write_text(json.dumps(state, indent=2), encoding='utf-8')
        temp.replace(self.path)

    def reserve(self, body):
        # Includes schema and developer instructions, and a vision allowance.
        # GPT-6 Luna standard short-context prices; no free-token assumption.
        maximum = int(body['max_output_tokens'])
        encoded = json.dumps(body).encode('utf-8')
        image_count = sum(len(m.get('content', [])) for m in body['input'] if isinstance(m.get('content'), list))
        input_bound = len(encoded) + image_count * 8192
        # Data URLs make len(encoded) a safe but excessively large bound. Image
        # bytes are excluded and the generous image allowance is used instead.
        import copy
        clean = copy.deepcopy(body)
        for message in clean['input']:
            if isinstance(message.get('content'), list):
                for item in message['content']:
                    if item.get('type') == 'input_image':
                        item['image_url'] = '[image]'
        input_bound = len(json.dumps(clean).encode('utf-8')) + image_count * 8192
        bound = (input_bound * .10 + maximum * .50) / 1e6
        with _lock:
            state = self.load()
            if state['pending']:
                raise RuntimeError('A previous API request has unresolved usage. Review its spend receipt before continuing.')
            if state['spentUSD'] + bound > self.cap:
                raise RuntimeError(f'API budget guard: next request could exceed ${self.cap:.3f}. Saved work is preserved.')
            state['pending'] = {'reservedUSD': bound, 'started': time.time(), 'model': body['model']}
            self.save(state)
        return bound

    def settle(self, usage=None, uncharged=False):
        with _lock:
            state = self.load()
            pending = state['pending']
            if not pending:
                return 0
            cost = 0 if uncharged else luna_cost(usage) if usage is not None else pending['reservedUSD']
            state['spentUSD'] += cost
            state['receipts'].append(pending | {'finished': time.time(), 'usage': usage, 'estimatedUSD': cost,
                'usageConfirmed': usage is not None, 'pricingDate': PRICING_DATE, 'uncharged': uncharged})
            state['pending'] = None
            self.save(state)
            return cost
