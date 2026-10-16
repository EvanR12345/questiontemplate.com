// Presentation only: no project mutations, network calls, or generation jobs.
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const WORKSPACES = {
  write: ['Story', 'Paste one chapter at a time. Add the remaining chapters, then use Generate full video for the complete workflow.', ['Chapter names are metadata. They are excluded from narration unless you explicitly enable them.', 'Analyze chapter prepares narration and a scene plan for this chapter. It can use paid directing, but does not generate the chapter’s images.', 'New chapter keeps this project’s characters, references and settings. Chapter actions duplicate, reorder or delete a chapter.']],
  narration: ['Narration', 'Review the exact spoken script, listen to saved audio, or regenerate just this chapter’s voice.', ['Automatic uses cleaned story text; editing the script switches it to manual.', 'Clean from story again replaces the edited narration script with a newly cleaned version.', 'Regenerate narration creates one continuous chapter recording. Sentence timing appears after audio is ready.']],
  cast: ['Characters', 'Keep recurring identities and their reference images here. Scene clothing and injuries can change with the story.', ['Promote only recurring people to the main library. Supporting and temporary people can stay in the chapter.', 'Attach identity references to the correct person. The selected image workflow must support reference conditioning.', 'Locations, continuity and story memory are editable below the character list. Advanced JSON edits change structured story facts.']],
  review: ['Layout', 'Inspect the director’s scenes and shots, edit a prompt or image, and resolve visual findings.', ['A scene is a story event; its shots are the individual images shown during narration.', 'Regenerate one shot to change that image without remaking the entire chapter. It can use paid image compute.', 'Visual checks judge story/identity problems. Technical checks only validate files. Accepting an image keeps its recorded AI findings.']],
  timeline: ['Video', 'See when images appear, adjust their timing and motion, and assemble already prepared assets into a video.', ['Render chapter assembles this chapter. Render full story assembles chapters in their saved order with the configured intro.', 'Rendering uses prepared images and audio. Generate full video also creates missing narration, plans and images.', 'After editing a shot, the old video remains available until you render an updated version. Unchanged compatible chapter renders can be reused.']],
  settings: ['Settings', 'Choose the look, voice, checks and output for this project. Open only the sections you need, then save changes.', ['Search finds a setting by its label or explanation. Hidden sections still retain their values.', 'Most changes apply to future generation or the next render; existing images and recordings are not automatically replaced.', 'Setup buttons configure providers or the shared helper. They can apply separately from Save project settings.']],
  files: ['Files', 'Find saved assets, check cloud copies, split finished videos, make thumbnails and upload from cloud.', ['Cloud status distinguishes files on the helper from verified cloud copies. Saving a project does not automatically remove local files.', 'Splitting reuses a saved full video. Thumbnail labels can show one part number or a range.', 'YouTube and Vimeo uploads require their own connection and an explicit upload action. Vimeo can host a video linked from Patreon; this does not create a Patreon post.']],
  audio: ['Audio', 'Paste a script, choose a model and voice, preview, then generate a downloadable recording.', ['Each voice model has its own voice list and supported controls. Switching models does not make their voices equivalent.', 'Kokoro can use the local helper. Kaggle voices require an account, quota and notebook startup time.', 'Device preview uses your browser’s system voice. It does not sound like the selected studio model or produce a downloadable recording.']],
  planner: ['Production planner', 'Compare estimated schedules and GPU options, then experiment with parallel tasks without starting a rental.', ['My proposed schedule estimates elapsed production time. Where time goes shows task work; overlapping work must not be added as elapsed time.', 'Drag a task to change its proposed start. Dependencies, API slots and GPU capacity still constrain when it can run.', 'The planner is a simulation. Exporting or changing a schedule does not deploy it or start generation. Read evidence and coverage before relying on a forecast.']],
};

