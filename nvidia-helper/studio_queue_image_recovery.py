"""Recover a direct image job using its own dispatch, PNG and sidecar receipts."""
import copy
import json
from contextvars import ContextVar
from studio_data import digest as fingerprint, get_chapter, get_shot, uid
from studio_qc import decision, production_status


current_receipt=ContextVar("direct_image_receipt",default=None)


class ImageRecoveryRequired(RuntimeError):
    """Keep saved work for an explicit decision without dispatching another request."""
    def __init__(self,message,*,pause_queue=False):
        super().__init__(message)
        self.pause_queue=pause_queue


def select_image(project, shot, path, metadata, qc, stale):
    if shot.get('imagePath'):
        shot.setdefault('history', []).append({
            'imagePath':shot['imagePath'], 'metadata':shot.get('imageMetadata',{}), 'qc':shot.get('qc',{}),
        })
    shot.update(imagePath=path, imageMetadata=metadata, referenceImages=metadata['referenceImages'],
                sourceImagePath=metadata.get('sourceImagePath') or '', retryCount=metadata['attempt'],
                status='QC', qc=copy.deepcopy(qc), generationError='', generationStale=stale)
    get_chapter(project,shot['chapterId'])['renderStale']=True
    project['renderStale']=True


def claim_image(db, job):
    # Commit the initial no-dispatch state in the same transaction as the first
    # RUNNING transition. The next process can distinguish it from an old attempt.
    if job['kind'] == 'image' and not job['attempt'] and job['started'] is None:
        db.execute('INSERT OR IGNORE INTO queued_image_receipts VALUES(?,?)',
                   (job['id'], json.dumps({'state':'unsubmitted'})))


def verified_legacy_attempts(service, project, job):
    """Older jobs have no intent row. Account for every actual attempt before replay."""
    chapter = get_chapter(project, job['chapter'])
    messages = [entry.get('message', '') for entry in chapter.get('errors', [])
                if entry.get('job') == job['id']]
    if any(message.startswith('RemoteGenerationUncertain:') for message in messages):
        return False
    trace = getattr(service, 'trace', None)
    if trace is None:
        return False
    with trace.lock:
        # The worker already opened a new root for the current retry. Only the
        # previous attempts, tied to this exact durable job ID, can prove safety.
        rows = trace.db.execute("""
            SELECT root.id, child.stage, child.status, child.metadata
            FROM spans root LEFT JOIN spans child ON child.parent=root.id
                AND child.stage IN ('Image backend','Save image')
            WHERE root.project=? AND root.kind='job' AND root.stage='Queue: image'
                AND root.metadata=? AND root.id IS NOT ?
        """, (project['id'], json.dumps({'jobId':job['id']}), trace.parent.get())).fetchall()
    if len({row[0] for row in rows}) != job['attempt'] or not rows:
        return False
    # The old provider explicitly documented ImageAdmissionBlocked as "no POST".
    # Its final error can attest to a single call, never to earlier calls in a retry chain.
    blocked_before_post = (job['attempt'] == len(rows) == 1
                           and len(messages) == 1 and messages[0].startswith('ImageAdmissionBlocked:'))
    for _, stage, status, raw in rows:
        if stage != 'Image backend':
            return False  # Missing dispatch history or a potentially saved, unowned result.
        try:
            metadata = json.loads(raw)
        except (TypeError, ValueError):
            return False
        if not isinstance(metadata,dict) or metadata.get('shot') != job['shot'] or metadata.get('seed') != job['seed']:
            return False
        local = metadata.get('provider') in ('existing', 'native-flux')
        if not local and not (blocked_before_post and status == 'FAILED' and metadata.get('provider') == 'comfyui'):
            return False
    return True


def manual_selection_changed(current, submitted):
    version = current.get('imageSelectionRevision')
    previous = submitted.get('imageSelectionRevision')
    if version is not None or previous is not None:
        return version != previous
    # Older workspace selections already record a version in the shot history.
    # Ignore automatic image history and unrelated changes to the project.
    def legacy_choices(shot):
        return [(row.get('imagePath'),row.get('metadata',{}).get('imageSHA256'))
                for row in shot.get('history',[]) if row.get('reason')=='Before choosing saved image']
    return legacy_choices(current) != legacy_choices(submitted)


def selection_changed(current, submitted):
    return (current.get('imagePath', '') != submitted.get('imagePath', '')
            or manual_selection_changed(current,submitted)
            or current.get('qcOverride') != submitted.get('qcOverride'))


