"""Exact input reuse for director guards, independent of project revisions.

Fresh project reads and the ordinary SHA signature remain authoritative. Only
an input document previously checked against that SHA can bypass re-encoding
the same document as sorted JSON. The compact key is never deserialized.
"""
import pickle
import sys
import threading
from collections import OrderedDict
from studio_data import digest, get_chapter, state_before


def analysis_input_document(project, chapter_id):
    """Keep the existing analysis signature's complete input schema in one place."""
    chapter=get_chapter(project,chapter_id)
    incoming_state,incoming_memory=state_before(project,chapter_id)
    fields=('sourceText','name','scenes','people','narrationMode','cleanNarrationText','includeChapterLabel')
    return {'chapter':{key:chapter.get(key) for key in fields},
        'audio':chapter.get('audio',{}).get('signature'),'settings':project['settings'],
        'inputState':incoming_state,'inputMemory':incoming_memory,
        'identities':[{key:person.get(key) for key in ('id','name','description','aliases',
            'permanentIdentity','defaultAppearance','references')}
            for person in project['characters']]}


def analysis_input_signature(project,chapter_id):
    return digest(analysis_input_document(project,chapter_id))


_CANONICAL_SIGNATURE=analysis_input_signature


class ExactAnalysisInputs:
    """Bounded immutable comparisons; changed inputs always use the original SHA.

    Pickle is used only as a type-preserving byte encoder of JSON project data;
    nothing is unpickled. Unlike Python equality, it distinguishes True, 1 and
    1.0. Order/alias differences can cause harmless extra SHA checks. No cached
    key can authorize a different input document. Metadata is conservatively
    included in the eight MiB budget, and entries are additionally capped at 8.
    """
    def __init__(self,max_bytes=8*1024*1024,max_entries=8):
        self.max_bytes=max_bytes;self.max_entries=max_entries
        self._entries=OrderedDict();self._sizes={};self._bytes=0
        self._lock=threading.RLock()

    @property
    def accounted_bytes(self):
        with self._lock:return self._bytes

    @property
    def entries(self):
        with self._lock:return len(self._entries)

    def matches(self,project,chapter_id,expected,signature):
        # A subclass/mock can depend on state outside this document. Only the
        # known pure production function has the exact schema needed to cache.
        if signature is not _CANONICAL_SIGNATURE:
            return signature(project,chapter_id)==expected
        document=analysis_input_document(project,chapter_id)
        encoded=pickle.dumps(document,protocol=5)
        key=(project['id'],chapter_id,expected)
        size=sys.getsizeof(encoded)+sys.getsizeof(key)+sum(sys.getsizeof(v) for v in key)+256
        with self._lock:
            if key in self._entries and self._entries[key]==encoded:
                self._entries.move_to_end(key)
                return True
            # A miss never substitutes a different signature algorithm.
            if signature(project,chapter_id)!=expected:return False
            # Fresh store loads are private snapshots. Still fail closed if an
            # adapter changes that object while the ordinary signature runs;
            # bytes from before such a change must never borrow its new SHA.
            if pickle.dumps(analysis_input_document(project,chapter_id),protocol=5)!=encoded:return False
            if key in self._entries:
                self._entries.pop(key);self._bytes-=self._sizes.pop(key)
            if size<=self.max_bytes and self.max_entries>0:
                while self._entries and (self._bytes+size>self.max_bytes or len(self._entries)>=self.max_entries):
                    old,_=self._entries.popitem(last=False)
                    self._bytes-=self._sizes.pop(old)
                self._entries[key]=encoded;self._sizes[key]=size;self._bytes+=size
            return True