// Titles are the real interface labels, not model-generated documentation.
export const FEATURES = [
  ['Start & save', 'Project health', 'Run the same free readiness checks as Generate full video: voices, director, model configuration, saved caps, disk, archive and selected character references. Passing setup checks does not guarantee live GPU stock or enough disk for a full production.', 'write'],
  ['Start & save', 'Edit history / Undo', 'Undo recorded manual edits without deleting generated files. Conflicting newer work is protected. Affected exports become stale and illustrations require review. Older generations stay in their existing image and audio histories.', 'write'],
  ['Start & save', 'Search everything', 'Find saved dialogue, chapters, characters, scenes, shots and filenames across this project. File search needs the helper connection; private file links are created only when requested.', 'write'],
  ['Video', 'Render short edit preview', 'Queue a local preview up to 30 seconds around the selected shot, using saved pictures, motion, narration and watermark. No new images or directing. This excludes intro, outro and reminders and does not replace the saved chapter export.', 'timeline'],
  ['Video', 'Completion report', 'View and download retained stage timings, API/GPU cost estimates, recorded repairs and unresolved findings. Overlapping work is separate from calendar time. Missing costs stay unknown; this is not the account invoice.', 'timeline'],
  ['Start & save', 'What’s next? and production overview', 'Shows the next useful workspace, exported chapter count, active task, shared-queue ETA coverage, recorded API/GPU estimates and cloud-save state. Unknown costs and timing remain labeled unknown.', 'write'],
  ['Queue & recovery', 'Problems inbox', 'Links current blocking image findings, timing or continuity warnings and latest failed chapter jobs directly to their chapter or shot. Old failed attempts do not automatically remain current problems.', 'review'],
  ['Start & save', 'Chapter search, ranges and selected work', 'Search chapter names or story text, filter rendered/unfinished chapters, select a number range and queue analysis, narration or individual chapter exports in saved order. Selection does not limit Generate full video.', 'write'],
  ['Scenes & images', 'Compare saved image versions', 'Compares the current illustration with earlier saved outputs. Choosing an earlier version retains both files, clears acceptance for the old image and requires review against current story requirements.', 'review'],
  ['Story & voice', 'Compare saved recordings', 'Listen to current and previous chapter recordings. Only an earlier recording of the current narration script can be selected. Changed duration scales visual boundaries and adds a timing-review warning.', 'narration'],
  ['Start & save', 'My saved setups', 'Save a named look, voice, image workflow, visual checks and finishing preferences. Load it from this or another project. New projects inherit the setup library; spending caps, credentials, story facts and reference photos are excluded.', 'settings'],
  ['Video & finishing', 'Make a small edit', 'Upload a replacement shot picture, compare saved versions, jump to the narration script, remove subscribe reminders/ding or preview one shot in the saved chapter video. Rerender affected exports to show edits; the preview plays the previous export until then.', 'timeline'],
  ['Cloud & publishing', 'File location badges', 'Shows cloud-only, cloud plus helper, helper-only, uploading/pending and missing files. Cloud identity comes from the verified archive manifest; it is not a new remote checksum check or permission to delete a local original.', 'files'],
  ['Start & save', 'Before you continue', 'Explains the affected scope and which assets are reused before manual chapter analysis, narration, image regeneration or rendering. Time and cost are not fabricated when no compatible estimate is available. Automatic full-video production retains its readiness step.', 'write'],
  ['Start & save', 'Generate full video', 'Runs narration, directing, images, checks/allowed repairs, rendering and cloud saving for the project. Saved spending caps still apply. Publishing is a separate action.', 'write'],
  ['Start & save', 'Check readiness', 'Checks the current setup and reports missing requirements before production. It does not generate a video or guarantee that a future cloud GPU will be in stock.', 'write'],
  ['Start & save', 'Generation strategy', 'Timed align delays image rental to reduce paid waiting, using matching history and a 15-second buffer. Keep fastest video favors earlier image work. The legacy mode follows chapters in order.', 'write'],
  ['Start & save', 'Overlap cloud tasks', 'Allows eligible independent cloud stages to run together. Timed strategies manage this automatically; the checkbox is editable only in the legacy strategy when its requirements are met.', 'write'],
  ['Start & save', 'Timing history and estimate coverage', 'Shows compatible real observations and gaps in the forecast. A new model, voice or workflow can lack matching timing history.', 'write'],
  ['Start & save', 'Project actions', 'Create or switch projects, reconnect the helper, or import/export project JSON. JSON is a plan/settings backup; media remains in its helper or cloud archive.', 'write'],
  ['Start & save', 'Sync offline edits', 'Sends this browser’s offline project to the helper after confirmation and backup. Review which project/version is open before syncing.', 'write'],
  ['Start & save', 'Save project settings', 'Saves this project’s current settings. Unsaved form edits are retained in this open page, but should be saved before closing it.', 'settings'],
  ['Story & voice', 'Analyze chapter', 'Prepares cleaned narration, audio and the director’s editable scene/shot plan for the selected chapter. Review the plan before buying its images, or let a full automatic run continue.', 'write'],
  ['Story & voice', 'Chapter actions', 'Duplicate a chapter, move it earlier/later, or delete it. Changing earlier story facts can affect continuity in later chapters.', 'write'],
  ['Story & voice', 'Narration source', 'Automatic cleans the story before speech. Manual uses your edited narration script. Chapter headings are not spoken unless you enable them.', 'narration'],
  ['Story & voice', 'Regenerate narration audio', 'Recreates only the current chapter’s continuous voice recording from its narration script and voice settings. Recheck timing and rerender affected video afterward.', 'narration'],
  ['Story & voice', 'Voice models and previews', 'Pick a model, then a voice from that model’s own cards. Preview before a long run. Instructions and voice-cloning support vary by model.', 'settings', 'settings-voice'],
  ['Story & voice', 'Kaggle voice setup', 'Connects private voice notebooks. Kaggle jobs can queue, start slowly or hit quota; it is not an always-on speech API.', 'settings', 'settings-voice'],
  ['People & continuity', 'Main character library', 'Stores recurring identity traits and reference images. Clothing, injuries and temporary appearance are tracked separately per scene.', 'cast'],
  ['People & continuity', 'Detected people', 'Chapter-level supporting, temporary and background people. Accept, rename, merge, attach an existing identity or promote a recurring person.', 'cast'],
  ['People & continuity', 'Locations and reference images', 'Save descriptions and relevant visual references for recurring places. Compatible image workflows can use those references.', 'cast'],
  ['People & continuity', 'Continuity state and story memory', 'Structured facts carried between scenes and chapters: objects, injuries, relationships, locations and appearance changes. These are story facts, not chat memory.', 'cast'],
  ['Scenes & images', 'Scene and shot editing', 'Change characters, narration boundaries, actions, camera, prompts, references, model settings or motion. Manual choices stay editable and saved.', 'review'],
  ['Scenes & images', 'Regenerate images', 'Generates the selected shot/scene using its saved parameters. Older outputs and generation metadata remain available. A different model needs compatible settings.', 'review'],
  ['Scenes & images', 'Visual checks: Off / Sampled / Practical / Strict', 'Off skips visual review; Sampled reviews the first shots plus a stable selection; Practical reviews every new image and treats minor differences as warnings; Strict uses detailed review. Paid API checks take time.', 'settings', 'settings-checks'],
  ['Scenes & images', 'Automatic repairs', 'Repairs confirmed major problems within saved API/GPU caps and retry limits. Minor or uncertain Practical findings remain warnings. Unsupported or exhausted repairs stop safely.', 'settings', 'settings-checks'],
  ['Scenes & images', 'Accept image', 'Keeps an image despite its saved findings. This is a user decision, not proof that an AI check passed. Later edits can require a fresh review.', 'review'],
  ['Scenes & images', 'Image model, workflow and references', 'The model draws images; the workflow supplies references and generation settings. Luna writes visual direction but does not draw the pictures.', 'settings', 'settings-images'],
  ['Scenes & images', 'Economy storyboard canvases', 'Legacy Qwen multi-panel workflow: four crops from one canvas, with lower per-shot detail and possible mixing. It is not the recommended individual-image Klein workflow.', 'settings', 'settings-economy'],
  ['Video & finishing', 'Render chapter / Render full story', 'Assembles saved narration, timed images, motion, transitions and enabled overlays. Compatible unchanged chapter renders can be reused.', 'timeline'],
  ['Video & finishing', 'Validate timing', 'Checks shot/scene timing against narration. It does not judge whether an image depicts the story correctly.', 'timeline'],
  ['Video & finishing', 'Video output and renderer', 'Resolution, frame rate and motion control export appearance. The local renderer chooses CPU or NVIDIA processing on this computer; cloud rendering is separate in Files.', 'settings', 'settings-video'],
  ['Video & finishing', 'Optional intro', 'Separate opening visuals and optional explicit voice text. Off starts with Chapter 1. Full story only plays it once; every chapter repeats it.', 'settings', 'settings-intro'],
  ['Video & finishing', 'Reminders, ding and outro', 'Configure occasional subscribe popups, their sound and a spoken ending/teaser. Export-version settings control which are included for YouTube or Patreon.', 'settings', 'settings-engagement'],
  ['Video & finishing', 'Watermark', 'Overlay text or a transparent logo at a chosen position and opacity. Apply changes by rendering a new video; existing exports remain saved.', 'settings', 'settings-watermark'],
  ['Cloud & publishing', 'Save project to cloud / Save all helper projects', 'Archives project data and registered media from the helper. Check sync completion before treating the cloud copy as safe. This does not delete local originals.', 'files'],
  ['Cloud & publishing', 'Refresh files, search and category', 'Refreshes the asset list. Filter by filename or media type to find narration, images, videos and thumbnails. Open uses the available helper/cloud copy.', 'files'],
  ['Cloud & publishing', 'Render in cloud', 'Plans working space and rents compatible compute to render saved assets into Google storage. Storage itself does not render. The current marketplace preview has a 120-second gate; long exports remain unvalidated.', 'files'],
  ['Cloud & publishing', 'Split saved full video', 'Creates parts at time intervals or chapter boundaries from an existing full video. It does not regenerate the story or its images.', 'files'],
  ['Cloud & publishing', 'Thumbnail maker', 'Choose artwork, a title and part label such as 1 or 1–6, then create a thumbnail. Thumbnail creation is separate from video generation and upload.', 'files'],
  ['Cloud & publishing', 'YouTube cloud upload', 'Uploads a selected cloud video directly through the private publisher after channel consent. Set title, description, privacy and audience, then explicitly start the upload.', 'files'],
  ['Cloud & publishing', 'Vimeo → Patreon', 'Uploads a cloud video to your connected Vimeo account. Use the resulting link in a Patreon post. Vimeo access/plan permissions are required; Studio does not create the Patreon post.', 'files'],
  ['Queue & recovery', 'Pause / Resume', 'Controls the shared helper queue. Completed assets are retained. A provider may need to finish or safely interrupt its current operation before pausing.', 'write'],
  ['Queue & recovery', 'Cancel current / Cancel full run', 'Stops the current job or the current automatic run. Saved completed outputs remain available. A full-run cancellation prevents its later stages from starting.', 'write'],
  ['Queue & recovery', 'Cancel project queue', 'Cancels current/waiting work belonging to this project. Other projects and completed media stay saved.', 'write'],
  ['Queue & recovery', 'Retry missing images / Retry failed job', 'Queues missing images or retries a failed attempt. Reuse depends on matching inputs; failed-attempt history remains visible.', 'review'],
  ['Queue & recovery', 'Job priority and history', 'Move waiting work earlier and inspect completed/failed attempts. Attempt counts are not counts of unique successfully generated images.', 'write'],
  ['Queue & recovery', 'Recover interrupted rental', 'Reconciles the helper’s saved managed-rental state after an interruption. Provider charges and remote requests must be checked before restarting work.', 'settings', 'settings-advanced'],
  ['Advanced & planner', 'Director workflow, reasoning and API tasks', 'Control how Luna groups and reviews plans, reasoning effort, and independent parallel API slots. More slots only speed independent work; continuity and rate limits still constrain it.', 'settings', 'settings-advanced'],
  ['Advanced & planner', 'Spending limits', 'API and GPU limits are separate. They bound automatic direction, checks and image compute; they are not prepaid credits or a guarantee that a project will finish inside the cap.', 'settings', 'settings-cost'],
  ['Advanced & planner', 'GPU providers and automatic startup', 'Configure Runpod, Lium and Vast for available RTX 5090 workers and saved rental limits. Saving provider configuration alone does not rent compute.', 'settings', 'settings-advanced'],
  ['Advanced & planner', 'Production planner: GPUs and prices', 'Compare hourly rates, measured throughput, model copies, setup and rental time. Prices/stock can change; measured samples and projections are labeled separately.', 'planner'],
  ['Advanced & planner', 'Production planner: drag and overlap', 'Move proposed tasks and inspect their dependencies/resource lanes. Dragging cannot make dependent tasks independent or create extra GPU/API capacity.', 'planner'],
  ['Advanced & planner', 'Production planner: work versus elapsed', 'Task work totals can exceed video production time when stages overlap. Startup is a fixed overhead, so cost per video minute usually falls for longer runs.', 'planner'],
  ['Advanced & planner', 'Production planner: import/export and evidence', 'Save a proposed schedule, inspect earlier observations and review measurement coverage. A planner file is not an executed production job.', 'planner'],
  ['Advanced & planner', 'Audio page: format and bitrate', 'Choose downloadable audio formats. Bitrate changes compressed file size and encoding quality; it does not change the speech model’s pronunciation or expressiveness.', 'audio'],
  ['Audio', 'Script, import and examples', 'Type or import a plain-text script. Try an example replaces the script with an audition sample; Clear empties it. Review the text before generating speech.', 'audio'],
  ['Audio', 'Model and voice cards', 'Choose the speech model, then one of its own voice cards. The recommendations are starting suggestions, not measured quality rankings. Some models require private Kaggle notebooks.', 'audio'],
  ['Audio', 'Preview voice versus Generate audio', 'Preview auditions the selected voice on a short script. Generate audio creates a recording with downloadable completed sections. Kaggle auditions are bounded; use Studio for full chapters.', 'audio'],
  ['Audio', 'Speed and volume', 'Speed changes speaking pace. Volume changes playback loudness. Preview the result before a long recording; faster speech is not necessarily more natural.', 'audio'],
  ['Audio', 'Acceleration and Connect NVIDIA', 'Choose automatic browser processing, browser GPU, CPU, or the existing local NVIDIA helper. Connect NVIDIA pairs the helper; it does not install a new Torch environment.', 'audio'],
  ['Audio', 'Pronunciation fixes', 'Enter one word = spoken spelling correction per line to guide unfamiliar names or words. Test the correction on a short preview.', 'audio'],
  ['Audio', 'Instant device preview', 'Uses the device’s system voice and pitch control. Play, pause, resume and stop affect this preview only. It does not audition the selected model or create a downloadable file.', 'audio'],
  ['Audio', 'Pause generation, cancel and resume remaining text', 'Pause/resume controls recording work when supported. Cancel retains completed downloadable sections. Resume remaining text continues from the saved recording state rather than starting over.', 'audio'],
  ['Audio', 'Restore recording and recent scripts', 'Restore retrieves a saved recording session where available. Recent scripts are saved on this browser/device; clearing that history does not remove Studio cloud projects.', 'audio'],
  ['Audio', 'Standalone sound effects', 'Ding, pop, laser, whoosh, buzz and win buttons play separate previews. They are not mixed into the generated narration.', 'audio'],
  ['Planner controls', 'Follow saved Studio settings', 'Imports the selected project’s saved settings and compatible timing history from the shared helper. Unsaved Studio form edits are not imported. Refresh saved setup reads again without changing the project.', 'planner'],
  ['Planner controls', 'Final video length, chapters and image cadence', 'Video minutes and chapter count size the forecast. Images / video min determines how frequently pictures change. Images / min continuous is measured warm production throughput, excluding setup and waiting.', 'planner'],
  ['Planner controls', 'GPU, resolution and conditioning profiles', 'Select a measured hardware/output profile. Fresh encoding includes new prompt/reference work; reused conditioning is a control measurement. Unsupported combinations must not be treated as measured speed.', 'planner'],
  ['Planner controls', 'Independent workers, pipeline and request slots', 'Independent workers use separate resident model copies. Pipelined requests overlap preparation/delivery around one copy. Hybrid combines them. Choose only completed tested configurations for calibrated estimates.', 'planner'],
  ['Planner controls', 'Scheduling mode and shared API slots', 'Compare proposed streaming overlap, installed behavior or a sequential baseline. API slots constrain independent Luna work; measured director profiles already include their tested concurrency once.', 'planner'],
  ['Planner controls', 'GPU readiness, cache and startup timing', 'Ready chapters and earliest boot delay control when rental can start. Start immediately favors speed. Existing model cache removes only applicable reload/download overhead, not image inference.', 'planner'],
  ['Planner controls', 'Retries and timing sensitivity', 'Extra attempts reserve estimated image work. Timing sensitivity is an assumed range, not a statistical confidence interval or observed failure rate.', 'planner'],
  ['Planner controls', 'Price basis, quotes and storage allocation', 'Choose published listings, historical receipts or your own compute quote. Running disk and allocated retained storage are separate. The forecast lists exclusions and does not spend money or resize a volume.', 'planner'],
  ['Planner controls', 'Repack earliest and Undo', 'Repack schedules tasks at their earliest feasible dependency/resource slots. Undo restores a previous schedule edit. Neither action changes production code or starts compute.', 'planner'],
  ['Planner controls', 'Play simulation, focus chapter and zoom', 'Play shows which planned tasks overlap over simulated time. Chapter focus and zoom only change the timeline view. Group preparation compresses the display without deleting those tasks or costs.', 'planner'],
  ['Planner controls', 'Task inspector and pair overlap test', 'Select a task to see its purpose, dependencies and resource needs. The pair test explains whether two types of work can overlap under the modeled rules; it does not benchmark a GPU.', 'planner'],
  ['Planner controls', 'GPU comparison and evidence', 'Filter/sort tested profiles and compare complete schedules. Evidence distinguishes measured runs, historical prices and untested assumptions. GPU availability changes and is not guaranteed by the chart.', 'planner'],
  ['Advanced & planner', 'Comic & panel tools', 'The earlier comic workspace supports panels, dialogue bubbles and comic exports. It is separate from the chapter-to-video controls and remains available through the mode switch.', 'write'],
];

