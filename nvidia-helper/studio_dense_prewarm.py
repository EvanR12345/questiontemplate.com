"""Optional startup overlap after the last staged visual envelope is frozen.

This never submits an image. The ordinary accepted-plan preflight remains the
only image-admission gate. Missing proof falls back to ordinary dense startup.
"""
import copy
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from studio_data import digest, get_chapter
from studio_deferred_qc import capacity_preflight, DESCRIPTOR_LIMIT
from image_provider import select_references
from prompt_preflight import require_shot


def reference_inputs(project):
    """All assets the selector can choose, including angle/style alternatives."""
    return {
        'characters': [(p['id'], p.get('references', []))
                       for p in project['characters'] if p.get('references')],
        'people': [(c['id'], p['id'], p.get('references', []))
                   for c in project['chapters'] for p in c.get('people', [])
                   if p.get('references')],
        'locations': [(p['id'], p.get('references', []))
                      for p in project.get('locations', []) if p.get('references')],
        'style': project.get('styleReferences', []),
        'manual': [(s['id'], s.get('referenceImages', []))
                   for c in project['chapters'] for sc in c['scenes'] for s in sc['shots']
                   if s.get('manual', {}).get('referenceImages')],
        'intro': [(s['id'], s.get('referenceImages', []))
                  for s in project['intro'].get('shots', [])
                  if s.get('manual', {}).get('referenceImages')],
    }


def reference_proof(project, store):
    inputs = reference_inputs(project)
    paths = set()
    def visit(value):
        if isinstance(value, dict):
            if value.get('path'): paths.add(value['path'])
            for item in value.values(): visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value: visit(item)
    visit(inputs)
    hashes = {}
    for path in sorted(paths):
        asset = store.asset(project['id'], path)
        # Selector uses these same owned assets. Check decoding as well as bytes.
        with Image.open(asset) as image: image.verify()
        hashes[path] = hashlib.sha256(asset.read_bytes()).hexdigest()
    return {'inputs': digest(inputs), 'assets': hashes}