def restore_legacy_request_settings(service, job, options, current):
    """Separate older normalized requests from the matching saved shot settings."""
    submitted = options.get('shotSnapshot')
    if ('validatedGenerationSettings' in options or not submitted
            or submitted.get('imageProvider') != current.get('imageProvider')
            or submitted.get('imageModel') != current.get('imageModel')):
        return options
    raw = current.get('generationSettings', {})
    effective = submitted.get('generationSettings', {})
    effective_fingerprint = fingerprint(effective)
    if fingerprint(raw) == effective_fingerprint:
        return options
    requested = raw | {'model': current.get('imageModel'), 'seed': job['seed']}
    if fingerprint(requested) != effective_fingerprint:
        from image_provider import ImageProvider
        try:
            provider = service.provider(current['imageProvider'])
            # Studio's providers normalize through the shared settings method.
            # Verify that transformation without loading a model or checking VRAM.
            requested = ImageProvider.validateSettings(provider, requested, check_hardware=False)
        except (OSError, ValueError, KeyError, TypeError):
            return options
    if fingerprint(requested) != effective_fingerprint:
        return options
    return options | {'shotSnapshot': submitted | {'generationSettings': raw},
                      'validatedGenerationSettings': effective}


class QueuedImageReceipt:
    def __init__(self,service,job):
        self.service=service
        self.job=job

    def dispatch(self,provider,attempt,spec,previous_path,previous_sha,override,qc,selection_revision=None):
        job=self.job
        path=f"{job['chapter']}/{job['shot']}-{uid()}-a{attempt}.png"
        record={'path':path, 'spec':spec, 'previous':previous_path, 'previousSHA':previous_sha,
                'remote':provider=='comfyui', 'previousReview':fingerprint(override),
                'selectionRevision':selection_revision, 'qc':qc}
        self.dispatched = record
        with self.service.cv:
            self.service.db.execute('INSERT OR REPLACE INTO queued_image_receipts VALUES(?,?)',
                                    (job['id'],json.dumps(record)))
            self.service.db.commit()
        return path

    def confirm_retry_safe(self, *, commit=True):
        with self.service.cv:
            # A verified admission rejection or terminal backend error allows
            # another attempt. An accepted result and an uncertain call never do.
            record = self.dispatched | {'retrySafe':True}
            self.service.db.execute('UPDATE queued_image_receipts SET receipt=? WHERE job=? AND receipt=?',
                                    (json.dumps(record),self.job['id'],json.dumps(self.dispatched)))
            if commit:self.service.db.commit()

    def matches_spec(self, project, shot, options, expected):
        signature = self.service.visual_spec_signature
        current = signature(project, shot)
        if current == expected:
            return True
        submitted = options.get('shotSnapshot')
        if 'validatedGenerationSettings' in options or not submitted or signature(project, submitted) != expected:
            return False
        compatible = restore_legacy_request_settings(self.service, self.job, options, shot)
        return compatible is not options and signature(project, compatible['shotSnapshot']) == current

    def recover(self, project, options):
        service=self.service
        job=self.job
        with service.cv:
            row=service.db.execute('SELECT receipt FROM queued_image_receipts WHERE job=?',(job['id'],)).fetchone()
        if row is None:
            if job.get('attempt') or job.get('started') is not None:
                # Retry, Retry missing, Cancel and Resume replace the display
                # message. Only durable job identity and execution evidence count.
                if not verified_legacy_attempts(service, project, job):
                    raise ImageRecoveryRequired('This earlier image attempt has no verified output receipt. Check its saved files and any remote work before starting a new image.',
                                                pause_queue=True)
                submitted = options.get('shotSnapshot')
                shot = get_shot(project,job['chapter'],job['shot'])
                if not submitted or selection_changed(shot, submitted):
                    raise ImageRecoveryRequired('The earlier image has no verified selection receipt. The current selection is retained.')
                path = shot.get('imagePath')
                expected_sha = submitted.get('imageMetadata',{}).get('imageSHA256')
                if path and (not expected_sha or service.store.asset_digest(project['id'],path) != expected_sha):
                    raise ImageRecoveryRequired('The earlier selected image cannot be verified. The current selection is retained.')
            return False
        try:
            receipt=json.loads(row[0])
            if receipt == {'state':'unsubmitted'}:
                return False
            required={'path','spec','previous','previousSHA','previousReview','remote','qc'}
            if not isinstance(receipt,dict) or not required.issubset(receipt):
                raise ValueError('Incomplete dispatch receipt')
        except (ValueError,TypeError) as error:
            raise ImageRecoveryRequired('The interrupted job receipt is unreadable. Inspect the saved files and any remote work before continuing.',
                                        pause_queue=True) from error
        pid=job['project']
        submitted = options.get('shotSnapshot') or {}
        def choice_changed(shot):
            return (shot.get('imageSelectionRevision')!=receipt.get('selectionRevision')
                    or manual_selection_changed(shot,submitted))
        path=service.store.asset_local(pid,receipt['path'])
        sidecar=path.with_suffix('.json')
        sidecar_name=sidecar.relative_to(service.store.folder(pid)).as_posix()
        if not sidecar.is_file() and service.store.has_asset(pid,sidecar_name):
            sidecar=service.store.asset(pid,sidecar_name)
        if not sidecar.is_file():
            if receipt['remote'] and not receipt.get('retrySafe'):
                raise ImageRecoveryRequired('The interrupted image request has no verified result. Reconcile the remote operation before starting another image.',pause_queue=True)
            shot=get_shot(project,job['chapter'],job['shot'])
            selected=shot.get('imagePath','')
            if (selected!=receipt['previous'] or
                (service.store.asset_digest(pid,selected) if selected else None)!=receipt['previousSHA'] or
                fingerprint(shot.get('qcOverride'))!=receipt['previousReview'] or
                choice_changed(shot) or
                not self.matches_spec(project,shot,options,receipt['spec'])):
                raise ImageRecoveryRequired('The shot or its selected image changed after interruption. The current selection is retained.')
            return False
        try:
            metadata=json.loads(sidecar.read_text(encoding='utf-8'))
        except (OSError,ValueError) as error:
            raise ImageRecoveryRequired('The saved image receipt is unreadable. Inspect the saved files before starting another image.',pause_queue=receipt['remote']) from error
        mismatch = 'The saved image and its job receipt do not match. Inspect the saved files before starting another image.'
        if (not isinstance(metadata, dict) or not {'referenceImages','attempt','requestedModel'}.issubset(metadata)
                or metadata.get('queueJobId') != job['id'] or metadata.get('seed') != job['seed']):
            raise ImageRecoveryRequired(mismatch, pause_queue=receipt['remote'])

        def restore(latest):
            # Verify the actual candidate inside the same store transaction that adopts it.
            digest = service.store.asset_digest(pid, receipt['path'])
            if not digest or metadata.get('imageSHA256') != digest:
                raise ImageRecoveryRequired(mismatch, pause_queue=receipt['remote'])
            shot = get_shot(latest, job['chapter'], job['shot'])
            selected = shot.get('imagePath', '')
            selected_sha = digest if selected == receipt['path'] else (
                service.store.asset_digest(pid, selected) if selected else None)
            current_metadata=shot.get('imageMetadata',{})
            selected_result=(selected==receipt['path'] and selected_sha==digest and
                             current_metadata.get('queueJobId')==job['id'] and current_metadata.get('imageSHA256')==digest)
            already_in_history=any(x.get('imagePath')==receipt['path'] for x in shot.get('history',[]))
            previous_selection = (selected == receipt['previous'] and selected_sha == receipt['previousSHA']
                                  and fingerprint(shot.get('qcOverride')) == receipt['previousReview']
                                  and not choice_changed(shot)
                                  and not already_in_history)
            if not (selected_result or previous_selection) or not self.matches_spec(latest,shot,options,receipt['spec']):
                if selected_result:shot['generationStale']=True
                if selected!=receipt['path'] and not already_in_history:
                    shot.setdefault('history',[]).append({'imagePath':receipt['path'],'metadata':metadata,'selected':False,
                        'reason':'Earlier interrupted generation; current selection or shot has changed.'})
                return 'The shot or its selected image changed. The current selection and earlier output are retained.'
            if not selected_result:
                select_image(latest,shot,receipt['path'],metadata,receipt['qc'],shot['imageModel']!=metadata['requestedModel'])
            if not any(entry.get('queueJobId')==job['id'] and entry.get('imageSHA256')==digest for entry in shot.get('retryHistory',[])):
                shot['retryHistory']=[entry['metadata'] for entry in shot.get('history',[])
                                      if entry.get('metadata',{}).get('queueJobId')==job['id']]+[metadata]
            assessment=decision(latest,shot,service.store)
            shot['qcDecision']=assessment
            shot['status']=production_status(latest,shot,service.store)
            chapter=get_chapter(latest,job['chapter'])
            chapter['status']='COMPLETE' if all(service.saved_image_ready(latest,s)
                for scene in chapter['scenes'] for s in scene['shots']) else 'READY_FOR_IMAGES'
            if assessment['blocking'] and not (options.get('deferQC') and assessment['disposition']=='pending'):
                return 'The recovered image is saved. Review or run Visual QC on the selected image to continue.'
            return ''
        message=service.store.mutate(pid,restore)
        if message:raise ImageRecoveryRequired(message)
        return True


def run_queued_image(service,project,job,options):
    receipt=QueuedImageReceipt(service,job)
    if receipt.recover(project,options):
        return True
    current = get_shot(project, job['chapter'], job['shot'])
    submitted = options.get('shotSnapshot') or current
    if selection_changed(current, submitted):
        raise ImageRecoveryRequired('The selected image or its acceptance changed after queuing. Review the current selection before generating another image.')
    options = restore_legacy_request_settings(service, job, options, current)
    submitted = options.get('shotSnapshot') or current
    if submitted is not current and service.visual_spec_signature(project,submitted)!=service.visual_spec_signature(project,current):
        raise ImageRecoveryRequired('The shot was edited after queuing. Review the current settings before generating another image.')
    project=service.begin_queued_image_rental(project,job,options) or project
    token=current_receipt.set(receipt)
    try:
        result = service.generate(project,job['chapter'],job['shot'],job['seed'],options)
        if isinstance(result,dict) and result.get('status') == 'SUPERSEDED':
            raise ImageRecoveryRequired('The selected image or shot changed during generation. The current selection and earlier output are retained.')
    finally:
        current_receipt.reset(token)
    return False