export const FIELD_HELP = {
  settingProjectName:'The project’s label in the project picker and cloud archive. It is not automatically spoken.',
  settingStyle:'The visual style supplied to new image plans. Existing pictures keep their current appearance until regenerated.',
  settingLayout:'Pacing and composition guidance for the director. AUTO lets the story determine the scene structure.',
  settingGenerationMode:'Changes planning/check intensity. Explicit visual-check settings still apply; QUICK does not change the selected image model by itself.',
  settingImagePreset:'Suggested image-workflow settings. Compatibility depends on the selected provider/model.',
  settingCheckLevel:'How many images receive paid visual review and how detailed it is. File integrity checks are separate.',
  settingSampleEvery:'Used only for Sampled checks: approximately one in this many images, plus the required first shots.',
  settingQCPolicy:'For Sampled checks, choose whether minor findings are advisory or detailed review is required. Practical/Strict levels set this themselves.',
  settingRepair:'Allow targeted regeneration/editing for major visual problems. Automatic full-video completion manages this within the saved limits.',
  settingFocusedPrompts:'Use concise, explicit people/action/prop facts in new prompts. Does not truncate prompts or overwrite manual ones.',
  settingImageProvider:'The backend that runs the image workflow. ComfyUI can connect to the managed cloud GPU.',
  settingImageModel:'The weights that draw the image. A model must be installed/available with a compatible workflow.',
  settingImageWorkflow:'The recipe for generation, references or editing. A model name alone does not supply those capabilities.',
  settingReferenceStrength:'How strongly the SD fallback follows its reference. Native Klein uses its own conditioning; this control does not tune it.',
  settingEconomyPanels:'Legacy Qwen canvas mode: four cropped shots per generation. Lower detail and mixed compositions are possible.',
  settingVoiceEngine:'Choose the speech model first; its available voices appear below. New models run through Kaggle.',
  settingKokoroKaggle:'Use a Kaggle notebook for Kokoro instead of the laptop. Other new voice models already require Kaggle.',
  settingVoiceBatchSize:'Share Kaggle startup across this many chapters, within the 4,000-word bound. Each still gets its own WAV; the first result may arrive later.',
  settingVoiceInstruction:'Qwen3-TTS delivery instructions, separate from spoken text. Other engines do not necessarily support them.',
  settingFishLicense:'Only enable if you actually hold the required separate commercial license for Fish Audio.',
  settingVoice:'The selected Kokoro narrator. Changing it affects new recordings, not saved audio.',
  settingModelVoice:'The selected model’s own voice ID/preset. It is not interchangeable with another model’s voice names.',
  settingSpeed:'Narration speed multiplier. Higher is faster and shortens the recording; preview naturalness before long runs.',
  settingNarrationDelivery:'Delivery/post-processing profile. Model support and the resulting sound vary; compare a short preview.',
  settingSoundEffects:'How supported story sound-effect tokens are handled in the narration. Preview exclamations and impacts before a long recording.',
  settingEmphasisPhrases:'Phrases to emphasize subtly when the narration system supports it. Keep them short and story-specific.',
  voicePreviewText:'Exact sample spoken by the preview. Delivery instructions belong in their separate field.',
  introEnabled:'Add a separate opening. Off starts directly with the first chapter.',
  introDuration:'Intro length/maximum in seconds. End with narration can shorten it, with a 10-second minimum.',
  introPlacement:'Play once before the full story, or repeat before every chapter.',
  introEndOnNarration:'Finish soon after the intro voice to avoid a silent gap, bounded by the selected duration and minimum.',
  introShowTitle:'Render visible title text in the intro. It does not automatically narrate the title.',
  introTitle:'Optional visible title, used only when Show title text is enabled.',
  introSubtitle:'Optional additional visible intro text.',
  introVisualPrompt:'Describe the opening visual. Keep its action and character details grounded in the story.',
  introVoiceText:'Only this explicit text is spoken in the intro. It is separate from every chapter’s narration.',
  settingWatermarkEnabled:'Add the chosen text/logo during rendering. Changing it requires a new export.',
  settingWatermarkType:'Choose rendered text or an uploaded image/logo.',
  settingWatermarkText:'The visible watermark text, not narration.',
  settingWatermarkPosition:'Where the overlay sits in the video frame.',
  settingWatermarkWidth:'Approximate watermark width as a percentage of the output frame.',
  settingWatermarkOpacity:'Lower values make the watermark more transparent.',
  settingWatermarkMargin:'Distance from the frame edge as a percentage of its size.',
  settingWatermarkColor:'Text color. Uploaded logos keep their own colors.',
  settingUnattended:'Continue the complete run automatically, including allowed major-error repairs. Budget or unrecoverable failures still stop safely.',
  settingAPIBudget:'USD cap for this project’s directing, planning and visual-check API work. Separate from GPU rental.',
  settingGPUBudget:'USD cap for managed image GPU rental. Cloud rendering can have its own allowance in Files.',
  settingVideoSize:'Final exported frame dimensions. 1280 × 720 is 720p; changing it does not regenerate higher-detail images.',
  settingImageFit:'Fill frame crops edges to avoid borders. Preserve composition can leave unused space when aspect ratios differ.',
  settingZoomAmount:'Maximum zoom/pan movement scale. Larger amounts crop more of the original image.',
  settingMotionMode:'Gentle uses restrained movement, Director follows each shot, and Static disables image motion.',
  settingVideoFps:'Frames per second in the export. 30 fps is the current production target; more frames require more rendering work.',
  localRenderBackend:'Processing on this computer only. NVIDIA accelerates compatible rendering; CPU is the compatibility option. Cloud rendering is separate.',
  settingDirectorProvider:'The model service that plans the story. Luna is the director, not the image generator.',
  settingReasoning:'How much reasoning the director uses where supported. Higher effort can increase latency and token usage.',
  settingDirectorExecution:'How director passes are grouped and reviewed. Lean reduces repetition; staged review keeps focused parallel visual passes.',
  settingDirectorTier:'Standard is the measured default. Fast costs more; Flex can delay or fail capacity. No silent fallback should change the tier.',
  settingDirectorParallelism:'Maximum independent API tasks. Story dependencies and provider limits still constrain actual overlap.',
  settingCompactDirectorCuts:'Experimental compact response structure for Lean. Required cuts and downstream reviews remain.',
  settingConciseDirectorPrompts:'Avoid repeating saved story/identity facts in visual supplements. The grounded final image prompt stays intact.',
  settingFactGroupSentences:'Maximum sentence group for source-fact analysis. Larger groups reduce calls but can increase fidelity risk.',
  settingVisualPacking:'How many visual plans share one request. Larger groups need their own measured quality/speed validation.',
  settingSourceVisualOverlap:'Experimental overlap between compatible fact work and visuals. Dependent continuity work must still wait.',
  settingFactBeatsMode:'Source of the initial story beats in Lean. Experimental alternatives must preserve source facts.',
  settingContinuity:'How strictly plans should preserve identity, objects and story state.',
  settingAppearance:'How to handle deliberate changes such as new clothing or injuries. Intentional story changes should not be repaired back to the old look.',
  settingRetries:'Maximum image retry attempts. A finite limit prevents endless repair loops; spending caps still apply.',
  settingResolution:'Image generation dimensions, separate from final video dimensions. The selected model must support them.',
  settingSteps:'Denoising/inference steps per image. More steps are not automatically better for distilled models.',
  settingGuidance:'How strongly the model follows text using its supported guidance mechanism. Model/workflow-specific; do not copy values blindly.',
  settingSampler:'Algorithm used to generate the image. It must be supported by the selected workflow.',
  settingScheduler:'How generation noise/timesteps are scheduled. Must match the model/workflow.',
  settingFallback:'Explicitly permit the SD fallback if the selected provider fails. It can change image style and quality.',
  videoRecipeSource:'Choose a project with an exact saved export setup. Copying preserves this project’s caps, reviews, intro and overlays.',
  engDestination:'YouTube or Patreon export version. Patreon omits promotional overlays/outro by default.',
  engPatreonOutro:'Include the spoken ending in Patreon exports too.',
  engPatreonPopup:'Include subscribe reminders in Patreon exports too.',
  engPopup:'Overlay occasional subscribe reminders during the video.',
  engMin:'Minimum minutes between reminders.',
  engMax:'Maximum minutes between reminders. Timing varies within the saved range.',
  engDuration:'How long each reminder stays visible, in seconds.',
  engPosition:'Where the reminder appears on the frame.',
  engText:'Visible reminder wording. It is not added to chapter narration.',
  engDing:'Play a short ding with each enabled reminder.',
  engVolume:'Ding loudness relative to the configured sound level.',
  engOutro:'Append a separate spoken ending, subject to the selected export version.',
  engOutroText:'Exact outro narration. Prepare outro audio after changing its wording.',
  engTeaser:'Text used for the next-part teaser. Keep it grounded in the next story section.',
  engOutroDuration:'Target ending length in seconds.',
  engSplit:'Create video parts after the complete export.',
  engPartMinutes:'Target length for time-based parts. Chapter-boundary mode follows the selected chapters instead.',
  engSplitMode:'Split by time intervals or exact chapter boundaries.',
  engSplitChapters:'Chapters per part when splitting by chapter boundaries.',
  splitMode:'Split the saved video by time or chapter boundaries.',
  splitChapters:'Number of whole chapters per part.',
  splitMinutes:'Target part length in minutes for time-based splitting.',
  cloudSearch:'Filter saved assets by name/path without changing them.',
  cloudCategory:'Filter the file list by media category.',
  thumbTitle:'Visible thumbnail title. Keep it short enough to read at small size.',
  thumbPart:'Optional part label such as 1, 5, or 1–6.',
  publishSource:'Saved cloud video to upload. It must be available in the cloud first.',
  publishTitle:'YouTube video title for the explicit upload.',
  publishDescription:'YouTube description for the explicit upload.',
  publishPrivacy:'Visibility of the YouTube upload. Check this before starting.',
  publishKids:'YouTube audience designation. Choose based on the actual content.',
  cloudRenderDestination:'YouTube or Patreon export version for the cloud render. The finished asset is saved to the configured Google bucket.',
  cloudRenderBudget:'Maximum USD allowance for this render job, including its configured compute/transfer scope.',
  vimeoToken:'Private Vimeo API credential saved only by the helper. It is excluded from project exports.',
  vimeoSource:'Cloud video Vimeo will retrieve directly, without a browser download.',
  vimeoTitle:'Title of the new Vimeo upload.',
  vimeoPrivacy:'Vimeo visibility. Availability depends on your account and granted permissions.',
};