def envelope(service, project, chapter, prepared, cast):
    """Prove a complete finite envelope; no provider/health/generation calls."""
    chapters = [c for c in project['chapters'] if c['sourceText'].strip()]
    if not chapters or chapter['id'] != chapters[-1]['id'] or not prepared:
        raise ValueError('Only a complete final staged chapter can prewarm.')
    if any(c.get('proposedPlan') for c in chapters):
        raise ValueError('An unresolved proposed plan remains.')
    if not project['settings'].get('lockImageModel'):
        raise ValueError('Future model/workflow selection is not locked.')
    if any(sc.get('manual') or any(s.get('manual') for s in sc['shots'])
           for c in chapters for sc in c['scenes']):
        raise ValueError('Manual plan preservation can change the provisional envelope.')
    provider = service.provider('comfyui')
    settings = provider.validateSettings(copy.deepcopy(project['settings']['image']), check_hardware=False)
    existing = []
    for prior in chapters[:-1]:
        if not service.saved_narration_ready(project, prior) or not service.analysis_is_current(
                project, prior, prior.get('audio', {}).get('signature')):
            raise ValueError('An earlier chapter does not have a current accepted plan.')
        existing.extend(s for sc in prior['scenes'] for s in sc['shots'])
    intro = project['intro']
    if intro['enabled']:
        if (intro.get('sourceBrief') or intro.get('visualPrompt', '').strip()) and not intro.get('shots'):
            raise ValueError('The intro visual envelope is incomplete.')
        if intro.get('voiceText', '').strip() and not service.saved_intro_audio_ready(project):
            raise ValueError('Intro narration is not ready.')
        existing.extend(intro.get('shots', []))
    for shot in existing:
        require_shot(project, shot, new_image=True)
        select_references(project, shot, service.store, provider)
        if (shot['imageProvider'] != 'comfyui' or shot['imageModel'] != settings['model']
                or shot['workflow'] != project['settings']['image']['workflow']
                or shot['generationSettings']['width'] > settings['width']
                or shot['generationSettings']['height'] > settings['height']):
            raise ValueError('A whole-run image workflow differs from the locked startup.')
        for selected in shot['characters']:
            if selected['type'] == 'main' and not any(p['id'] == selected['id'] and p.get('references')
                    for p in project['characters']):
                raise ValueError('An earlier main identity lacks canonical references.')
    known = {p['id']: p for p in project['characters'] + chapter.get('people', []) + cast}
    # The canonical library wins over a chapter's reference-free cast copy.
    names = {p['name'].casefold(): p for p in chapter.get('people', []) + project['characters']}
    maximum = 0
    provisional = []
    # Selector is read-only. Share the large frozen chapter/source structures;
    # only its cast lookup needs a private chapter dictionary per group.
    view = {**project, 'chapters': [dict(c) if c['id'] == chapter['id'] else c for c in project['chapters']]}
    for group_index, item in enumerate(prepared):
        units = item['visualTimings']
        if not units: raise ValueError('The staged narration-unit bound is missing.')
        # Valid exact coverage can have at most one shot per frozen narration
        # unit, including existing visual-change splits/coverage repairs.
        maximum += len(units)
        repairs = project['settings']['maxImageRetries']
        if type(repairs) is not int or repairs < 0 or (len(existing)+maximum)*(1+repairs) > DESCRIPTOR_LIMIT:
            raise ValueError('The frozen whole-run descriptor bound exceeds capacity.')
        mapping = {}
        for person in item['analysis']['people']:
            found = names.get(person['name'].casefold())
            if person.get('type') == 'main' and (not found or not found.get('references')):
                raise ValueError('A future main identity has no verified canonical references.')
            mapping[person['id']] = found or person
        storyboard = item['storyboard']
        get_chapter(view, chapter['id'])['people'] = list({p['id']: p for p in [*known.values(), *mapping.values()]}.values())
        if not storyboard['detail']['shots'] or len(storyboard['detail']['shots']) > len(units):
            raise ValueError('The provisional shot count exceeds its frozen coverage bound.')
        for index, shot in enumerate(storyboard['detail']['shots']):
            selected = []
            for identity in shot['characters']:
                person = mapping.get(identity) or known.get(identity)
                if person is None: raise ValueError('An unresolved provisional identity remains.')
                if person['type'] == 'main' and not names.get(person['name'].casefold(), {}).get('references'):
                    raise ValueError('A provisional main identity lacks canonical references.')
                selected.append({'id': person['id'], 'type': person['type']})
            camera = storyboard['cameras'][index]
            scene = storyboard['plan']['scenes'][shot['sceneIndex']]
            location = next((p for p in project.get('locations', [])
                             if p['name'].casefold() == scene['location'].casefold()), {})
            candidate = {'id': 'prewarm-%s-%s' % (group_index, index), 'chapterId': chapter['id'],
                'characters': selected, 'camera': camera, 'manual': {}, 'locationId': location.get('id'),
                'imageModel': settings['model'], 'generationSettings': settings}
            # Supports newly detected supporting people without manufacturing
            # a reference. New main people were rejected above.
            select_references(view, candidate, service.store, provider)
            provisional.append(candidate)
    bounded = existing + [{'generationSettings': settings} for _ in range(maximum)]
    proof = capacity_preflight(project, bounded, service.store.folder(project['id']), project['settings']['maxImageRetries'])
    result = {'version': 1, 'eligible': True, 'chapter': chapter['id'], 'maximumImages': len(bounded),
            'provisionalImages': len(existing) + len(provisional), 'capacity': proof,
            'references': reference_proof(project, service.store),
            'model': settings['model'], 'workflow': project['settings']['image']['workflow'],
            'width': settings['width'], 'height': settings['height']}
    result['signature'] = digest({'project': project['id'], 'sources': [(c['id'], c['sourceText']) for c in chapters],
        'settings': project['settings'], 'prepared': prepared, 'references': result['references'],
        'maximumImages': result['maximumImages']})
    return result


def validate_final(proof, project, store):
    shots = [s for c in project['chapters'] if c['sourceText'].strip() for sc in c['scenes'] for s in sc['shots']]
    if project['intro']['enabled']: shots += project['intro'].get('shots', [])
    if len(shots) > proof['maximumImages']:
        raise ValueError('Accepted image count escaped the prewarm envelope.')
    for shot in shots:
        settings = shot['generationSettings']
        if (shot['imageProvider'] != 'comfyui' or shot['imageModel'] != proof['model']
                or shot['workflow'] != proof['workflow'] or settings['width'] > proof['width']
                or settings['height'] > proof['height']):
            raise ValueError('Accepted image settings escaped the prewarm envelope.')
    if reference_proof(project, store) != proof['references']:
        raise ValueError('Reference selection or asset bytes changed after prewarm proof.')


