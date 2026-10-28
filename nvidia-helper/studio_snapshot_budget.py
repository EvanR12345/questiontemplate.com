"""Conservative local RAM gate for three extra retained shot snapshots.

No serialization, copying, OS probes, provider calls or project mutations.
Object sizes overcount immutable values shared by deepcopy deliberately.
"""
import math
import sys

EXTRA_SNAPSHOTS=3
MAX_EXTRA_BYTES=128*2**20
SPARE_BYTES=512*2**20
MAX_VISITS=1000000
MAX_UNIQUE_OBJECTS=300000
MAX_DEPTH=128
MAX_PENDING=250000


def snapshot_budget_details(project,available_bytes):
    """Return numeric-only evidence; unknown/unsupported inputs fail closed."""
    report=dict(allowed=False,extraSnapshots=EXTRA_SNAPSHOTS,maximumExtraBytes=MAX_EXTRA_BYTES,
                spareBytes=SPARE_BYTES,reason='unknown_available_memory',visits=0,uniqueObjects=0)
    if type(available_bytes) not in (int,float):return report
    try:
        if not math.isfinite(available_bytes) or available_bytes<=0:return report
    except (OverflowError,ValueError):return report
    report['availableBytes']=available_bytes
    if available_bytes<SPARE_BYTES:
        report['reason']='insufficient_available_memory';return report
    if type(project) is not dict:
        report['reason']='unsupported_project';return report
    seen=set();active=set();stack=[project];size=0;visits=0;exit_marker=object()
    # Low available RAM can reject a large project after a small prefix. There
    # is no reason to traverse the whole story once a lower bound cannot fit.
    ram_room=min(available_bytes-SPARE_BYTES,MAX_EXTRA_BYTES*1.5)
    hard_limit=MAX_EXTRA_BYTES//EXTRA_SNAPSHOTS
    limit=min(hard_limit,int(ram_room*2//(3*EXTRA_SNAPSHOTS)))
    def finish(reason,**values):
        report.update(reason=reason,visits=visits,uniqueObjects=len(seen),**values)
        return report
    get_size=sys.getsizeof;seen_add=seen.add
    try:
        while stack:
            value=stack.pop()
            if value is exit_marker:
                active.remove(stack.pop());continue
            visits+=1
            if visits>MAX_VISITS:return finish('traversal_visit_limit')
            identity=id(value)
            if identity in seen:
                if identity in active:return finish('cyclic_project')
                continue
            kind=type(value)
            if kind not in (dict,list,str,int,float,bool,type(None)):
                return finish('unsupported_value')
            if kind is float and not math.isfinite(value):
                return finish('nonfinite_project_value')
            seen_add(identity)
            if len(seen)>MAX_UNIQUE_OBJECTS:
                return finish('traversal_object_limit')
            size+=get_size(value)
            if size>limit:
                return finish('snapshot_size_limit' if limit==hard_limit else 'insufficient_available_memory',
                    singleSnapshotBytesLowerBound=size,extraEstimatedBytesLowerBound=size*EXTRA_SNAPSHOTS)
            if kind in (dict,list):
                if len(active)>=MAX_DEPTH:
                    return finish('traversal_depth_limit')
                count=len(value)*(2 if kind is dict else 1)
                if len(stack)+count+2>MAX_PENDING:return finish('traversal_pending_limit')
                active.add(identity);stack.append(identity);stack.append(exit_marker)
                if kind is dict:
                    if any(type(key) is not str for key in value):return finish('unsupported_key')
                    stack.extend(value);stack.extend(value.values())
                else:stack.extend(value)
    except (TypeError,RuntimeError,OverflowError,MemoryError):
        return finish('unmeasurable_project')
    extra=size*EXTRA_SNAPSHOTS
    required=SPARE_BYTES+extra+(extra+1)//2
    report.update(visits=visits,uniqueObjects=len(seen),singleSnapshotEstimatedBytes=size,extraEstimatedBytes=extra,
                  requiredAvailableBytes=required,availableBytes=available_bytes,
                  allowed=available_bytes>=required,
                  reason='within_snapshot_budget' if available_bytes>=required else 'insufficient_available_memory')
    return report


def adaptive_snapshot_room(project,available_bytes):
    """Whether three extra full snapshots fit the conservative bounded gate."""
    return snapshot_budget_details(project,available_bytes)['allowed']