const FIELD_NAMES={
  engPatreonOutro:'Outro in Patreon exports',engPatreonPopup:'Reminders in Patreon exports',engPosition:'Reminder position',engOutroText:'Outro narration',engOutroDuration:'Outro duration',introEnabled:'Enable intro',introDuration:'Intro duration',introPlacement:'Intro placement',introEndOnNarration:'End intro with its narration',introShowTitle:'Show intro title',introTitle:'Intro title',introSubtitle:'Intro subtitle',introVisualPrompt:'Intro visual prompt',introVoiceText:'Intro narration',splitMode:'Split mode',splitChapters:'Chapters per part',splitMinutes:'Minutes per part',cloudSearch:'Find saved files',cloudCategory:'File category',thumbTitle:'Thumbnail title',thumbPart:'Thumbnail part label',publishSource:'YouTube source video',publishTitle:'YouTube title',publishDescription:'YouTube description',publishPrivacy:'YouTube visibility',cloudRenderBudget:'Cloud render spending limit',vimeoSource:'Vimeo source video',vimeoTitle:'Vimeo title',
  settingAPIBudget:'Project API limit (USD)',settingGPUBudget:'Project GPU rental limit (USD)',settingQCPolicy:'How findings affect production',settingSampleEvery:'Sample about one image in',settingVoiceEngine:'Narration model',settingModelVoice:'Voice for selected model',settingSpeed:'Speaking speed',settingNarrationDelivery:'Narration delivery',settingVoiceBatchSize:'Kaggle chapters per voice job',settingRetries:'Max image retries',settingVideoFps:'Frames per second',settingVideoSize:'Video size',settingImageFit:'Image framing',settingMotionMode:'Image motion',settingReferenceStrength:'SD reference strength',settingVoiceInstruction:'Narrator instructions · Qwen3-TTS',settingKokoroKaggle:'Run Kokoro on Kaggle',settingFishLicense:'Fish Audio commercial license',settingDirectorTier:'API processing',settingDirectorExecution:'Director workflow',settingDirectorParallelism:'Independent Luna tasks',settingUnattended:'Complete full-video generation automatically',settingRepair:'Automatic image repairs',engMin:'Minimum reminder interval',engMax:'Maximum reminder interval',engPopup:'Subscribe reminder',engDing:'Reminder ding',engVolume:'Ding volume',engOutro:'Spoken outro',engText:'Reminder message',engTeaser:'Next-part teaser',engDestination:'Export version',engDuration:'Reminder duration',engPartMinutes:'Minutes per part',engSplit:'Automatic video parts',engSplitMode:'Split at',engSplitChapters:'Chapters per part',voicePreviewText:'Voice audition text',publishKids:'YouTube audience',cloudRenderDestination:'Cloud render export version',vimeoToken:'Private Vimeo API token',vimeoPrivacy:'Vimeo visibility',videoRecipeSource:'Copy from a saved test project',settingFactBeatsMode:'Emotional beat ownership',
};
export function fieldLocation(id){
  if(/^(cloud|split|fileSplit|thumb|publish|vimeo)/.test(id))return ['files'];
  if(/^intro/.test(id))return ['settings','settings-intro'];
  if(/^eng/.test(id))return ['settings','settings-engagement'];
  if(/^settingWatermark/.test(id))return ['settings','settings-watermark'];
  if(/^setting(?:Check|Sample|QC|Repair)/.test(id))return ['settings','settings-checks'];
  if(/^setting(?:Voice|ModelVoice|Kokoro|Fish|Speed|Narration|Sound|Emphasis)/.test(id)||id==='voicePreviewText')return ['settings','settings-voice'];
  if(/^setting(?:API|GPU|Unattended)/.test(id))return ['settings','settings-cost'];
  if(/^setting(?:Video|ImageFit|Zoom|Motion)/.test(id)||id==='localRenderBackend')return ['settings','settings-video'];
  if(/^setting(?:ProjectName|Style|Layout|GenerationMode|ImagePreset)$/.test(id))return ['settings','settings-basics'];
  if(id==='videoRecipeSource')return ['settings','settings-saved-setup'];
  if(id==='settingEconomyPanels')return ['settings','settings-economy'];
  if(/^setting(?:ImageProvider|ImageModel|ImageWorkflow|ReferenceStrength|FocusedPrompts)$/.test(id))return ['settings','settings-images'];
  return ['settings','settings-advanced'];
}
export function fieldFeatures(){
  return Object.entries(FIELD_HELP).map(([id,text])=>{
    const title=FIELD_NAMES[id] || id.replace(/^setting/,'').replace(/([a-z])([A-Z])/g,'$1 $2').replace(/^./,c=>c.toUpperCase());
    return ['Field reference',title,text,...fieldLocation(id)];
  });
}
export function findFeatures(query = '', scope) {
  const phrase = query.trim().toLocaleLowerCase();
  const words = phrase.split(/\s+/).filter(Boolean);
  const score = item => (phrase && item[1].toLocaleLowerCase().includes(phrase) ? 100 : 0) + words.filter(w => item[1].toLocaleLowerCase().includes(w)).length;
  return [...FEATURES,...fieldFeatures()].filter(item => (!scope || item[3] === scope) && words.every(word => item.slice(0, 3).join(' ').toLocaleLowerCase().includes(word))).sort((a,b) => score(b)-score(a));
}