class Startup:
    """One startup future; join before generation, abort/join before any stop."""
    def __init__(self, rental, gate, clock=time.monotonic, maximum_assembly_seconds=120):
        self.rental, self.gate, self.clock = rental, gate, clock
        self.abort = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='studio-dense-prewarm')
        self.future = None
        self.started = None
        self._closed = False
        self.maximum_assembly_seconds = maximum_assembly_seconds
        self.assembly_ready = False
        self.lock = threading.Lock()
        self.closed_event = threading.Event()
        self.close_error = None
        self.retired = False
        self.retiring = False
        self.original_gate = rental.gate
        self.thread_id = None
        self.close_thread_id = None
        self.assembly_event = threading.Event()
        self.timer = None
        self.timer_error = None

    def start(self):
        with self.lock:
            if self.future is not None or self._closed: return
            self.started = self.clock()
            original = self.rental.gate
            def guarded(*args, **kwargs):
                if self.abort.is_set() and threading.get_ident()!=self.close_thread_id:
                    raise RuntimeError('Dense startup aborted; no image admitted.')
                return original(*args, **{**kwargs, 'abort_event': self.abort})
            # GPUReservation constructs its single owned controller with this gate.
            self.rental.gate = guarded
            def execute():
                self.thread_id = threading.get_ident()
                return self.rental.ensure()
            self.future = self.pool.submit(execute)
            def deadline():
                # Windows Event.wait/monotonic clocks can disagree by a tick.
                # A single early wake must not silently disable the bound.
                while not self.assembly_event.is_set():
                    remaining=self.maximum_assembly_seconds-(self.clock()-self.started)
                    if remaining>=0:
                        if self.assembly_event.wait(max(.01,remaining+.001)):return
                    else:
                        try:self.check()
                        except BaseException as error:self.timer_error=error
                        return
            self.timer = threading.Thread(target=deadline, name='studio-dense-prewarm-bound', daemon=True)
            self.timer.start()

    def mark_ready(self):
        self.check()
        with self.lock:
            ready = not self.retiring and not self.retired
            if ready:
                self.assembly_ready = True
                self.assembly_event.set()
        if not ready:self.close(abort=True, retire=True)
        return ready

    def check(self):
        if self.retired:return
        if self.timer_error:raise self.timer_error
        if threading.get_ident() == self.close_thread_id:return
        # A controller's gate may re-enter CloudStoryOverlap.gate. The startup
        # thread must never join/retire its own future, or wait on a main thread
        # that is already joining it. Its sole scheduling duty is to abort.
        if threading.get_ident() == self.thread_id:
            if self.abort.is_set(): raise RuntimeError('Dense startup aborted before image admission.')
            return
        if self.retiring:
            self.close(abort=True, retire=True)
            return
        if self.future is not None and self.future.done():
            try:self.future.result()
            except BaseException:
                if self.retiring:
                    self.close(abort=True, retire=True)
                    return
                raise
        with self.lock:
            retire = (not self._closed and self.started is not None and not self.assembly_ready
                      and self.clock()-self.started > self.maximum_assembly_seconds)
            if retire:self.retiring = True
        if retire:
            # Optional scheduling cannot invalidate otherwise valid planning.
            # Retire this window first; ordinary startup later uses its carried
            # actual spend and the same cap. UNKNOWN/failed stop still blocks.
            self.close(abort=True, retire=True)

    def join(self):
        if self.retired: return
        if threading.get_ident() == self.thread_id:
            raise RuntimeError('Startup cannot wait on its own future.')
        if self.future is None: return
        while not self.future.done():
            self.gate('Waiting for the prewarmed image worker')
            self.abort.wait(.05)
        if not self.retired:self.future.result()

    def close(self, abort=False, retire=False):
        if threading.get_ident() == self.thread_id:
            raise RuntimeError('Startup must be joined by its owner before shutdown.')
        with self.lock:
            owner = not self._closed
            if owner:
                self._closed = True
                self.close_thread_id = threading.get_ident()
        if not owner:
            if threading.get_ident() == self.close_thread_id:return
            self.closed_event.wait()
            if self.close_error:raise self.close_error
            return
        if abort: self.abort.set()
        self.assembly_event.set()
        try:
            try:
                if self.future is not None:
                    try: self.future.result()
                    except BaseException:
                        if not abort: raise
            finally:self.pool.shutdown(wait=True, cancel_futures=True)
            if abort:
                self.rental.stop('Dense preparation interrupted; startup joined before shutdown')
                if self.rental.state.get('status') != 'STOPPED':
                    raise RuntimeError('Prewarmed GPU shutdown is not confirmed; reconcile its exact owner.')
                # A later ordinary initial/repair window gets the original gate,
                # never this retired window's permanently set abort event.
                self.rental.gate = self.original_gate
            self.retired = retire
        except BaseException as error:
            self.close_error = error
            raise
        finally:
            # Executor thread identifiers may be reused by later image/API
            # workers; an old abort event must never attach to that new worker.
            self.thread_id = None
            self.close_thread_id = None
            self.closed_event.set()
            if self.timer is not None and self.timer is not threading.current_thread():self.timer.join()
