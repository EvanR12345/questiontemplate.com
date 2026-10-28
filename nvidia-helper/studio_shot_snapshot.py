"""Frozen image-task inputs omit only already-durable timing telemetry."""
import copy


def shot_snapshot_view(project):
    """Read-only shallow projection for snapshot sizing; never mutate this view.

    Story, chapter, cast, references, QC, settings, rental identity and budget
    values are identical. Timing writers always operate on the fresh store.
    """
    view=dict(project)
    production=project.get('production')
    if isinstance(production,dict) and 'timings' in production:
        view['production']={key:value for key,value in production.items() if key!='timings'}
    return view


def shot_snapshot(project):
    """Independent full-project copy except production.timings; no live writes."""
    return copy.deepcopy(shot_snapshot_view(project))