export function matchesSetting(text, words) {
  const tokens=String(text).toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
  return words.every(word=>tokens.some(token=>token.startsWith(word)));
}

export function workspaceGuideHTML(tab) {
  const [title, text, steps] = WORKSPACES[tab] || WORKSPACES.write;
  return `<aside class="workspace-guide" aria-label="${esc(title)} guide"><div><strong>${esc(title)} · what to do here</strong><p>${esc(text)}</p></div><button type="button" class="guide-help-button" data-open-guide="${esc(tab)}">Explain this section</button><details><summary>How it works</summary><ul>${steps.map(step => `<li>${esc(step)}</li>`).join('')}</ul></details></aside>`;
}

export function guideDestination(tab, anchor) {
  const sections={'settings-basics':'Project basics','settings-saved-setup':'Saved video setup','settings-checks':'Visual checks','settings-images':'Image generator','settings-economy':'Economy storyboard canvases','settings-voice':'Voice settings','settings-intro':'Intro settings','settings-engagement':'Reminders and ending','settings-watermark':'Watermark settings','settings-cost':'Spending limits','settings-video':'Video output','settings-advanced':'Advanced AI settings'};
  return sections[anchor] || WORKSPACES[tab]?.[0] || 'Studio';
}

const sectionNotes = {
  'Project basics': 'Name, visual style and pacing for new plans.',
  'Saved video setup': 'Copy the exact setup recorded by a compatible saved export.',
  'Visual checks': 'Review coverage, major-error repairs and retry behavior.',
  'Image generator': 'Model, backend and reference workflow.',
  'Economy storyboard canvases': 'Optional legacy multi-panel generation; lower detail per shot.',
  'Voice': 'Speech model, its own voices, delivery and a short audition.',
  'Optional intro': 'Separate opening visuals and explicit narration.',
  'Export versions, reminders & ending': 'YouTube/Patreon versions, subscribe reminders, ding, outro and parts.',
  'Watermark': 'Text or a logo over the finished video.',
  'Spending limits and automatic production': 'Automatic completion and separate API/GPU limits.',
  'Video output': 'Export size, frame rate, image motion and local rendering.',
  'Advanced AI settings': 'Director tuning, image parameters and provider connections.',
};
const sectionIds = {'Project basics':'settings-basics','Image generator':'settings-images','Economy storyboard canvases':'settings-economy','Saved video setup':'settings-saved-setup','Advanced AI settings':'settings-advanced'};

