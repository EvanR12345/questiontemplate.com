"""Build compact, pinned Misaki dictionaries; Apache-2.0 (see THIRD-PARTY.md)."""
import gzip
import json
import re
import urllib.request
from pathlib import Path

REVISION = 'fba1236595f2d2bf21d414ba6e57d25256afada3'
ROOT = Path(__file__).resolve().parents[1]

for locale in ('us', 'gb'):
    url = f'https://raw.githubusercontent.com/hexgrad/misaki/{REVISION}/misaki/data/{locale}_gold.json'
    source = json.load(urllib.request.urlopen(url, timeout=60))
    # Context-dependent entries stay in the phrase-level eSpeak fallback.
    words = {k: v.replace('ɾ', 'T').replace('ʔ', 't')
             for k, v in source.items()
             if isinstance(v, str) and re.fullmatch(r"[a-z]+(?:'[a-z]+)?", k) and len(k) > 1}
    raw = json.dumps(words, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()
    compressed = gzip.compress(raw, mtime=0)
    (ROOT / 'vendor' / f'english-{locale}.json.gz').write_bytes(compressed)
    print(locale, len(words), 'words;', len(compressed), 'download bytes')