export function organizeSettings(container, state = {}) {
  const doc = container.ownerDocument;
  const groups = [];
  for (const element of [...container.children]) {
    const heading = element.matches('.section-box') ? element.querySelector(':scope > h3') : element.matches('details') ? element.querySelector(':scope > summary') : null;
    if (!heading) continue;
    const title = heading.textContent.trim();
    if (!sectionNotes[title]) continue;
    let group = element;
    if (element.tagName !== 'DETAILS') {
      group = doc.createElement('details');
      group.className = 'settings-group';
      group.id = element.id || sectionIds[title] || '';
      const summary = doc.createElement('summary');
      summary.textContent = title;
      group.append(summary);
      heading.remove();
      group.append(...element.childNodes);
      element.replaceWith(group);
    } else {
      group.classList.add('settings-group');
      group.id ||= sectionIds[title] || '';
    }
    group.dataset.ui = 'setting-group-' + group.id;
    const note = doc.createElement('span');
    note.className = 'settings-group-note';
    note.textContent = sectionNotes[title];
    group.querySelector(':scope > summary').append(note);
    group.open = state.open?.has(group.id) ?? title === 'Project basics';
    group.addEventListener('toggle', () => {
      if (state.query) return; // Searching must not replace the user's expansion choices.
      state.open ??= new Set(['settings-basics']);
      if (group.open) state.open.add(group.id); else state.open.delete(group.id);
    });
    groups.push(group);
  }
  for (const control of container.querySelectorAll('input[id]:not([type="hidden"]),select[id],textarea[id]')) {
    const text = FIELD_HELP[control.id];
    const label = control.closest('label');
    if (!text || !label) continue;
    const hint = doc.createElement('small');
    hint.id = 'explain-' + control.id;
    hint.className = 'setting-explanation';
    hint.textContent = text;
    // Keep the original label as the control's name; the linked hint is its description.
    hint.setAttribute('aria-hidden','true');
    hint.hidden = !state.explanations;
    label.append(hint);
    control.setAttribute('aria-describedby', [control.getAttribute('aria-describedby'), hint.id].filter(Boolean).join(' '));
    label.dataset.settingSearch = (label.firstChild?.textContent + ' ' + text).toLocaleLowerCase();
  }
  const tools = doc.createElement('div');
  tools.className = 'settings-finder';
  tools.innerHTML = '<label>Find a setting<input type="search" id="studioSettingsSearch" placeholder="Try voice, repairs, budget, motion…" aria-describedby="studioSettingsMatches"></label><div class="settings-finder-actions"><label class="inline"><input type="checkbox" id="studioSettingExplanations">Show field explanations</label><button type="button" data-settings-expand>Expand all</button><button type="button" data-settings-collapse>Collapse all</button></div><p id="studioSettingsMatches" class="muted" role="status" aria-live="polite"></p>';
  const before = groups[0];
  if (before) before.before(tools); else container.prepend(tools);
  const search = tools.querySelector('input[type="search"]');
  const apply = () => {
    const words = search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    state.query = search.value;
    let matches = 0;
    for (const group of groups) {
      const labels = [...group.querySelectorAll('label')];
      const heading = group.querySelector(':scope > summary').textContent.toLocaleLowerCase();
      const matchingLabels = labels.filter(label => matchesSetting(label.dataset.settingSearch || label.textContent,words));
      const visible = !words.length || matchesSetting(heading,words) || matchingLabels.length > 0;
      group.hidden = !visible;
      for (const label of labels) label.classList.toggle('setting-match', !!words.length && matchingLabels.includes(label));
      if(words.length)for(const label of matchingLabels){
        for(let detail=label.closest('details');detail&&detail!==group;detail=detail.parentElement?.closest('details'))detail.open=true;
      }
      if (visible) {
        matches++;
        group.open = words.length > 0 || (state.open?.has(group.id) ?? group.id === 'settings-basics');
      }
    }
    tools.querySelector('[role="status"]').textContent = words.length ? `${matches} of ${groups.length} sections match. Clear search to show all settings.` : `${groups.length} sections · changes stay in this form until you save.`;
  };
  search.value = state.query || '';
  search.addEventListener('input', apply);
  const explain = tools.querySelector('#studioSettingExplanations');
  explain.checked = !!state.explanations;
  explain.addEventListener('change', () => {
    state.explanations = explain.checked;
    for (const hint of container.querySelectorAll('.setting-explanation')) hint.hidden = !explain.checked;
  });
  for (const [selector, open] of [['[data-settings-expand]', true], ['[data-settings-collapse]', false]]) {
    tools.querySelector(selector).addEventListener('click', () => {
      state.open = new Set(open ? groups.map(g => g.id) : []);
      for (const group of groups) group.open = open;
    });
  }
  apply();
  return {
    reveal(id) {
      search.value = ''; apply();
      const target = groups.find(group => group.id === id);
      if (!target) return;
      target.open = true;
      state.open ??= new Set(['settings-basics']); state.open.add(target.id);
      target.scrollIntoView({block:'start',behavior:'auto'});
      target.querySelector('summary').focus({preventScroll:true});
    },
  };
}

export function openStudioGuide({scope = 'write', all = false, navigate, document: doc = globalThis.document} = {}) {
  const existing = doc.getElementById('studioFunctionGuide');
  if (existing) { existing.querySelector('input').focus(); return existing; }
  const previous = doc.activeElement;
  const dialog = doc.createElement('dialog');
  dialog.id = 'studioFunctionGuide';
  dialog.className = 'studio-function-guide';
  dialog.setAttribute('aria-labelledby','studioGuideTitle');
  dialog.innerHTML = '<header><div><p class="guide-kicker">Studio help</p><h2 id="studioGuideTitle">What does this do?</h2></div><button type="button" data-guide-close aria-label="Close help">Close</button></header><p>Search the functions below. Help does not change your project or start generation.</p><label>Find a function<input id="studioGuideSearch" type="search" placeholder="Try repair, intro, upload, parallel…"></label><div class="guide-scope"><button type="button" data-guide-scope="current">This section</button><button type="button" data-guide-scope="all">All functions</button></div><p id="studioGuideCount" role="status" aria-live="polite"></p><div id="studioGuideResults"></div>';
  doc.body.append(dialog);
  let currentOnly = !all;
  const search = dialog.querySelector('input');
  const render = () => {
    const results = findFeatures(search.value, currentOnly ? scope : undefined);
    dialog.querySelector('#studioGuideCount').textContent = `${results.length} functions and settings${currentOnly ? ' in this section' : ''}`;
    dialog.querySelector('#studioGuideResults').innerHTML = results.length ? results.map(([category,title,description,tab,anchor]) => `<article><small>${esc(category)}</small><h3>${esc(title)}</h3><p>${esc(description)}</p>${navigate && tab !== 'audio' && tab !== 'planner' ? `<button type="button" data-guide-go="${esc(tab)}" data-guide-anchor="${esc(anchor || '')}">Open ${esc(guideDestination(tab,anchor))}</button>` : tab === 'planner' ? '<a href="./pipeline.html" target="_blank" rel="noopener">Open production planner</a>' : tab === 'audio' ? '<a href="./" target="_blank" rel="noopener">Open Audio</a>' : ''}</article>`).join('') : '<p>No matching function. Try fewer words, or choose All functions.</p>';
    for (const button of dialog.querySelectorAll('[data-guide-scope]')) button.setAttribute('aria-pressed', String((button.dataset.guideScope === 'current') === currentOnly));
    dialog.querySelectorAll('[data-guide-go]').forEach(button => button.addEventListener('click', () => {
      const tab = button.dataset.guideGo, anchor = button.dataset.guideAnchor;
      dialog.close();
      void navigate(tab, anchor);
    }));
  };
  search.addEventListener('input', () => { if (search.value.trim()) currentOnly = false; render(); });
  dialog.querySelectorAll('[data-guide-scope]').forEach(button => button.addEventListener('click', () => { currentOnly = button.dataset.guideScope === 'current'; render(); }));
  dialog.querySelector('[data-guide-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { dialog.remove(); if (previous?.isConnected) previous.focus({preventScroll:true}); }, {once:true});
  render(); dialog.showModal(); search.focus();
  return dialog;
}

export function mountStandaloneGuide(scope, document = globalThis.document) {
  const host = document.querySelector('.top-actions, .studio-nav, .topbar nav, header nav');
  if (!host || document.getElementById('studioGlobalHelp')) return;
  const button = document.createElement('button');
  button.type = 'button'; button.id = 'studioGlobalHelp'; button.className = 'guide-global-help';
  button.textContent = 'Help · all functions';
  button.addEventListener('click', () => openStudioGuide({scope, all:true, document}));
  host.append(button);
}

export function organizeFileTools(container) {
  for (const section of [...container.querySelectorAll(':scope > .section-box')]) {
    const heading=section.querySelector(':scope > h3');
    if (!heading) continue;
    const title=heading.textContent.trim();
    const details=container.ownerDocument.createElement('details');
    details.className='file-tool-group section-box';
    if(section.id)details.id=section.id;
    details.dataset.ui='file-tool-'+title.toLowerCase().replace(/[^a-z0-9]+/g,'-');
    const summary=container.ownerDocument.createElement('summary');summary.textContent=title;
    heading.remove();details.append(summary,...section.childNodes);section.replaceWith(details);
  }
}
