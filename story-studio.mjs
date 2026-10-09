import {captureFormFocus,restoreFormFocus,openFormDialog} from './studio-form-state.mjs?v=forms-20261009';
import {createFormDrafts,createProjectSelection} from './studio-form-drafts.mjs?v=drafts-20261009';
import {resolveConnectedProject,canAdoptConnection} from './studio-connection.mjs?v=connection-20261009';
import {checkpointProject,backupSnapshot,canApplyBackgroundProject} from './studio-project-safety.mjs?v=safety-20261009';
import {createMediaLoader} from './studio-media-loader.mjs?v=media-20261009b';
import {createEditorSave} from './studio-editor-save.mjs?v=editor-20261009b';
import { pairingKey, helperJson, importHelperPairing } from "./helper-connection.mjs?v=connection-20261009";
import { nativeRequest } from "./native-client.mjs?v=connection-20261009";
import {rendererPlacement} from './studio-render-status.mjs?v=render-research-20261009';
import {createProgressReader} from './studio-progress.mjs?v=large-20261008';
import {engagementForm, engagementValues, filesPanel, wireFiles, cachedMediaLink, directCloudDownload, videoDeliveryStatus} from './studio-cloud-ui.mjs?v=files-20261009b';
import {
  loadProjectState,
  saveStudioProject,
  loadStudioProject,
  listStudioProjects,
} from "./image-store.mjs?v=storage-2";

const $ = (s) => document.querySelector(s),
  escape = (s) =>
    String(s ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
const time = (n) =>
  `${Math.floor((n || 0) / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor((n || 0) % 60)
    .toString()
    .padStart(2, "0")}`;
const id = (p) => p + crypto.randomUUID().replaceAll("-", "").slice(0, 16);
let key = pairingKey(),
  project,
  chapterId,
  tab = "write",
  health,
  projects = [],
  queue,
  connected = false,
  dirty = false,
  polling = false,
  scenePage = 0,
  overlapRequested = false,
  saveTimer;
let cacheWarning="";
let connectionPromise;
const startingProduction=new Set();
const settingsDrafts=createFormDrafts(),projectSelection=createProjectSelection();
const mediaCache = new Map();
const progressReader=createProgressReader(qualityDecision);
const mediaLoader=createMediaLoader({resolve:(path,projectId,options)=>media(path,projectId,options)});
const root = document.createElement("section");
root.id = "productionStudio";
root.className = "production";
function settingsDraftStatus(){
  const status=$('#settingsDraftStatus');
  if(status)status.textContent=settingsDrafts.has(project?.id)?'Unsaved settings · retained in this open page until you save.':'';
}
function rememberSettings(event){
  if(tab!=='settings'||!project||!root.contains(event.target))return;
  if(/^(setting|intro|eng)/.test(event.target.id)&&settingsDrafts.remember(project.id,event.target))settingsDraftStatus();
}
root.addEventListener('input',rememberSettings);
root.addEventListener('change',rememberSettings);
window.addEventListener('beforeunload',event=>{
  if(editorSave.dirty||settingsDrafts.dirty||document.querySelector('dialog.studio-form-dialog[open][data-unsaved="true"]')){event.preventDefault();event.returnValue='';}
});
const switcher = document.createElement("nav");
switcher.className = "production-switch";
switcher.setAttribute("aria-label", "Studio mode");
switcher.innerHTML =
  '<button id="productionMode" aria-pressed="true">Story → video</button><button id="comicMode" aria-pressed="false">Comic & panel tools</button>';
$(".topbar").after(switcher);
switcher.after(root);
document.body.classList.add("production-mode");
$("#productionMode").onclick = () => mode(true);
$("#comicMode").onclick = () => mode(false);
function mode(production) {
  document.body.classList.toggle("production-mode", production);
  root.hidden = !production;
  $("#productionMode").setAttribute("aria-pressed", production);
  $("#comicMode").setAttribute("aria-pressed", !production);
}
function ch() {
  return (
    project?.chapters.find((c) => c.id === chapterId) || project?.chapters[0]
  );
}
function shots(c = ch()) {
  return c?.scenes.flatMap((s) => s.shots) || [];
}
function people() {
  return [
    ...(project?.characters || []),
    ...(ch()?.people || []).filter(
      (p) => !p.removed && !project.characters.some((c) => c.id === p.id),
    ),
  ];
}
function names(cast) {
  return (
    cast
      .map((c) => people().find((x) => x.id === c.id)?.name || c.id)
      .join(", ") || "No characters"
  );
}
function note(message, error = false) {
  const el = $("#productionNotice");
  if (el) {
    el.hidden = false;
    el.className = "notice" + (error ? " error" : "");
    el.textContent = message;
  }
}
async function api(path, body) {
  if (!key)
    throw new Error(
      "Open the pairing link printed by the shared NVIDIA helper. Audio and Studio use the same connection.",
    );
  return helperJson("/studio/" + path, key, body);
}
async function cache() {
  if(!project)return;
  const warnings=[];
  await checkpointProject(project,{
    connected,cloudEnabled:Boolean(health?.storage?.enabled),save:saveStudioProject,
    remember:projectId=>localStorage.setItem('qt-production-project',projectId),
    warn:message=>warnings.push(message),
  });
  cacheWarning=warnings.join(' ');
  if(cacheWarning)note(cacheWarning,true);
}
async function media(path, projectId=project.id, {refresh=false}={}) {
  if(refresh)mediaCache.delete(projectId+'/'+path);
  return cachedMediaLink(mediaCache,projectId,path,api);
}
async function fillMedia() {
  mediaLoader.load(root,project.id);
}
async function refreshProjects() {
  projects = connected ? await api("projects") : await listStudioProjects();
}
async function connectHelper() {
  if (pairingKey()) { await connect(); if (connected) return; }
  document.getElementById('helperPairingDialog')?.remove();
  const dialog=document.createElement('dialog');
  dialog.id='helperPairingDialog';dialog.setAttribute('aria-labelledby','helperPairingTitle');
  dialog.style.cssText='max-width:520px;width:calc(100% - 40px);padding:24px;border-radius:14px;';
  dialog.innerHTML=`<h2 id="helperPairingTitle">Connect your shared helper</h2><p>Select the private helper pairing file. Its key stays in this browser and is sent only to your local helper. This does not grant access to another computer.</p><label>Helper pairing file<input id="helperPairingFile" type="file"></label><p class="muted">Use the helper’s .pairing-key file or its saved pairing text file. You can also open the helper’s normal pairing link.</p><label>Or enter helper pairing key<input id="helperPairingInput" type="password" autocomplete="off" spellcheck="false"></label><p id="helperPairingStatus" role="status"></p><div class="toolbar"><button id="helperPairingSubmit">Connect helper</button><button id="helperPairingCancel">Cancel</button></div>`;
  document.body.append(dialog);
  const status=dialog.querySelector('#helperPairingStatus'),input=dialog.querySelector('#helperPairingInput'),file=dialog.querySelector('#helperPairingFile');
  const pair=async value=>{
    try{importHelperPairing(value);input.value='';file.value='';await connect();if(connected){dialog.close();dialog.remove();}else status.textContent='The helper did not connect. Keep it running and allow this site’s local network access in Chrome.';}
    catch(error){status.textContent=error.message;}
  };
  file.onchange=async()=>{
    const selected=file.files[0];if(!selected)return;
    if(selected.size>2048){status.textContent='Choose the small helper pairing file, not a project or API credential.';file.value='';return;}
    try{await pair(await selected.text());}catch{status.textContent='The pairing file could not be read. Select it again.';}
  };
  dialog.querySelector('#helperPairingSubmit').onclick=()=>pair(input.value);
  dialog.querySelector('#helperPairingCancel').onclick=()=>{dialog.close();dialog.remove();};
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();
}
function connect() {
  if(connectionPromise)return connectionPromise;
  connectionPromise=reconnect().finally(()=>{connectionPromise=undefined;});
  return connectionPromise;
}
async function reconnect() {
  key=pairingKey();
  // Keep autosave local until the connection has chosen a safe snapshot.
  // Existing editors stay mounted while health/project requests are pending.
  connected=false;
  try {
    const info=await api('health');
    const available=await api('projects');
    await flush();
    const requestedProject=project,startEditorRevision=editorSave.revision;
    const resolved=await resolveConnectedProject({project:requestedProject,projects:available,providers:info.providers,api});
    // Includes edits typed during the final project fetch/import, before redraw.
    await flush();
    if(canAdoptConnection(project,resolved.project,{requestedProject,
      editorRevision:editorSave.revision,startEditorRevision,preserveOffline:resolved.preserveOffline}))
      project=resolved.project;
    health=info;queue=info.queue;projects=available;
    if(!projects.some(p=>p.id===project.id))projects=[{id:project.id,name:project.name,revision:project.revision,chapters:project.chapters.length},...projects];
    mediaCache.clear();connected=true;chapterId=ch()?.id;
    await cache();render();
    if(project._unsynced)note('Your offline edits are retained. Review them, then use Sync offline edits; existing helper generations remain saved.');
  } catch(error) {
    connected=false;
    // Export and the mounted editor must retain the latest input even if its
    // browser checkpoint failed. No remote overwrite or automatic re-import.
    if(editorSave.dirty&&project){project=editorSnapshot();project._unsynced=true;}
    render();note(error.message,true);
  }
}
function editorSnapshot(){
  return backupSnapshot(project,chapterId,tab,{
    sourceText:$('#chapterStory')?.value,name:$('#chapterName')?.value,
    cleanNarrationText:$('#narrationScript')?.value,narrationMode:$('#narrationMode')?.value,
    includeChapterLabel:$('#includeChapterHeading')?.checked,
  });
}
async function mutate(path, body, redraw = true) {
  const requestedProject=project.id;
  const p = await api(path, { project: requestedProject, ...body });
  // The helper committed its result to the requested project. A slow edit
  // must not switch the user's currently selected workspace back to it.
  if(project.id!==requestedProject)return p;
  if (p?.chapters) {
    if((Number(p.revision)||0)<(Number(project.revision)||0))return p;
    project = p;
    await cache();
  } else queue = p;
  if (redraw) render();
  return p;
}
async function patch(scope, item, values, redraw = false) {
  if (connected) {
    try {
      await mutate(
        "edit",
        { scope, id: item, chapter: chapterId, patch: values },
        redraw,
      );
    } catch (error) {
      if (!error.message.includes("Cannot reach the NVIDIA helper"))
        throw error;
      connected = false;
    }
  }
  if (!connected) {
    delete project._connectionPlaceholder;
    const target =
      scope === "project"
        ? project
        : scope === "chapter"
          ? ch()
          : scope === "shot"
            ? shots().find((s) => s.id === item)
            : scope === "character"
              ? project.characters.find((c) => c.id === item)
              : people().find((p) => p.id === item);
    Object.assign(target, values);
    project._unsynced = true;
    await cache();
    if (redraw) render();
  }
  const state = $("#productionSaveState");
  if (state)
    state.textContent = connected
      ? health?.storage?.enabled ? "Saved to helper · cloud sync runs in the background" : "Saved to helper and browser"
      : "Saved in browser · helper offline";
}
async function action(fn) {
  try {
    if(connectionPromise)await connectionPromise;
    await flush();
    await fn();
  } catch (error) {
    note(error.message, true);
  }
}
const editorSave=createEditorSave(persistEditor);
async function flush() {
  clearTimeout(saveTimer);
  await saveEditor();
}
async function saveEditor() {
  try { await editorSave.flush(); }
  finally { dirty=editorSave.dirty; }
}
async function persistEditor() {
  const c = ch();
  if (!c) return;
  if (tab === "write") {
    const source = $("#chapterStory")?.value ?? c.sourceText;
    await patch(
      "chapter",
      c.id,
      { sourceText: source, name: $("#chapterName")?.value || c.name },
      false,
    );
  } else if (tab === "narration") {
    await patch(
      "chapter",
      c.id,
      {
        cleanNarrationText: $("#narrationScript").value,
        narrationMode: $("#narrationMode").value,
        includeChapterLabel: $("#includeChapterHeading").checked,
      },
      false,
    );
  }
}
function dirtyEditor() {
  delete project._connectionPlaceholder;
  editorSave.markDirty();
  dirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => void saveEditor().catch(error=>note(error.message,true)), 600);
  if (tab === "write")
    $("#wordCount").textContent = wordStats($("#chapterStory").value);
}
function wordStats(text) {
  const words = (text.match(/[\p{L}\p{N}]+/gu) || []).length;
  return `${words.toLocaleString()} words · ~${time((words / 150) * 60)} narration`;
}
function offlineProject() {
  return {
    schemaVersion: 1,
    id: id("pr-"),
    name: "My story",
    revision: 0,
    created: Date.now() / 1000,
    updated: Date.now() / 1000,
    _unsynced: true,
    settings: {
      style: "cinematic illustration",
      layoutMode: "AUTO",
      generationMode: "BALANCED",
      autoContinue: false,
      voice: "am_michael",
      speed: 1,
      narrationDelivery: "standard",
      emphasisPhrases: ["already gone", "only twelve days"],
      director: {
        provider: "local-qwen",
        model: "Qwen3.5-4B Q4_K_M",
        reasoning: "Balanced",
        vision: false,
      },
      image: {
        provider: "existing",
        model: "sd15",
        workflow: "text-to-image",
        preset: "Balanced Quality",
        width: 512,
        height: 512,
        steps: 20,
        sampler: "DPM++ 2M",
        scheduler: "karras",
        guidance: 7,
        referenceStrength: 0.65,
        loras: [],
        controlnets: [],
        ipAdapter: {},
        fallbackEnabled: false,
        fallback: { provider: "existing", model: "sd15" },
      },
      continuityStrictness: "Medium",
      appearanceHandling: "Automatic",
      maxImageRetries: 2,
      visionQC: false,
      automaticRepair: false,
      qcPolicy: "practical",
      customLayout: {
        minDuration: 3,
        maxDuration: 30,
        imagesPerMinute: 8,
        minShots: 1,
        maxShots: 4,
        pacing: "balanced",
        cameraVariety: "medium",
        transition: "cut",
        movement: "subtle",
      },
      video: {
        width: 1280,
        height: 720,
        fps: 30,
        crf: 21,
        imageFit: "cover",
        motionMode: "gentle",
        zoomAmount: .06,
      },
    },
    intro: {
      enabled: false,
      duration: 15,
      placement: "full_story_only",
      title: "My story",
      showTitle: false,
      endOnNarration: true,
      shots: [],
      subtitle: "",
      voiceText: "",
      visualPath: "",
      audioPath: "",
    },
    characters: [],
    locations: [],
    continuity: {},
    storyMemory: {},
    chapters: [offlineChapter(1)],
    assets: [],
    render: {},
    warnings: [],
  };
}
function offlineChapter(n) {
  return {
    id: id("ch-"),
    number: n,
    name: "Chapter " + n,
    sourceText: "",
    cleanNarrationText: "",
    narrationMode: "automatic",
    includeChapterLabel: false,
    status: "NOT_ANALYZED",
    audio: {},
    people: [],
    scenes: [],
    timeline: [],
    analysis: {},
    handoff: {},
    manual: {},
    history: [],
    render: {},
    errors: [],
  };
}
function options(values, selected) {
  if (selected && !values.some((v) => (Array.isArray(v) ? v[0] : v) === selected))
    values = [...values, selected];
  return values
    .map((v) => {
      const [value, label] = Array.isArray(v) ? v : [v, v];
      return `<option value="${escape(value)}" ${value === selected ? "selected" : ""}>${escape(label)}</option>`;
    })
    .join("");
}
function stats() {
  const c = ch();
  return `<div class="stats"><div><strong>${time(c.audio.duration)}</strong><small>Narration duration</small></div><div><strong>${c.scenes.length}</strong><small>Scenes</small></div><div><strong>${shots().length}</strong><small>Shots</small></div><div><strong>${shots().filter((s) => s.imagePath).length}</strong><small>Images saved</small></div><div><strong>${c.people.filter((p) => p.type !== "main").length}</strong><small>Supporting / temporary</small></div></div>`;
}
function refreshBackgroundSummary() {
  const chapterLookup = new Map(project.chapters.map(chapter=>[chapter.id,chapter]));
  for (const button of root.querySelectorAll("[data-chapter]")) {
    const chapter = chapterLookup.get(button.dataset.chapter);
    if (!chapter) continue;
    button.querySelector("strong").textContent = chapter.name;
    button.querySelector("small").textContent =
      chapter.status.replaceAll("_", " ") +
      (chapter.continuityNeedsReview ? " · review continuity" : "");
  }
  const subtitle = root.querySelector(".chapter-heading .subtitle");
  if (subtitle)
    subtitle.textContent = `${ch().status.replaceAll("_", " ")} · ${project.settings.image.model} · ${project.settings.image.workflow}`;
  const summary = root.querySelector("#productionContent > .stats");
  if (summary) summary.outerHTML = stats();
  // Update progress without replacing the story/narration editor or its selection.
}
function render() {
  progressReader.clear();
  if (!project) {
    root.innerHTML =
      '<div class="notice">Connecting to your shared helper…</div>';
    return;
  }
  const c = ch();
  chapterId = c?.id;
  if (!c) {
    project.chapters.push(offlineChapter(1));
    chapterId = project.chapters[0].id;
    return render();
  }
  const focusContext=JSON.stringify([project.id,chapterId,tab]);
  const savedFocus=captureFormFocus(root,focusContext);
  const openControls = [...root.querySelectorAll('details[data-ui][open]')].map(el => el.dataset.ui);
  const overlapEligible = connected && health?.cloudOverlapAvailable &&
    project.settings.image.provider === "comfyui" && project.settings.director.provider === "openai-luna" &&
    Number.isFinite(project.settings.budget?.openaiUSD) && project.settings.budget.openaiUSD > 0 &&
    !project.settings.economyPanels;
  const placement = rendererPlacement(connected,health);
  root.innerHTML = `<div class="project-bar"><div><div class="kicker">Your story workspace</div><h1>${escape(project.name)}</h1><div class="connection-line"><span class="connection-dot ${connected ? 'connected' : ''}" aria-hidden="true"></span>${connected ? `Shared helper connected · ${escape(health.hardware.gpu)}` : "Helper offline · edits use this browser"}</div></div><div class="toolbar project-controls"><select id="productionProject" aria-label="Project">${options(
    projects.map((p) => [p.id, p.name+(p.loadError?" · needs recovery":"")]),
    project.id,
  )}</select><details class="action-menu" data-ui="project-actions"><summary>Project actions</summary><div class="toolbar"><button id="productionNewProject">New project</button><button id="productionConnect">${connected ? "Reconnect" : "Connect helper"}</button><button id="productionBackup">Export project</button><label class="import-control"><button id="productionImport">Import</button><input id="productionImportFile" type="file" accept="application/json,.json" hidden></label></div></details></div></div>
  <div id="productionNotice" class="notice" role="status" aria-live="polite" hidden></div>
  <div class="full-video-bar"><div><strong>Your complete story, in one video</strong><p class="muted">Add your chapters below, then start the full workflow. Saved results and manual edits are preserved. Voice generation stays on your computer.</p><label class="inline"><input id="productionOverlap" type="checkbox" ${overlapEligible && overlapRequested ? "checked" : ""} ${overlapEligible ? "" : "disabled"}> Overlap cloud tasks (preview)</label><p class="muted">Runs direction and images together, plus only the reviews selected in Settings. Requires cloud images, Luna and an API spending cap.</p><details data-ui="render-placement"><summary>${escape(placement.title)}</summary><p class="muted">${escape(placement.detail)}</p></details><div id="productionReadiness" role="status"></div><div id="fullVideoStatus" role="status" aria-live="polite"></div></div><div class="toolbar"><button id="productionCheckReadiness" ${connected ? "" : "disabled"}>Check readiness</button><button class="primary" id="productionFullVideo" ${connected ? "" : "disabled"}>Generate full video</button></div></div>
  ${project._unsynced ? '<div class="notice">This browser has offline edits.<button id="syncOffline">Sync offline edits</button></div>' : ""}
  ${project.warnings
    .filter((w) => !w.resolved)
    .map(
      (w) =>
        `<div class="notice"><strong>${escape(w.message)}</strong><p>${escape(w.categories.join(", "))}</p><button data-warning="${w.id}" data-choice="update">Update affected chapters</button> <button data-warning="${w.id}" data-choice="keep">Keep existing chapters</button></div>`,
    )
    .join("")}
  <div class="studio-grid"><aside class="chapter-rail" aria-label="Chapters"><h2>Chapters</h2>${project.chapters.map((x) => `<button class="chapter-button ${x.id === c.id ? "selected" : ""}" data-chapter="${x.id}" ${x.id === c.id ? 'aria-current="true"' : ''}><span>${x.number.toString().padStart(2, "0")}</span><strong>${escape(x.name)}</strong><small>${escape(x.status.replaceAll("_", " "))}${x.continuityNeedsReview ? " · review continuity" : ""}</small></button>`).join("")}<button class="chapter-add" id="productionAddChapter">+ New chapter</button></aside>
  <div class="production-main"><div class="chapter-heading"><div><h2>${escape(c.name)}</h2><div class="subtitle">${escape(c.status.replaceAll("_", " "))} · ${escape(project.settings.image.model)}</div></div><details class="action-menu" data-ui="chapter-actions"><summary>Chapter actions</summary><div class="toolbar"><button id="productionDuplicate">Duplicate</button><button id="productionMoveUp" aria-label="Move chapter earlier">↑</button><button id="productionMoveDown" aria-label="Move chapter later">↓</button><button class="danger" id="productionDelete">Delete chapter</button></div></details></div>
  <nav class="workspace-tabs" role="tablist" aria-label="Chapter workflow">${[
    ["write", "Story", "Paste your chapter"],
    ["narration", "Narration", "Review & listen"],
    ["cast", "Characters", "People & references"],
    ["review", "Layout", "Scenes & images"],
    ["timeline", "Video", "Timeline & export"],
    ["settings", "Settings", "Style & AI options"],
    ["files", "Files", "Cloud, parts & thumbnails"],
  ]
    .map(
      ([v, label, hint], index) =>
        `<button role="tab" id="studio-tab-${v}" aria-controls="productionContent" aria-selected="${tab === v}" tabindex="${tab === v ? 0 : -1}" data-tab="${v}"><strong>${index < 5 ? index + 1 + ' · ' : ''}${label}</strong><small>${hint}</small></button>`,
    )
    .join("")}</nav>
  <section id="productionContent" role="tabpanel" aria-labelledby="studio-tab-${tab}">${content()}</section><div class="queue-panel" id="productionQueue"></div><p class="save-state" id="productionSaveState">Saved assets stay in the helper output folder. Refresh restores this project.</p></div></div>`;
  root.dataset.focusContext=focusContext;
  if(tab==="settings")settingsDrafts.restore(project.id,root);
  wire();
  settingsDraftStatus();
  renderQueue();
  for (const detail of root.querySelectorAll('details[data-ui]')) detail.open = openControls.includes(detail.dataset.ui);
  void fillMedia();
  restoreFormFocus(root,savedFocus);
  if(cacheWarning)note(cacheWarning,true);
}
function content() {
  const c = ch();
  switch (tab) {
    case "write":
      return `<label>Chapter name<input id="chapterName" value="${escape(c.name)}"></label><label>Story<textarea class="story-editor" id="chapterStory" placeholder="Paste this chapter’s story. Characters and continuity carry forward from earlier chapters.">${escape(c.sourceText)}</textarea></label><div class="toolbar story-actions"><span id="wordCount" class="muted">${wordStats(c.sourceText)}</span><button id="productionReviewNarration">Review narration</button><button class="primary" id="productionAnalyze">Analyze chapter</button><details class="action-menu" data-ui="story-tools"><summary>Story tools</summary><div class="toolbar"><button id="productionExample">Load two-chapter test story</button><button id="productionLegacy">Import existing comic story</button></div></details></div><p class="muted">Analyze creates narration and a scene plan. Review the layout before generating images.</p>${stats()}${c.errors.length ? '<details class="past-errors" data-ui="chapter-errors"><summary>Recent generation errors · ' + c.errors.length + '</summary>' : ''}${c.errors
        .slice(-2)
        .map((e) => `<div class="notice error">${escape(e.message)}</div>`)
        .join("")}${c.errors.length ? '</details>' : ''}`;
    case "narration":
      return `<h2>Exactly what the narrator will say</h2><div class="two-col"><label>Narration source<select id="narrationMode">${options(
        [
          ["automatic", "Use cleaned story text automatically"],
          ["manual", "Edit narration text manually"],
        ],
        c.narrationMode,
      )}</select></label><label class="inline"><input type="checkbox" id="includeChapterHeading" ${c.includeChapterLabel ? "checked" : ""}>Explicitly include chapter heading in narration</label></div><label>Narration script<textarea id="narrationScript" class="narration-editor">${escape(c.cleanNarrationText || cleanText(c.sourceText, c.name))}</textarea></label><p class="muted">Chapter labels and production notes are excluded by default. Quoted dialogue is preserved. Editing this script switches it to manual mode.</p><div class="toolbar"><button id="productionClean">Clean from story again</button><button class="primary" id="productionNarrate">Regenerate narration audio</button><button id="productionAnalyze">Analyze chapter</button></div>${c.audio.path ? `<audio controls data-asset="${escape(c.audio.path)}"></audio><p class="muted">${time(c.audio.duration)} · one continuous chapter WAV · real sentence timing. Word alignment is unavailable.</p><button id="productionDownloadAudio">Download ${escape(c.audio.downloadName)}</button>` : ""}<details><summary>Sentence timing</summary>${(c.audio.sentences || []).map((t) => `<p class="muted">${time(t.start)} → ${time(t.end)} · ${escape(t.text)}</p>`).join("")}</details>`;
    case "cast":
      return `<div class="toolbar"><h2 style="flex:1">Main character library</h2><button id="productionAddCharacter">+ Add main character</button></div><p class="muted">Permanent identity belongs here. Clothing and injuries are tracked separately for each scene.</p>${project.characters.map(personRow).join("") || '<p class="notice">Add known recurring characters, or promote detected people after analysis.</p>'}<div class="section-box"><h2>Detected people in ${escape(c.name)}</h2><p class="muted">Supporting and temporary people remain in this chapter. Only people you promote enter the main library.</p>${
        c.people
          .filter(
            (p) => !p.removed && !project.characters.some((x) => x.id === p.id),
          )
          .map(personRow)
          .join("") ||
        '<p class="muted">Analyze the chapter to detect people.</p>'
      }</div><details><summary>Locations and reference images</summary>${project.locations.map((l) => `<p><strong>${escape(l.name)}</strong> · ${escape(l.description)} <button data-location="${l.id}">Add location reference</button></p>`).join("")}<button id="editLocations">Edit locations JSON</button></details><details><summary>Continuity state and story memory</summary><button id="editContinuity">Edit continuity JSON</button><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${escape(JSON.stringify(c.handoff || {}, null, 2))}</pre></details>`;
    case "review":
      return review();
    case "timeline":
      return timeline();
    case "settings":
      return settings();
    case "files":
      return filesPanel(project);
  }
}
function cleanText(text, title) {
  return text
    .split("\n")
    .filter((line) => {
      const s = line.trim().replace(/^[#* ]+|[#* ]+$/g, "");
      return (
        s !== title &&
        !/^(chapter|part|book)\s+(\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)(\s*[:—–-].*)?$/i.test(
          s,
        ) &&
        !/^(scene|shot)\s+\d+\b|^(director|production|camera|image prompt|negative prompt|status|metadata)\s*:/i.test(
          s,
        )
      );
    })
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
function personRow(p) {
  const main = project.characters.some((x) => x.id === p.id);
  return `<article class="person-row"><div class="person-info"><h3>${escape(p.name)}<span class="type-tag">${escape(p.type)}</span></h3><p class="muted">${escape(p.description)}</p><p class="muted">${escape(p.evidence || "")}</p><div class="reference-strip">${p.references.map((r) => `<img data-asset="${escape(r.path)}" alt="${escape(r.kind)} reference">`).join("")}</div><div class="toolbar"><button data-person="${p.id}" data-person-action="edit">Edit profile</button><button data-person="${p.id}" data-person-action="reference">Add reference</button>${main ? `<button data-person="${p.id}" data-person-action="generate-reference">Generate reference</button>` : ""}${!main ? `<button data-person="${p.id}" data-person-action="accept">${p.accepted ? "Accepted" : "Accept"}</button><button data-person="${p.id}" data-person-action="promote">Promote to main</button><button data-person="${p.id}" data-person-action="attach">Attach / merge with main</button>` : ""}<button data-person="${p.id}" data-person-action="remove">Remove</button></div></div></article>`;
}
function review() {
  const c = ch();
  const page = c.scenes.slice(scenePage * 10, scenePage * 10 + 10);
  return `<h2>${c.scenes.length ? "Chapter analysis complete" : "Scene plan"}</h2>${stats()}<div class="notice">Narration is separate from chapter metadata. Intro: ${project.intro.enabled ? "ON · " + (project.intro.endOnNarration !== false ? "up to " : "") + project.intro.duration + " seconds · " + project.intro.placement.replaceAll("_", " ") : "OFF"}. Images use ${escape(project.settings.image.model)}.</div><div class="toolbar"><button data-go="narration">Review narration</button><button data-go="cast">Review characters</button><button data-go="settings">Review image settings</button><button class="primary" id="productionGenerate">Generate missing images</button><button id="productionRetryFailed">Regenerate failed images</button><button id="productionAnalyze">Regenerate chapter plan</button></div><label class="inline"><input id="productionAutoContinue" type="checkbox" ${project.settings.autoContinue ? "checked" : ""}>Auto continue to images after future analyses</label>${c.proposedPlan ? '<div class="notice">A new plan is ready. Existing manual shots were preserved.<button id="applyProposedPlan">Apply proposed plan</button></div>' : ""}<div class="page-row"><span class="muted">Scenes ${Math.min(c.scenes.length, scenePage * 10 + 1)}–${Math.min(c.scenes.length, scenePage * 10 + 10)} of ${c.scenes.length}</span><div class="toolbar"><button id="scenePrevious" ${scenePage === 0 ? "disabled" : ""}>Previous</button><button id="sceneNext" ${(scenePage + 1) * 10 >= c.scenes.length ? "disabled" : ""}>Next</button></div></div>${page.map(sceneRow).join("") || '<p class="muted">Paste a chapter and select Analyze. Qwen will plan scenes around the actual narration timing.</p>'}<details><summary>Director decisions and diagnosis metadata</summary><button id="editChapterPlan">Edit chapter plan JSON</button><pre style="max-height:280px;overflow:auto;white-space:pre-wrap">${escape(JSON.stringify(c.analysis, null, 2))}</pre></details>`;
}
function sceneRow(scene) {
  return `<article class="scene-item"><div class="scene-head"><div><h3>Scene ${ch().scenes.indexOf(scene) + 1} · ${escape(scene.purpose)}</h3><span class="scene-time">${time(scene.start)} → ${time(scene.end)}</span></div><span class="type-tag">${scene.origin || "AI"}</span></div><p class="narration-excerpt">${escape(scene.shots.map((s) => s.narrationSegment).join(" "))}</p><p class="muted">${escape(scene.location)} · ${escape(scene.mood)} · ${scene.shots.length} shots</p><p class="muted">Characters: ${escape(names(scene.characters))}</p><p class="muted">${escape(scene.pacingReason)}</p>${scene.appearanceChanges?.length ? `<div class="notice"><strong>Planned appearance / object changes</strong>${scene.appearanceChanges.map((e) => `<p>${escape(people().find((p) => p.id === e.characterId)?.name || e.characterId)} · ${escape(JSON.stringify(e.to))}<br><small>Story evidence: ${escape(e.reason)}</small></p>`).join("")}${!scene.appearanceChangesReviewed ? `<button data-accept-changes="${scene.id}">Accept planned changes</button>` : "Reviewed"}</div>` : ""}<div class="toolbar"><button data-scene="${scene.id}" data-scene-action="edit">Edit scene</button><button data-scene="${scene.id}" data-scene-action="add">Add shot</button><button data-scene="${scene.id}" data-scene-action="split">Split scene</button><button data-scene="${scene.id}" data-scene-action="merge">Merge with next</button><button data-scene="${scene.id}" data-scene-action="up">↑</button><button data-scene="${scene.id}" data-scene-action="down">↓</button><button data-scene="${scene.id}" data-scene-action="plan">Regenerate scene plan</button></div><div class="shot-grid">${scene.shots.map(shotRow).join("")}</div></article>`;
}
function checkLevel(s = project.settings) {
  return s.qcCheckLevel ?? ((s.visionQC || s.generationMode === "MAX QUALITY") ? s.qcPolicy || "practical" : "off");
}
function qualityDecision(shot) {
  return shot.qcDecision || {disposition: shot.qc?.pass === true ? "passed" : shot.qc?.pass === false ? "review" : "unreviewed", blocking: shot.qc?.pass === false && shot.qc?.action !== "review"};
}
function qualityLabel(shot) {
  return ({accepted: "Accepted by you", advisory: "Advisory · production continues", review: "Needs your review", pending: "QC pending", passed: "AI passed", unreviewed: "Not checked"})[qualityDecision(shot).disposition] || "Needs review";
}
function promptMeasurements(shot) {
  const m = shot.imageMetadata?.promptMeasurements;
  if (!m) return "";
  const seconds = m.clientIntervalSeconds == null ? "unknown client time" : `${m.clientIntervalSeconds.toFixed(2)}s client time`;
  const cost = m.estimatedImageIntervalRentalUSD == null ? "rental estimate unavailable" : `$${m.estimatedImageIntervalRentalUSD.toFixed(6)} estimated image interval rental`;
  return `<details><summary>Prompt measurements · ${m.promptWords} words</summary><p>${m.promptWords} whitespace words · ${m.promptCharacters} characters, including reference instructions. ${seconds}; ${cost}. Shared startup, warm-up, idle and shutdown are excluded. Token counts remain unknown unless actually measured.</p></details>`;
}
function shotRow(shot) {
  return `<article class="shot-card">${shot.imagePath ? `<img data-asset="${escape(shot.imagePath)}" alt="${escape(shot.action)}">` : `<div class="shot-empty">${escape(shot.status.replaceAll("_", " "))}</div>`}<h4>${time(shot.start)} → ${time(shot.end)} · ${escape(shot.camera.shot)}</h4><p>${escape(shot.action)}</p><p>${escape(shot.imageModel)} · ${escape(shot.workflow)} · AI check: ${escape(shot.qc?.status || "UNREVIEWED")} · ${escape(qualityLabel(shot))}</p>${shot.qc?.issues?.length ? `<details><summary>AI findings (${shot.qc.issues.length})</summary><p>${shot.qc.issues.map(escape).join("<br>")}</p></details>` : ""}<div class="shot-cast">${people()
    .map(
      (p) =>
        `<label><input type="checkbox" data-shot-cast="${shot.id}" value="${p.id}" ${shot.characters.some((c) => c.id === p.id) ? "checked" : ""}>${escape(p.name)} <small>${escape(p.type)}</small></label>`,
    )
    .join(
      "",
    )}<button data-shot="${shot.id}" data-shot-action="none">N/A</button></div><div class="toolbar"><button data-shot="${shot.id}" data-shot-action="edit">Edit shot</button><button data-shot="${shot.id}" data-shot-action="generate">${shot.imagePath ? "Regenerate" : "Generate"}</button><button data-shot="${shot.id}" data-shot-action="repair" ${shot.imagePath ? "" : "disabled"}>Repair</button><button data-shot="${shot.id}" data-shot-action="qc" ${shot.imagePath && checkLevel() !== "off" ? "" : "disabled"}>Visual QC</button>${shot.imagePath && shot.qc?.pass !== true && qualityDecision(shot).disposition !== "accepted" ? `<button data-shot="${shot.id}" data-shot-action="accept-image">Use this image · no repair</button>` : ""}<button data-shot="${shot.id}" data-shot-action="delete">Delete</button></div>${promptMeasurements(shot)}${shot.generationError ? `<p class="notice error">${escape(shot.generationError)}</p>` : ""}</article>`;
}
function timeline() {
  const c = ch();
  return `<h2>Chapter timeline</h2><p class="muted">Chapter narration begins at 00:00. Click a shot to edit its timing, motion or transition.</p>${c.audio.path ? `<audio controls data-asset="${escape(c.audio.path)}"></audio>` : ""}<div class="timeline">${shots()
    .map(
      (s) =>
        `<button data-shot="${s.id}" data-shot-action="edit" style="width:${Math.max(70, (s.end - s.start) * 7)}px">${time(s.start)}<small>${escape(s.camera.shot)}</small><small>${(s.end - s.start).toFixed(1)}s · ${escape(shotMotionLabel(s))}</small></button>`,
    )
    .join(
      "",
    )}</div><div class="toolbar"><button class="primary" id="productionRenderChapter">Render chapter</button><button class="primary" id="productionRenderFull">Render full story</button><button data-go="settings">Intro and video settings</button><button id="validateTimeline">Validate timing</button></div>${c.render.path ? `<div class="section-box"><h3>${escape(c.name)} video · ${time(c.render.duration)}${c.renderStale ? " · Previous render; rebuild for changes" : ""}</h3><video controls data-asset="${escape(c.render.path)}"></video><button id="downloadChapterVideo">Download chapter video</button></div>` : ""}${project.render.path ? `<div class="section-box"><h3>Full story · ${time(project.render.duration)}${project.renderStale ? " · Previous render; rebuild for changes" : ""}</h3><video controls data-asset="${escape(project.render.path)}"></video><button id="downloadFullVideo">Download full story</button></div>` : ""}<div class="section-box"><h3>Full story order</h3><p>${project.intro.enabled && project.intro.placement === "full_story_only" ? `Intro ${project.intro.endOnNarration !== false ? "up to " : ""}${project.intro.duration}s → ` : ""}${project.chapters.map((c) => escape(c.name) + (project.intro.enabled && project.intro.placement === "every_chapter" ? " + intro" : "")).join(" → ")}</p><p class="muted">Unchanged rendered chapters are reused. Intro OFF starts directly with Chapter 1.</p></div>`;
}
function settings() {
  const s = project.settings,
    i = s.image;
  const providers = health?.providers || {};
  const selected = providers[i.provider];
  const level = checkLevel(s), w = {enabled:false, type:"text", text:"Studio", imagePath:"", position:"bottom-right", widthPercent:12, opacity:.65, marginPercent:2, color:"#FFFFFF", ...s.watermark};
  const models = selected?.models || [i.model];
  return `<h2>Project settings</h2><p class="muted">Saved for this project. Use the sections below, then save your changes.</p><div class="toolbar"><button data-save-settings>Save changes</button><a href="#settings-checks">Checks</a><a href="#settings-intro">Intro</a><a href="#settings-watermark">Watermark</a><a href="#settings-video">Video</a><a href="#settings-voice">Voice</a><a href="#settings-cost">Spending limits</a></div><label>Project name<input id="settingProjectName" value="${escape(project.name)}"></label><div class="two-col"><label>Visual style<select id="settingStyle">${options(["cinematic illustration", "anime", "manga", "storybook", "photorealistic cinema", "full-color manhwa", "dark fantasy"], s.style)}</select></label><label>Director layout<select id="settingLayout">${options(["AUTO", "CINEMATIC", "DYNAMIC", "MANGA / ANIME", "STORYBOOK", "CUSTOM"], s.layoutMode)}</select></label><label>Generation mode<select id="settingGenerationMode">${options(["QUICK", "BALANCED", "MAX QUALITY"], s.generationMode)}</select></label><label>Image quality preset<select id="settingImagePreset">${options(["Fast Local", "Balanced Quality", "Maximum Quality", "Character Reference", "Image Repair", "Anime/Manga", "Cinematic", "Storybook"], i.preset)}</select></label></div>
  <div class="section-box" id="settings-checks"><h3>Visual checks</h3><div class="two-col"><label>Check level<select id="settingCheckLevel">${options([["off", "Off · no automatic or manual paid checks"], ["sampled", "Sampled · first shot + a stable sample"], ["practical", "Practical · every new image"], ["strict", "Strict · every new image"]], level)}</select></label><label>Sample about one image in<input id="settingSampleEvery" type="number" min="2" max="20" step="1" value="${s.qcSampleEvery ?? 5}"></label><label>How findings affect production<select id="settingQCPolicy">${options([["practical", "Practical · minor differences are advisory"], ["strict", "Strict · detailed review"]], ["practical","strict"].includes(level) ? level : s.qcPolicy || "practical")}</select></label></div><p class="muted">Sampled checks include the first shot of each chapter and planned intro, then select roughly one in N using stable shot IDs. Resuming keeps the same selection. Skipped images stay “Not checked.” Off also overrides MAX QUALITY. Existing AI findings and your image acceptances stay saved.</p><label class="inline"><input id="settingRepair" type="checkbox" ${s.automaticRepair ? "checked" : ""} ${level !== "off" && (level === "strict" || level === "sampled" && s.qcPolicy === "strict") ? "" : "disabled"}>Allow automatic retries in strict review</label><p class="muted">Practical review treats minor pose, framing, expression and prop detail differences as advice. Clear story or identity errors need your review. Strict retries also need a verified estimate for checks, replacement images, rechecks and extra rental within the 10% overhead ceiling. ${escape(project.qcRepairBudget?.reason || "Missing cost evidence keeps automatic repairs on hold.")}</p></div>
  <div class="section-box"><h3>Image generator</h3><label class="inline"><input id="settingFocusedPrompts" type="checkbox" ${s.focusedPrompts === true ? "checked" : ""}>Focused story facts for newly planned images</label><p class="muted">Optional guidance based on the prompt test: keep explicit gender, established people count and prop ownership; shorten repeated reference instructions. Existing shots and manual prompts stay saved. Prompts have no fixed word quota and are never cut off. A universal speed, price or quality winner was not established.</p><div class="two-col"><label>Image provider<select id="settingImageProvider">${options(
    [
      ["existing", "Existing SD fallback / DreamShaper"],
      ["native-flux", "Quantized FLUX.2 Klein 4B"],
      ["comfyui", "ComfyUI · local or cloud through SSH"],
    ],
    i.provider,
  )}</select></label><label>Image model<select id="settingImageModel">${options(models, i.model)}</select></label><label>Workflow<select id="settingImageWorkflow">${options(selected?.workflow || [i.workflow], i.workflow)}</select></label><label>SD reference strength<input ${i.provider === "native-flux" ? "disabled" : ""} id="settingReferenceStrength" type="number" min="0" max="1" step=".05" value="${i.referenceStrength}"></label></div><p class="muted">${selected?.installed ? "Installed" : "Unavailable: configure this local backend before generating."} ${selected?.validated === false ? "This native configuration has not passed laptop validation yet." : ""} ${escape(selected?.capabilities?.referenceLimitations || "")}</p><button id="showModelNotes">Model evaluation and diagnosis</button> <button id="connectCloudImages">Connect Qwen cloud images</button></div>
  <div class="section-box"><h3>Economy storyboard canvases</h3><label class="inline"><input id="settingEconomyPanels" type="checkbox" ${s.economyPanels ? 'checked' : ''}>Four independent shots per Qwen canvas</label><p class="muted">Luna groups the shots; each crop is saved separately as a 640×360 landscape image. This can reduce generation calls by about 75%, with less detail and a risk of composition mixing. Individual full-resolution regeneration remains available. The helper does not start or stop rented GPUs.</p><button id="prepareStory">Prepare complete narration and director plan</button> <button id="prepareRestyle">Restyle existing shots</button></div>
  <div class="section-box" id="settings-voice"><h3>Voice</h3><div class="two-col"><label>Existing Kokoro voice<select id="settingVoice">${options(["am_michael", "am_fenrir", "am_puck", "bm_george", "af_heart", "af_bella", "af_nicole", "bf_emma"], s.voice)}</select></label><label>Speaking speed<input id="settingSpeed" type="number" min=".5" max="2" step=".05" value="${s.speed}"></label><label>Narration delivery<select id="settingNarrationDelivery">${options([["standard", "Standard · sentence timing"], ["cinematic", "Restrained cinematic · connected delivery"]], s.narrationDelivery || "standard")}</select></label><label>Sound effects<select id="settingSoundEffects">${options([["subtle", "Subtle · quieter than narration"], ["off", "Off · brief pauses"]], s.soundEffects || "subtle")}</select></label></div><p class="muted">Unsupported cries such as Ahhh and Aaagghhh become brief pauses. Marked effects and isolated cues such as Thud are never spelled as letters. The source story stays intact.</p><p class="muted">Cinematic delivery gives short connected sentences shared voice context. Sentences containing your emphasis phrases run 4% slower. Kokoro cannot accept acting prompts or guarantee a reference narrator’s pitch or emotion. Grouped sentence times use model duration estimates.</p><label>Emphasis phrases · one per line, up to 12<textarea id="settingEmphasisPhrases" rows="2">${escape((s.emphasisPhrases || []).join("\n"))}</textarea></label><label>Voice audition text<textarea id="voicePreviewText" rows="3">${escape(project.introHookProposal?.text || project.intro.voiceText || "He had already lost everything. This time, he would fight for a second chance.\nAhhh!\n*Slash*\nThud.\nNo! I can't leave you here.")}</textarea></label><div class="toolbar"><button id="voicePreviewGenerate">Preview selected voice</button><button id="voicePreviewRefresh">Refresh auditions</button></div><p class="muted">Exports use AAC at 128 kbps. Auditions use the same encoding when FFmpeg is configured; lossless WAV masters stay saved. Short fades protect speech joins. Bitrate does not change the narrator or remove noise inside a phrase.</p><p class="muted">Local audition only; chapter audio and shot timings stay saved. Listen before regenerating a chapter.</p><div id="voicePreviewResults">${voicePreviewResults()}</div></div>
  <div class="section-box" id="settings-intro"><h3>Optional intro</h3><p class="muted">Off starts directly with Chapter 1. Enabling reuses your saved intro assets and text; generation buttons run only when selected.</p><label class="inline"><input id="introEnabled" type="checkbox" ${project.intro.enabled ? "checked" : ""}>Enable intro</label><div class="two-col"><label>Duration: <span id="introDurationValue">${project.intro.duration}</span> seconds<input id="introDuration" type="range" min="10" max="30" step="1" value="${project.intro.duration}"></label><label>Placement<select id="introPlacement">${options(
    [
      ["full_story_only", "Full story only"],
      ["every_chapter", "Every chapter"],
    ],
    project.intro.placement,
  )}</select></label><label class="inline"><input id="introEndOnNarration" type="checkbox" ${project.intro.endOnNarration !== false ? "checked" : ""}>End with narration · avoid silent gap</label><label class="inline"><input id="introShowTitle" type="checkbox" ${project.intro.showTitle ? "checked" : ""}>Show title text in video</label><label>Optional title<input id="introTitle" value="${escape(project.intro.title)}"></label><label>Subtitle<input id="introSubtitle" value="${escape(project.intro.subtitle)}"></label></div><p class="muted">With End with narration enabled, the selected duration is a maximum: the intro finishes shortly after its audio, with a 10-second minimum. Turn it off for a fixed duration. Editing voice text requires regenerating its separate audio before previewing.</p><p class="muted">For an engaging opening, start with a specific conflict, show why it matters, and leave a story-supported question. Use the shot editor to change timing, prompts, references and motion; saved chapter assets remain available.</p><label>Intro visual description<textarea id="introVisualPrompt" rows="2">${escape(project.intro.visualPrompt || "")}</textarea></label><label>Optional explicit intro voice text<textarea id="introVoiceText" rows="2">${escape(project.intro.voiceText)}</textarea></label><div class="toolbar"><button id="introUpload">Choose background image</button><button id="introGenerate">Generate intro visual</button><button id="introAudio">Generate separate intro voice</button><button id="introPreview">Preview intro</button><button id="editIntroShots">Edit intro shots and references</button></div>${project.intro.shots?.length ? `<div class="shot-grid">${project.intro.shots.map(s => `<article class="shot-card">${s.imagePath ? `<img data-asset="${escape(s.imagePath)}" alt="Intro shot">` : ""}<p>Intro · AI check: ${escape(s.qc?.status || "UNREVIEWED")} · ${escape(qualityLabel(s))}</p>${s.imagePath && s.qc?.pass !== true && qualityDecision(s).disposition !== "accepted" ? `<button data-intro-accept="${s.id}">Use this image · no repair</button>` : ""}</article>`).join("")}</div>` : ""}${project.intro.visualPath ? `<img data-asset="${escape(project.intro.visualPath)}" alt="Intro background" style="max-width:260px;margin-top:12px">` : ""}</div>

  ${engagementForm(project)}
  <div class="section-box" id="settings-watermark"><h3>Watermark</h3><label class="inline"><input id="settingWatermarkEnabled" type="checkbox" ${w.enabled ? "checked" : ""}>Add watermark to chapter, full-story and intro exports</label><div class="two-col"><label>Watermark type<select id="settingWatermarkType">${options([["text","Text"],["image","Uploaded logo"]],w.type)}</select></label><label>Text<input id="settingWatermarkText" maxlength="100" value="${escape(w.text)}"></label><label>Position<select id="settingWatermarkPosition">${options([["top-left","Top left"],["top-right","Top right"],["bottom-left","Bottom left"],["bottom-right","Bottom right"],["center","Center"]],w.position)}</select></label><label>Width (% of video)<input id="settingWatermarkWidth" type="number" min="1" max="50" step="1" value="${w.widthPercent}"></label><label>Opacity (%)<input id="settingWatermarkOpacity" type="number" min="5" max="100" step="1" value="${Math.round(w.opacity*100)}"></label><label>Edge margin (%)<input id="settingWatermarkMargin" type="number" min="0" max="10" step=".5" value="${w.marginPercent}"></label><label>Text color<input id="settingWatermarkColor" type="color" value="${escape(w.color)}"></label></div><input id="settingWatermarkPath" type="hidden" value="${escape(w.imagePath)}"><div class="toolbar"><button id="watermarkUpload">Upload logo</button><button id="watermarkRemove" ${w.imagePath ? "" : "disabled"}>Remove selected logo</button></div><p id="watermarkLogoStatus" class="muted">${w.imagePath ? "Saved logo selected. Transparency is preserved." : "No logo selected. Upload a PNG, JPEG or WebP image."}</p>${w.imagePath ? `<img data-asset="${escape(w.imagePath)}" alt="Selected watermark logo" style="max-width:160px;max-height:100px;margin-top:12px">` : ""}<p class="muted">Applied once to finished exports, including intros within them. Original images and reusable unmarked clips stay saved. Logo changes require rebuilding exports. Watermark rendering uses your chosen video size, frame rate and quality.</p></div>
  <div class="section-box" id="settings-cost"><h3>Spending limits</h3><label>Project API limit (USD)<input id="settingAPIBudget" type="number" min=".001" step=".01" value="${s.budget?.openaiUSD ?? ""}" placeholder="Keep existing provider configuration"></label><p class="muted">The project API ledger includes direction, planning and visual checks. This is separate from GPU rental. ${s.cloudWindow?.gpuBudgetUSD != null ? `Saved GPU window limit: $${escape(s.cloudWindow.gpuBudgetUSD)}.` : "Existing GPU window configuration stays saved. Rental limits also require the actual worker start time and hourly rate."} Pausing Studio does not stop a rented GPU. Settings does not start cloud workers.</p><button id="settingsCloudBudget">Open cloud connection</button></div>
  <details id="settings-video" open><summary>Video output</summary><div class="two-col"><label>Video size<select id="settingVideoSize">${options(
    [
      ["640x360", "640 × 360"],
      ["1280x720", "1280 × 720"],
      ["1920x1080", "1920 × 1080"],
    ],
    s.video.width + "x" + s.video.height,
  )}</select></label><label>Image framing<select id="settingImageFit">${options(
    [
      ["contain", "Preserve entire composition"],
      ["cover", "Fill frame (crop edges)"],
    ],
    s.video.imageFit || "contain",
  )}</select></label><label>Zoom amount (%)<input id="settingZoomAmount" type="number" min="0" max="35" step="1" value="${Math.round((s.video.zoomAmount ?? .06)*100)}"></label><label>Image motion<select id="settingMotionMode">${options(
    [
      ["gentle", "Gentle zooms and pans"],
      ["director", "Use each shot’s motion"],
      ["static", "All images static"],
    ],
    s.video.motionMode || "director",
  )}</select></label><label>Frames per second<select id="settingVideoFps">${options(
    [
      ["24", "24"],
      ["25", "25"],
      ["30", "30"],
      ["60", "60 · smoother motion"],
    ],
    String(s.video.fps),
  )}</select></label></div><div class="toolbar"><label>Local video renderer<select id="localRenderBackend" ${connected ? '' : 'disabled'}>${options([["native","NVIDIA GPU · photo motion"],["cpu","CPU · compatibility"]],health?.renderer?.backend || "cpu")}</select></label><button id="applyRenderBackend" ${connected ? '' : 'disabled'}>Apply renderer</button></div><p class="muted">${escape(health?.renderer?.note || 'Connect the shared helper to choose the local renderer.')} Image generation, narration, projects and previous videos stay saved. GPU mode needs the optional NVIDIA codec library. If it cannot initialize, choose CPU compatibility; completed assets stay saved.</p></details>  <details><summary>Advanced AI settings</summary><div class="two-col"><label>Director provider<select id="settingDirectorProvider">${options(
    [
      ["local-qwen", "Local Qwen3.5-4B Q4_K_M"],
      ["openai-luna", "GPT-6 Luna · OpenAI API"],
    ],
    s.director.provider,
  )}</select></label><label>Director reasoning<select id="settingReasoning">${options(["Fast", "Balanced", "High"], s.director.reasoning)}</select></label><label>Continuity strictness<select id="settingContinuity">${options(["Low", "Medium", "High"], s.continuityStrictness)}</select></label><label>Appearance changes<select id="settingAppearance">${options(["Automatic", "Review changes", "Strict"], s.appearanceHandling)}</select></label><label>Max image retries<input id="settingRetries" type="number" min="0" max="10" value="${s.maxImageRetries}"></label><label>Resolution<select id="settingResolution">${options(
    [
      ["384x384", "384 × 384 · references / repair"],
      ["448x448", "448 × 448 · native balanced"],
      ["512x512", "512 × 512 · SD fallback"],
      ["768x512", "768 × 512"],
      ["512x768", "512 × 768"],
      ["1344x768", "1344 × 768 · cloud landscape"],
      ["1024x1024", "1024 × 1024 · cloud square"],
    ],
    i.width + "x" + i.height,
  )}</select></label><label>Steps<input id="settingSteps" type="number" min="1" max="50" value="${i.steps}"></label><label>Guidance<input id="settingGuidance" type="number" min="1" max="14" step=".5" value="${i.guidance}"></label><label>Sampler<input id="settingSampler" value="${escape(i.sampler)}"></label><label>Scheduler<input id="settingScheduler" value="${escape(i.scheduler)}"></label></div><label class="inline"><input id="settingFallback" type="checkbox" ${i.fallbackEnabled ? "checked" : ""}>Enable explicit SD 1.5 fallback when the chosen provider fails</label><button id="editCustomLayout">Edit custom pacing targets</button> <button id="editImageSettings">Edit conditioning / LoRA / full generation settings</button> <button id="configureCloud">Cloud setup · Luna + Runpod</button> <button id="configureRuntimes">Configure local runtime paths</button><p class="muted">Unsupported settings are rejected before jobs are queued. No silent model substitution.</p></details><button class="primary" id="productionSaveSettings">Save project settings</button><p id="settingsDraftStatus" class="muted" role="status"></p>`;
}
function shotMotionLabel(shot) {
  const mode = project.settings.video.motionMode || "director";
  if (mode === "static") return "static";
  const motion = shot.motion || "static";
  if (mode !== "gentle" || shot.manual?.motion || motion !== "static")
    return motion;
  const camera = (shot.camera?.shot || "").toLowerCase();
  const duration = shot.end - shot.start;
  if (
    duration < 2 ||
    camera.includes("insert") ||
    camera.includes("extreme close")
  )
    return "static";
  if (
    duration >= 8 &&
    (camera.includes("establishing") || camera.includes("extreme wide"))
  )
    return "pan right";
  if (camera.includes("close") || camera.includes("reaction"))
    return "slow zoom out";
  return "slow zoom in";
}
function renderQueue() {
  const displayedProject=project.id;
  const el = $("#productionQueue");
  if (!el) return;
  const openDetails = new Set([...root.querySelectorAll('details[data-ui][open]')].map(detail => detail.dataset.ui));
  if (!queue) {
    el.innerHTML =
      '<p class="muted">Connect the helper to see production jobs. Completed assets stay saved after refresh.</p>';
    return;
  }
  const fullButton = $("#productionFullVideo");
  const activeFullRun = queue.jobs.find(
    (j) =>
      j.project === project.id &&
      ["produce-story", "render-full"].includes(j.kind) &&
      ["QUEUED", "RUNNING"].includes(j.status),
  );
  if (fullButton) {
    fullButton.disabled = !connected || startingProduction.has(project.id) || !!activeFullRun || !!project.production?.budgetBlocked;
    fullButton.textContent = activeFullRun
      ? queue.paused ? "Full video paused" : "Full video in progress"
      : startingProduction.has(project.id) ? "Checking and starting…" : "Generate full video";
  }
  const run = project.production,
    runStatus = $("#fullVideoStatus");
  if (runStatus && run) {
    const reviewCount = progressReader.read(project).blocking;
    const ready =
      run.status === "COMPLETE" && !!project.render?.path && !project.renderStale && !activeFullRun;
    const status =
      run.budgetBlocked
        ? run.message || "Cloud budget reached · queue paused · stop the Runpod pod"
        : queue.paused && activeFullRun
        ? "Paused · saved results preserved"
        : activeFullRun?.kind === "render-full"
        ? activeFullRun.message || "Rendering full story"
        : ready
          ? "Full video ready" + (reviewCount ? ` · ${reviewCount} shots need review` : "")
          : run.status === "COMPLETE" && project.render?.path && project.renderStale
            ? "Video settings changed · render the full story to apply"
            : ["FAILED", "CANCELLED"].includes(run.status)
              ? run.status + " · " + run.message
              : run.stage || run.message || run.status;
    const delivery=ready?videoDeliveryStatus(project,queue.storage):null;
    runStatus.innerHTML = `<span>${escape(status)}${delivery?' · '+escape(delivery.label):''}</span>${ready ? '<button class="video-result-button" id="openFullVideo">Open finished video</button>' : ""}${delivery?.retry?'<button id="retryVideoCloudSave">Retry cloud save</button>':''}`;
    $('#retryVideoCloudSave')?.addEventListener('click',()=>action(async()=>{
      await api('storage-sync',{project:project.id});queue=await api('queue');renderQueue();
    }));
    const open = $("#openFullVideo");
    if (open)
      open.onclick = () =>
        action(async () => {
          tab = "timeline";
          render();
        });
    if (activeFullRun?.started && activeFullRun.kind === "produce-story") {
      const elapsed = Math.max(0, Date.now() / 1000 - activeFullRun.started);
      const samples = (run.timings || [])
        .filter(
          (t) =>
            t.stage === "Image + quality checks" &&
            t.status === "COMPLETE" &&
            t.chapter === run.chapterNumber,
        )
        .slice(-5);
      let remaining =
        "Full-story ETA is being measured as chapters are planned.";
      if (
        samples.length >= 2 &&
        run.shotId &&
        run.totalImages > run.completedImages
      ) {
        const average =
          samples.reduce((sum, item) => sum + item.seconds, 0) / samples.length;
        const seconds = Math.max(
          average,
          (run.totalImages - run.completedImages) * average -
            Math.max(0, Date.now() / 1000 - run.updated),
        );
        remaining = `Images remaining in this chapter: approximately ${time(seconds * 0.8)}–${time(seconds * 1.25)}. Later chapters and rendering take additional time.`;
      }
      runStatus.insertAdjacentHTML(
        "beforeend",
        `<p class="muted">This attempt: ${time(elapsed)} elapsed. ${remaining}</p>`,
      );
    }
    if (run.timings?.length) {
      let costSummary = "";
      if (run.costs) {
        const gpu = Number.isFinite(run.costs.gpuWindowEstimatedUSD)
          ? '$' + run.costs.gpuWindowEstimatedUSD.toFixed(4) : 'not recorded';
        costSummary = `<p class="muted">Estimated run cost: OpenAI $${Number(run.costs.apiEstimatedUSD || 0).toFixed(4)} · GPU window ${gpu}. Storage and account billing are reported separately.</p>`;
      }
      const receipt = run.completionReceipt;
      if (Number.isFinite(receipt?.balancePlusAPIEstimateUSD)) {
        costSummary += `<p class="muted">Completion snapshot, including earlier rejected work and account storage/settlement: $${receipt.balancePlusAPIEstimateUSD.toFixed(2)}. Recorded ${escape(new Date(receipt.verifiedAt * 1000).toLocaleString())}. Retained storage continues billing while GPUs are stopped.</p>`;
      }
      const entries = run.timings.filter((t) => !t.detail),
        totals = {}, scopes = {};
      for (const entry of entries) {
        totals[entry.stage] = (totals[entry.stage] || 0) + entry.seconds;
        const scope = entry.chapter ? 'Chapter ' + entry.chapter : 'Project / intro';
        const breakdown = scopes[entry.stage] ||= {};
        breakdown[scope] = (breakdown[scope] || 0) + entry.seconds;
      }
      runStatus.insertAdjacentHTML(
        "beforeend",
        `<details data-ui="production-times"><summary>Costs and measured times</summary>${costSummary}<p class="muted">Recorded stage wall time includes loading, retries and pauses. Reused assets take only their validation time. Director passes are details within directing; GPU rental overlaps generation. Do not add either to the stage totals again.</p><table><thead><tr><th>Stage</th><th>Total time</th><th>By chapter</th></tr></thead><tbody>${Object.entries(
          totals,
        )
          .map(
            ([stage, seconds]) =>
              `<tr><td>${escape(stage)}</td><td>${time(seconds)}</td><td>${Object.entries(scopes[stage]).map(([scope, value]) => escape(scope) + ': ' + time(value)).join('<br>')}</td></tr>`,
          )
          .join(
            "",
          )}</tbody></table><details data-ui="production-passes"><summary>Every stage and director pass</summary>${run.timings.map((t) => `<p class="muted">${t.chapter ? "Chapter " + t.chapter + " · " : ""}${escape(t.stage)}${t.shot ? " · " + escape(t.shot) : ""} · ${t.seconds.toFixed(1)} s${t.reused ? " · reused" : ""}${t.status === "FAILED" ? " · failed attempt" : ""}</p>`).join("")}</details></details>`,
      );
    }
  }
  const jobs = queue.jobs.filter((j) => j.project === project.id),
    counts = queue.projectCounts?.[project.id],
    done =
      counts?.all.COMPLETE ??
      jobs.filter((j) => j.status === "COMPLETE").length,
    failed = jobs.filter((j) => j.status === "FAILED"),
    pending =
      counts?.all.QUEUED ?? jobs.filter((j) => j.status === "QUEUED").length,
    current = jobs.find((j) => j.id === queue.current),
    otherCurrent = queue.jobs.some(j=>j.id===queue.current&&j.project!==project.id),
    progress = progressReader.read(project),
    imageDone = progress.imageDone,
    imageTotal = progress.imageTotal,
    total = counts
      ? Object.values(counts.all).reduce((a, b) => a + b, 0)
      : jobs.length,
    failureCount = progress.failures,
    currentShot = shots(
      project.chapters.find((c) => c.id === current?.chapter),
    ).find((s) => s.id === current?.shot),
    currentScene = project.chapters
      .find((c) => c.id === current?.chapter)
      ?.scenes.find((s) => s.id === currentShot?.sceneId);
  el.innerHTML = `<div class="toolbar"><h3 style="flex:1">Production queue${imageTotal ? " · " + imageDone + " / " + imageTotal + " images saved" : ""}</h3><span class="muted">${pending} jobs waiting in this project · ${failureCount} images need attention · Shared queue ETA ${queue.paused ? "Paused" : queue.etaStatus === "LONGER_THAN_HISTORY" ? "Longer than earlier runs" : queue.etaSeconds == null ? "Measuring" : time(queue.etaSeconds)}</span></div><p class="muted" role="status">${queue.paused ? "PAUSED · " : ""}${escape((currentShot && currentScene ? "Scene " + (project.chapters.find((c) => c.id === current.chapter).scenes.indexOf(currentScene) + 1) + " — Shot " + (currentScene.shots.indexOf(currentShot) + 1) + " · " : "") + (current?.message || (otherCurrent?"Another project is using the shared helper; this project waits.":"Ready")))}</p><progress aria-label="Images saved in this project" max="${Math.max(1, imageTotal)}" value="${imageDone}"></progress><div class="toolbar"><button data-control="pause" ${queue.paused ? "disabled" : ""}>Pause</button><button data-control="resume" ${queue.paused ? "" : "disabled"}>Resume</button><button data-control="cancel-current" ${current ? "" : "disabled"}>${current?.kind === "produce-story" ? "Cancel full run" : "Cancel current"}</button><button data-control="cancel-all" ${current || pending ? "" : "disabled"}>Cancel project queue</button><button data-control="retry-missing" ${imageDone === imageTotal ? "disabled" : ""}>Retry missing images</button></div><p class="muted">Pause and Resume affect the shared helper queue. Cancel project queue stops only this project’s work; other projects and completed assets are retained.</p><details data-ui="job-history"><summary>Job history · ${done} completed attempts · ${failed.length} failed attempts</summary><div class="queue-jobs">${jobs
    .filter(
      (j) =>
        j.status === "FAILED" ||
        j.status === "CANCELLED" ||
        j.status === "RUNNING" ||
        j.status === "QUEUED",
    )
    .slice(0, 30)
    .map(
      (j) =>
        `<div class="queue-job"><span>${escape(j.kind)} · ${escape(j.message)}</span><button data-job-priority="${j.id}">Prioritize</button>${["FAILED", "CANCELLED"].includes(j.status) ? `<button data-job-retry="${j.id}">Retry same seed</button>` : ""}</div>`,
    )
    .join("")}</div></details>`;
  for (const detail of root.querySelectorAll('details[data-ui]')) {
    if (openDetails.has(detail.dataset.ui)) detail.open = true;
  }
  el.insertAdjacentHTML("beforeend", '<button id="downloadProductionTrace">Download timing report</button>');
  const executions = (queue.executions || []).filter(item => item.project === project.id && item.kind === "image-qc");
  if (executions.length) {
    el.insertAdjacentHTML("beforeend", `<details data-ui="cloud-tasks"><summary>Cloud tasks · ${executions.filter(item => item.status === "RUNNING").length} active</summary>${executions.map(item => `<p class="muted">${escape(item.message || item.status)}${item.status === "UNKNOWN" ? " · Needs reconciliation before retry" : ""}${item.status === "RUNNING" ? ` <button data-control="cancel-current" data-execution="${escape(item.id)}">Cancel this task</button>` : ""}</p>`).join("")}</details>`);
  }
  el.querySelector("#downloadProductionTrace").onclick = () => action(async () => {
    const trace = await api("trace?project=" + encodeURIComponent(displayedProject));
    downloadBlob(new Blob([JSON.stringify(trace, null, 2)], {type: "application/json"}), "studio-timing-report.json");
  });
  el.querySelectorAll("[data-control]").forEach(
    (b) =>
      (b.onclick = () =>
        action(async () => {
          queue = await api("control", { action: b.dataset.control, project: displayedProject,
            ...(b.dataset.execution ? {job: b.dataset.execution} : b.dataset.control==='cancel-current'&&current ? {job:current.id} : {}) });
          renderQueue();
        })),
  );
  el.querySelectorAll("[data-job-priority]").forEach(
    (b) =>
      (b.onclick = () =>
        action(() =>
          api("control", { action: "priority", job: b.dataset.jobPriority }),
        )),
  );
  el.querySelectorAll("[data-job-retry]").forEach(
    (b) =>
      (b.onclick = () =>
        action(() =>
          api("control", { action: "retry", job: b.dataset.jobRetry }),
        )),
  );
}
async function submit(kind, selected, options) {
  const targetProject=project.id,targetChapter=chapterId;
  await saveSettingsIfVisible();
  if(project.id!==targetProject||chapterId!==targetChapter)throw Error('The selected chapter changed. Choose the operation again in the current chapter.');
  queue = await api("jobs", {
    project: project.id,
    chapter: chapterId,
    kind,
    shots: selected,
    options,
  });
  renderQueue();
  note("Added to the production queue. Completed results save immediately.");
}
function wire() {
  if(tab==='files') {
    const filesProject=project;
    const filesAction=fn=>action(()=>{
      if(tab!=='files'||project?.id!==filesProject.id)return;
      return fn();
    });
    void filesAction(()=>wireFiles({p:filesProject,api,action:filesAction,note,
      media:path=>media(path,filesProject.id),
      submit:(kind,options)=>submit(kind,undefined,options),
      reload:async()=>{
        const before=project;
        const updated=await api('project?id='+filesProject.id);
        if(project!==before||project?.id!==filesProject.id||dirty)return;
        project=updated;mediaCache.clear();render();
      }}));
  }
  $('#prepareOutro')?.addEventListener('click',()=>action(async()=>{await saveSettings();await submit('outro-audio');}));
  $("#productionOverlap").onchange = (event) => { overlapRequested = event.target.checked; };
  const checkReadiness=async()=>{
    const targetProject=project.id;
    await saveSettingsIfVisible();
    if(project.id!==targetProject)throw Error('Project selection changed. Check the current project again.');
    const overlap=!!$("#productionOverlap")?.checked;
    const readiness=await api("production-readiness?project="+encodeURIComponent(project.id)+"&overlap="+overlap);
    if(project.id!==targetProject)throw Error('Project selection changed. Check the current project again.');
    $("#productionReadiness").innerHTML=`<details open><summary>${readiness.ready?'Ready to start':'Setup needs attention'}</summary><ul>${readiness.checks.map(x=>`<li>${x.ready?'✓':x.blocking?'Required:':'Note:'} ${escape(x.message)}</li>`).join('')}</ul><p class="muted">These checks do not generate images, rent a GPU or spend API tokens.</p></details>`;
    return readiness;
  };
  $("#productionCheckReadiness").onclick=()=>action(checkReadiness);
  $("#productionFullVideo").onclick = () =>
    action(async () => {
      const targetProject=project.id;
      if(startingProduction.has(targetProject))return;
      startingProduction.add(targetProject);renderQueue();
      try{
        const readiness=await checkReadiness();
        await flush();
        if(project.id!==targetProject)throw Error('Project selection changed. Start generation again in the intended project.');
        const overlap=!!$("#productionOverlap")?.checked;
        if(!readiness.ready){
          const issues=readiness.checks.filter(x=>x.blocking&&!x.ready).map(x=>x.message);
          throw Error('Before generation: '+issues.join(' '));
        }
        queue=await api('jobs',{project:targetProject,kind:'produce-story',options:{overlap,preflightRevision:readiness.revision}});
        note('Full video queued. Keep the shared helper running. Pause, cancel or retry here; completed work is saved and reused.');
      }finally{startingProduction.delete(targetProject);renderQueue();}
    });
  $("#productionConnect").onclick = () => action(connectHelper);
  $("#productionNewProject").onclick = () => action(newProject);
  $("#productionProject").onchange = event => {
    const wanted=event.target.value;
    const request=projectSelection.begin(project,editorSave.revision);
    void action(async()=>{
      request.project=project;request.editorRevision=editorSave.revision;
      let selected;
      try{selected=connected?await api('project?id='+encodeURIComponent(wanted)):await loadStudioProject(wanted);}
      catch(error){if(!projectSelection.current(request,project,editorSave.revision))return;event.target.value=project.id;throw error;}
      if(!projectSelection.current(request,project,editorSave.revision)){
        if(projectSelection.latest(request)&&request.project===project&&editorSave.revision!==request.editorRevision){event.target.value=project.id;note('Your new edits are retained. Select the other project again when you are ready.');}
        return;
      }
      if(!selected?.chapters?.length){event.target.value=project.id;throw new Error('This project is saved on the helper. Reconnect the helper, then select it again.');}
      project=selected;chapterId=project.chapters[0].id;scenePage=0;
      await cache();render();
    });
  };
  root.querySelectorAll("[data-chapter]").forEach(
    (b) =>
      (b.onclick = () =>
        action(async () => {
          chapterId = b.dataset.chapter;
          scenePage = 0;
          render();
        })),
  );
  root.querySelectorAll("[data-tab],[data-go]").forEach(
    (b) =>
      (b.onclick = () =>
        action(async () => {
          tab = b.dataset.tab || b.dataset.go;
          render();
          root.querySelector('[role="tab"][aria-selected="true"]')?.focus({preventScroll:true});
        })),
  );
  root.querySelectorAll('[role="tab"]').forEach(button => {
    button.onkeydown = event => {
      const buttons = [...root.querySelectorAll('[role="tab"]')];
      const index = buttons.indexOf(button);
      const target = event.key === 'ArrowRight' ? (index + 1) % buttons.length
        : event.key === 'ArrowLeft' ? (index + buttons.length - 1) % buttons.length
        : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : null;
      if (target === null) return;
      event.preventDefault();
      void action(async () => {
        tab = buttons[target].dataset.tab;
        render();
        root.querySelector('[role="tab"][aria-selected="true"]').focus();
      });
    };
  });
  $("#productionAddChapter").onclick = () =>
    action(async () => {
      if (connected) {
        await mutate("chapter", { action: "add" });
        chapterId = project.chapters.at(-1).id;
      } else {
        project.chapters.push(offlineChapter(project.chapters.length + 1));
        chapterId = project.chapters.at(-1).id;
        project._unsynced = true;
        await cache();
      }
      tab = "write";
      render();
    });
  $("#productionDuplicate").onclick = () =>
    action(() => mutate("chapter", { id: chapterId, action: "duplicate" }));
  $("#productionMoveUp").onclick = () =>
    action(() =>
      mutate("chapter", { id: chapterId, action: "move", direction: -1 }),
    );
  $("#productionMoveDown").onclick = () =>
    action(() =>
      mutate("chapter", { id: chapterId, action: "move", direction: 1 }),
    );
  $("#productionDelete").onclick = () =>
    confirmAction(
      "Delete this chapter from the project? Generated files remain on disk and in project backups.",
      () => mutate("chapter", { id: chapterId, action: "delete" }),
    );
  $("#productionBackup").onclick = () => {
    try {
      // Export must remain available even when browser or helper saving fails.
      const backup=backupSnapshot(project,chapterId,tab,{
        sourceText:$('#chapterStory')?.value,name:$('#chapterName')?.value,
        cleanNarrationText:$('#narrationScript')?.value,narrationMode:$('#narrationMode')?.value,
        includeChapterLabel:$('#includeChapterHeading')?.checked,
      });
      downloadBlob(new Blob([JSON.stringify(backup,null,2)],{type:'application/json'}),project.name+'.json');
      note('Project JSON exported with the current editor text and saved generation settings. Media files remain in their existing helper/cloud archive.'+(settingsDrafts.has(project.id)?' Unsaved settings are still in this page; Save project settings to include them in the next export.':''));
    } catch(error){note(error.message,true);}
  };
  $("#productionImport").onclick = () => $("#productionImportFile").click();
  $("#productionImportFile").onchange = (e) =>
    action(async () => {
      const p = JSON.parse(await e.target.files[0].text());
      if (p.schemaVersion !== 1 || !p.chapters)
        throw new Error("Choose a studio project JSON.");
      project = connected ? await api("import", { project: p }) : p;
      chapterId = project.chapters[0].id;
      await cache();
      await refreshProjects();
      render();
    });
  $("#syncOffline")?.addEventListener("click", () =>
    confirmAction(
      "Sync this browser’s offline project? The helper’s current JSON will be backed up first. Existing asset files remain saved.",
      async () => {
        const p = { ...project };
        delete p._unsynced;
        project = await api("import", { project: p });
        await cache();
        render();
      },
    ),
  );
  $("#chapterStory")?.addEventListener("input", dirtyEditor);
  $("#chapterName")?.addEventListener("input", dirtyEditor);
  $("#narrationScript")?.addEventListener("input", () => {
    $("#narrationMode").value = "manual";
    dirtyEditor();
  });
  $("#narrationMode")?.addEventListener("change", dirtyEditor);
  $("#includeChapterHeading")?.addEventListener("change", dirtyEditor);
  $("#productionReviewNarration")?.addEventListener("click", () =>
    action(async () => {
      tab = "narration";
      render();
    }),
  );
  $("#productionClean")?.addEventListener("click", () =>
    action(async () => {
      await patch(
        "chapter",
        chapterId,
        {
          narrationMode: "automatic",
          cleanNarrationText: cleanText(ch().sourceText, ch().name),
        },
        true,
      );
    }),
  );
  $("#productionAnalyze")?.addEventListener("click", () =>
    action(() => submit("analyze")),
  );
  $("#productionNarrate")?.addEventListener("click", () =>
    action(() => submit("narration")),
  );
  $("#productionExample")?.addEventListener("click", () =>
    confirmAction(
      "Create a separate two-chapter test project? Your current project stays saved.",
      testStory,
    ),
  );
  $("#productionLegacy")?.addEventListener("click", () =>
    action(async () => {
      const legacy = await loadProjectState();
      if (!legacy?.story)
        throw new Error("No existing comic story found in this browser.");
      project = await api("create", {
        name: legacy.name || "Imported story",
        legacy,
      });
      chapterId = project.chapters[0].id;
      await refreshProjects();
      await cache();
      render();
    }),
  );
  $("#productionGenerate")?.addEventListener("click", () =>
    action(() =>
      submit(
        "image",
        shots()
          .filter((s) => !s.imagePath)
          .map((s) => s.id),
      ),
    ),
  );
  $("#productionRetryFailed")?.addEventListener("click", () =>
    action(() =>
      submit(
        "image",
        shots()
          .filter((s) => s.status === "FAILED")
          .map((s) => s.id),
      ),
    ),
  );
  $("#productionAutoContinue")?.addEventListener("change", (e) =>
    action(() =>
      patch("project", project.id, {
        settings: { ...project.settings, autoContinue: e.target.checked },
      }),
    ),
  );
  $("#applyProposedPlan")?.addEventListener("click", () =>
    confirmAction(
      "Apply the new plan and replace the current manual layout? The old plan remains in history.",
      () => mutate("chapter", { action: "apply-plan", id: chapterId }),
    ),
  );
  root
    .querySelectorAll("[data-accept-changes]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          action(() =>
            patch(
              "scene",
              b.dataset.acceptChanges,
              { appearanceChangesReviewed: true },
              true,
            ),
          )),
    );
  $("#scenePrevious")?.addEventListener("click", () => {
    scenePage--;
    render();
  });
  $("#sceneNext")?.addEventListener("click", () => {
    scenePage++;
    render();
  });
  root
    .querySelectorAll("[data-shot]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          action(() => shotAction(b.dataset.shot, b.dataset.shotAction))),
    );
  root
    .querySelectorAll("[data-scene]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          action(() => sceneAction(b.dataset.scene, b.dataset.sceneAction))),
    );
  root.querySelectorAll("[data-shot-cast]").forEach(
    (b) =>
      (b.onchange = () =>
        action(async () => {
          const s = shots().find((s) => s.id === b.dataset.shotCast),
            selected = [
              ...root.querySelectorAll(`[data-shot-cast="${s.id}"]:checked`),
            ].map((input) => {
              const p = people().find((p) => p.id === input.value);
              return {
                id: p.id,
                type: p.type,
                appearanceState:
                  s.characters.find((c) => c.id === p.id)?.appearanceState ||
                  p.currentAppearance ||
                  p.defaultAppearance ||
                  {},
              };
            });
          await patch("shot", s.id, { characters: selected }, true);
        })),
  );
  root
    .querySelectorAll("[data-person]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          action(() => personAction(b.dataset.person, b.dataset.personAction))),
    );
  $("#productionAddCharacter")?.addEventListener("click", () =>
    formDialog(
      "Add main character",
      '<label>Name<input id="newPersonName"></label><label>Description<textarea id="newPersonDescription" rows="3"></textarea></label>',
      async () => {
        await mutate("character", {
          action: "add",
          name: $("#newPersonName").value,
          description: $("#newPersonDescription").value,
        });
      },
    ),
  );
  root.querySelectorAll("[data-location]").forEach(
    (b) =>
      (b.onclick = () =>
        action(async () => {
          const file = await chooseFile();
          if (!file) return;
          const asset = await upload(file),
            locations = structuredClone(project.locations);
          locations
            .find((l) => l.id === b.dataset.location)
            .references.push({ path: asset.path, kind: "location" });
          await patch("project", project.id, { locations }, true);
        })),
  );
  $("#editLocations")?.addEventListener("click", () =>
    jsonDialog("Locations", project.locations, (v) =>
      patch("project", project.id, { locations: v }, true),
    ),
  );
  $("#editContinuity")?.addEventListener("click", () =>
    jsonDialog("Chapter handoff and continuity", ch().handoff, (v) =>
      patch("chapter", chapterId, { handoff: v }, true),
    ),
  );
  $("#editChapterPlan")?.addEventListener("click", () =>
    jsonDialog("Editable scene plan", ch().scenes, (v) =>
      patch("chapter", chapterId, { scenes: v }, true),
    ),
  );
  $("#productionRenderChapter")?.addEventListener("click", () =>
    action(() => submit("render-chapter")),
  );
  $("#productionRenderFull")?.addEventListener("click", () =>
    action(() => submit("render-full")),
  );
  $("#validateTimeline")?.addEventListener("click", () => {
    let cursor = 0;
    const issues = [];
    for (const s of shots()) {
      if (Math.abs(s.start - cursor) > 0.08)
        issues.push(
          `Gap or overlap before ${s.id}: ${s.start.toFixed(2)} vs ${cursor.toFixed(2)} seconds`,
        );
      cursor = s.end;
    }
    if (Math.abs(cursor - (ch().audio.duration || 0)) > 0.08)
      issues.push("Timeline does not end with narration.");
    note(
      issues.join("\n") ||
        "Timeline covers the chapter narration with no gaps or overlaps.",
      !!issues.length,
    );
  });
  $("#productionDownloadAudio")?.addEventListener("click", () =>
    action(() => downloadAsset(ch().audio.path, ch().audio.downloadName)),
  );
  $("#downloadChapterVideo")?.addEventListener("click", () =>
    action(() => downloadAsset(ch().render.path, ch().render.downloadName)),
  );
  $("#downloadFullVideo")?.addEventListener("click", () =>
    action(() =>
      downloadAsset(project.render.path, project.render.downloadName),
    ),
  );
  root.querySelectorAll("[data-warning]").forEach(
    (b) =>
      (b.onclick = () =>
        action(() =>
          mutate("warning", {
            id: b.dataset.warning,
            action: b.dataset.choice,
          }),
        )),
  );
  root.querySelectorAll("[data-intro-accept]").forEach(b => b.onclick = () => action(() => {
    if (!connected) throw new Error("Connect the helper to accept an intro image.");
    const s=project.intro.shots.find(s=>s.id===b.dataset.introAccept);
    return mutate("review",{shot:s.id,chapter:s.chapterId || chapterId,imagePath:s.imagePath,imageSHA256:s.imageMetadata?.imageSHA256 || s.qc?.checkedImageSHA256 || "", reviewSpecSignature:s.qcReviewSignature});
  }));
  if (tab === "settings") wireSettings();
}
async function shotAction(sid, act) {
  const s = shots().find((s) => s.id === sid);
  if (act === "generate") return submit("image", [sid]);
  if (act === "qc") return submit("qc", [sid]);
  if (act === "accept-image") {
    if (!connected) throw new Error("Connect the helper to accept the current image; AI findings will remain saved.");
    return mutate("review", {shot:sid, chapter:s.chapterId, imagePath:s.imagePath,
      imageSHA256:s.imageMetadata?.imageSHA256 || s.qc?.checkedImageSHA256 || "", reviewSpecSignature:s.qcReviewSignature});
  }
  if (act === "none") return patch("shot", sid, { characters: [] }, true);
  if (act === "edit") {
    editShot(s);
    return;
  }
  if (act === "repair") {
    if (!project.qcRepairBudget?.allowed) throw new Error(project.qcRepairBudget?.reason || "Repair cost overhead is unverified; no paid repair has been queued.");
    const caps = health?.providers[s.imageProvider]?.capabilities;
    if (!caps?.supportsImageEditing && !caps?.supportsInpainting)
      throw new Error(
        "This workflow cannot edit images. Choose an editing provider in the shot editor.",
      );
    formDialog(
      "Repair this shot",
      `<label>Targeted repair instruction<textarea id="repairPrompt" rows="3">${escape(s.prompt)}</textarea></label>${s.imageProvider === "existing" ? '<label>White = repair, black = keep<input id="repairMask" type="file" accept="image/*"></label>' : '<p class="muted">The current image is the source; identity references are attached when supported.</p>'}`,
      async () => {
        await patch(
          "shot",
          sid,
          { prompt: $("#repairPrompt").value, sourceImagePath: s.imagePath },
          false,
        );
        const file = $("#repairMask")?.files[0];
        if (s.imageProvider === "existing" && !file)
          throw new Error(
            "SD repairs require a matching-size black/white mask.",
          );
        await submit("image", [sid], {
          operation: s.imageProvider === "existing" ? "inpaint" : "edit",
          budgetedQCRepair: true,
          mask: file ? await toDataURL(file) : undefined,
        });
      },
    );
    return;
  }
  if (act === "delete") {
    confirmAction(
      "Delete this shot? Its image files remain saved.",
      async () => {
        const scenes = structuredClone(ch().scenes),
          scene = scenes.find((x) => x.id === s.sceneId);
        if (scene.shots.length === 1)
          throw new Error(
            "A scene needs one shot. Merge it with another scene before deleting.",
          );
        const index = scene.shots.findIndex((x) => x.id === sid);
        if (index) scene.shots[index - 1].end = s.end;
        else scene.shots[1].start = s.start;
        scene.shots = scene.shots.filter((x) => x.id !== sid);
        await patch("chapter", chapterId, { scenes }, true);
      },
    );
  }
}
async function sceneAction(sid, act) {
  const scene = ch().scenes.find((s) => s.id === sid);
  if (act === "edit") {
    const value = structuredClone(scene);
    delete value.id;
    jsonDialog("Scene editor", value, (v) => patch("scene", sid, v, true));
    return;
  }
  if (act === "plan") {
    queue = await api("jobs", {
      project: project.id,
      chapter: chapterId,
      kind: "scene-plan",
      options: { sceneId: sid },
    });
    renderQueue();
    return;
  }
  const scenes = structuredClone(ch().scenes),
    index = scenes.findIndex((s) => s.id === sid),
    target = scenes[index];
  if (act === "add") {
    const old = target.shots.at(-1),
      middle = (old.start + old.end) / 2,
      newShot = structuredClone(old);
    old.end = middle;
    newShot.id = id("shot-");
    newShot.start = middle;
    newShot.imagePath = "";
    newShot.status = "READY_FOR_IMAGES";
    newShot.origin = "MANUAL";
    newShot.manual = { action: true };
    target.shots.push(newShot);
  } else if (act === "split") {
    if (target.shots.length < 2)
      throw new Error("Add a second shot before splitting a scene.");
    const split = Math.floor(target.shots.length / 2),
      newScene = structuredClone(target);
    newScene.id = id("scene-");
    newScene.shots = target.shots.splice(split);
    newScene.start = newScene.shots[0].start;
    target.end = target.shots.at(-1).end;
    newScene.shots.forEach((s) => (s.sceneId = newScene.id));
    scenes.splice(index + 1, 0, newScene);
  } else if (act === "merge") {
    if (index === scenes.length - 1)
      throw new Error("This is the final scene.");
    const next = scenes[index + 1];
    next.shots.forEach((s) => (s.sceneId = target.id));
    target.shots.push(...next.shots);
    target.end = next.end;
    scenes.splice(index + 1, 1);
  } else if (act === "up" || act === "down") {
    const next = index + (act === "up" ? -1 : 1);
    if (next < 0 || next >= scenes.length) return;
    scenes.splice(next, 0, scenes.splice(index, 1)[0]);
    let cursor = 0;
    for (const scene of scenes) {
      scene.start = cursor;
      for (const shot of scene.shots) {
        const duration = shot.end - shot.start;
        shot.start = cursor;
        shot.end = cursor + duration;
        cursor = shot.end;
      }
      scene.end = cursor;
    }
  }
  await patch("chapter", chapterId, { scenes }, true);
}
async function personAction(pid, act) {
  const p = people().find((p) => p.id === pid),
    main = project.characters.some((x) => x.id === pid);
  if (act === "edit") {
    editPerson(p, main);
    return;
  }
  if (act === "reference") {
    formDialog(
      "Add character reference",
      '<label>Reference type<select id="referenceKind">' +
        options(
          [
            "face",
            "front",
            "three-quarter",
            "side",
            "full-body",
            "back",
            "expression",
          ],
          "face",
        ) +
        '</select></label><label>Image<input id="characterReferenceFile" type="file" accept="image/*"></label>',
      async () => {
        const file = $("#characterReferenceFile").files[0];
        if (!file) throw new Error("Choose a reference image.");
        const uploaded = await upload(file);
        await patch(
          main ? "character" : "person",
          pid,
          {
            references: [
              ...p.references,
              { path: uploaded.path, kind: $("#referenceKind").value },
            ],
          },
          true,
        );
      },
    );
    return;
  }
  if (act === "generate-reference") {
    formDialog(
      "Generate a character reference",
      '<label>Reference view<select id="generatedReferenceKind">' +
        options(
          [
            "face",
            "front",
            "three-quarter",
            "side",
            "full-body",
            "back",
            "expression",
          ],
          "face",
        ) +
        "</select></label>",
      async () => {
        queue = await api("jobs", {
          project: project.id,
          kind: "character-reference",
          options: {
            characterId: pid,
            referenceKind: $("#generatedReferenceKind").value,
          },
        });
        renderQueue();
      },
    );
    return;
  }
  if (act === "promote")
    return mutate("character", {
      action: "promote",
      id: pid,
      chapter: chapterId,
    });
  if (act === "accept")
    return patch(main ? "character" : "person", pid, { accepted: true }, true);
  if (act === "attach") {
    if (!project.characters.length)
      throw new Error("Add or promote a main character first.");
    formDialog(
      "Attach detected person to main character",
      '<label>Main character<select id="attachPerson">' +
        options(
          project.characters.map((c) => [c.id, c.name]),
          project.characters[0].id,
        ) +
        "</select></label>",
      () =>
        mutate("character", {
          action: "attach",
          id: pid,
          chapter: chapterId,
          target: $("#attachPerson").value,
        }),
    );
    return;
  }
  if (act === "remove") {
    if (main)
      confirmAction(
        "Remove this main character from the library? Existing images stay saved.",
        () => mutate("character", { action: "remove", id: pid }),
      );
    else await patch("person", pid, { accepted: false, removed: true }, true);
  }
}
function formDialog(title, body, onSave) {
  const projectId=project?.id;
  return openFormDialog({document,title,body,escape,onSave,
    stillCurrent:()=>project?.id===projectId});
}
function jsonDialog(title, value, save) {
  formDialog(
    title,
    '<p class="muted">Every field is editable. Stable IDs stay fixed; generation metadata and previous assets remain in history.</p><textarea class="json-editor" id="jsonEditor">' +
      escape(JSON.stringify(value, null, 2)) +
      "</textarea>",
    () => save(JSON.parse($("#jsonEditor").value)),
  );
}
function confirmAction(text, fn) {
  formDialog("Confirm change", "<p>" + escape(text) + "</p>", () => action(fn));
}
async function chooseFile() {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.onchange = () => resolve(input.files[0]);
    input.oncancel = () => resolve(null);
    input.click();
  });
}
function toDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
async function upload(file) {
  return api("upload", { project: project.id, data: await toDataURL(file) });
}
function downloadBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}
async function downloadAsset(path, name) {
  if(await directCloudDownload(project.id,path,name,api))return;
  const response = await nativeRequest(
    "/studio/asset?project=" +
      project.id +
      "&path=" +
      encodeURIComponent(path) +
      "&download=" +
      encodeURIComponent(name),
    key,
  );
  if (window.showSaveFilePicker) {
    const handle = await showSaveFilePicker({ suggestedName: name });
    const stream = await handle.createWritable();
    await response.body.pipeTo(stream);
  } else downloadBlob(await response.blob(), name);
}
async function newProject() {
  await saveSettingsIfVisible();
  if (connected) project = await api("create", { name: "My story", sourceProject: project?.id });
  else project = offlineProject();
  chapterId = project.chapters[0].id;
  tab = "write";
  await cache();
  await refreshProjects();
  render();
}
async function saveSettingsIfVisible() {
  if (tab === "settings") await saveSettings();
}
function voicePreviewResults() {
  return (project.voicePreviews || []).slice().reverse().map(p => `<p>${escape(p.voice)} · ${p.speed}× · ${time(p.duration)} · ${escape(p.narrationDelivery || 'standard')} · ${escape(p.soundEffects || 'subtle')}${p.exportPath ? ' · AAC 128 kbps' : ' · lossless WAV'}</p><audio controls data-asset="${escape(p.exportPath || p.path)}"></audio>`).join('');
}
function wireSettings() {
  const updateChecks = () => {
    const level = $("#settingCheckLevel").value;
    if (["practical", "strict"].includes(level)) $("#settingQCPolicy").value = level;
    $("#settingQCPolicy").disabled = level !== "sampled";
    $("#settingSampleEvery").disabled = level !== "sampled";
    $("#settingRepair").disabled = level === "off" || $("#settingQCPolicy").value !== "strict";
    if ($("#settingRepair").disabled) $("#settingRepair").checked = false;
  };
  $("#settingCheckLevel").onchange = updateChecks;
  $("#settingQCPolicy").onchange = updateChecks;
  updateChecks();
  const updateWatermark = () => {
    const image = $("#settingWatermarkType").value === "image";
    $("#settingWatermarkText").disabled = image;
    $("#settingWatermarkColor").disabled = image;
  };
  $("#settingWatermarkType").onchange = updateWatermark;
  updateWatermark();
  $('#watermarkUpload').onclick = () => action(async () => {
    const file = await chooseFile();
    if (!file) return;
    const asset = await api('upload',{project:project.id,data:await toDataURL(file),purpose:'watermark'});
    $('#settingWatermarkPath').value = asset.path;
    $('#settingWatermarkType').value = 'image';
    await saveSettings(); render();
    note('Logo saved with transparency. Rebuild an export to apply it.');
  });
  $('#watermarkRemove').onclick = () => action(async () => {
    $('#settingWatermarkPath').value = '';
    $('#settingWatermarkEnabled').checked = false;
    $('#settingWatermarkType').value = 'text';
    await saveSettings(); render();
  });
  $('#settingsCloudBudget').onclick = () => $('#configureCloud').click();
  $('#applyRenderBackend').onclick = () => action(async () => {
    await saveSettings();
    health=await api('config',{renderBackend:$('#localRenderBackend').value});
    render();note(health.renderer.note);
  });
  $('#voicePreviewGenerate').onclick = () => action(async () => {
    const text = $('#voicePreviewText').value.trim();
    if (!text || text.length > 1800) throw new Error('Enter 1–1800 characters for the audition.');
    queue = await api('jobs', {project:project.id, kind:'voice-preview', options:{
      text, voice:$('#settingVoice').value, speed:Number($('#settingSpeed').value),
      soundEffects:$('#settingSoundEffects').value,
      narrationDelivery:$('#settingNarrationDelivery').value,
      emphasisPhrases:$('#settingEmphasisPhrases').value.split('\n').map(x=>x.trim()).filter(Boolean)}});
    renderQueue();
    note('Local voice audition queued. Refresh auditions when it finishes. Existing chapter audio is preserved.');
  });
  $('#voicePreviewRefresh').onclick = () => action(async () => {
    const latest = await api('project?id=' + project.id);
    project.voicePreviews = latest.voicePreviews || [];
    $('#voicePreviewResults').innerHTML = voicePreviewResults();
    await fillMedia();
  });
  $("#productionSaveSettings").onclick = () => action(saveSettings);
  root.querySelectorAll("[data-save-settings]").forEach(b => b.onclick = () => action(saveSettings));
  $('#prepareStory').onclick = () => action(async () => { await saveSettings(); await submit('prepare-story'); });
  $('#prepareRestyle').onclick = () => action(async () => { await saveSettings(); await submit('restyle-story'); });
  $('#editIntroShots').onclick = () => jsonDialog('Intro shots, timing and references', project.intro.shots || [], v => patch('project', project.id, {intro: {...project.intro, shots:v}}, true));
  $("#introDuration").oninput = (e) =>
    ($("#introDurationValue").textContent = e.target.value);
  $("#settingImageProvider").onchange = (e) =>
    action(async () => {
      const p = e.target.value,
        h = health?.providers[p];
      project._imageSelectedByUser = true;
      const settings = {
        ...project.settings,
        image: {
          ...project.settings.image,
          ...h?.recommended,
          provider: p,
          model: h?.models?.[0] || "unconfigured",
          workflow: h?.workflow?.[0] || "unconfigured",
        },
      };
      await patch("project", project.id, { settings }, true);
    });
  $("#settingImagePreset").onchange = (e) => {
    const native = $("#settingImageProvider").value === "native-flux";
    const refs = ["Fast Local", "Character Reference", "Image Repair"].includes(
      e.target.value,
    );
    $("#settingSteps").value = native
      ? 4
      : e.target.value === "Fast Local"
        ? 8
        : e.target.value === "Maximum Quality"
          ? 30
          : 20;
    $("#settingGuidance").value = native ? 1 : 7;
    if (native) $("#settingResolution").value = refs ? "384x384" : "448x448";
    if (e.target.value === "Anime/Manga") $("#settingStyle").value = "anime";
    if (e.target.value === "Cinematic")
      $("#settingStyle").value = "photorealistic cinema";
    if (e.target.value === "Storybook") $("#settingStyle").value = "storybook";
    for(const id of ['settingSteps','settingGuidance','settingResolution','settingStyle'])settingsDrafts.remember(project.id,$('#'+id));
    settingsDraftStatus();
  };
  $("#editCustomLayout").onclick = () =>
    jsonDialog("Custom layout targets", project.settings.customLayout, (v) =>
      patch(
        "project",
        project.id,
        { settings: { ...project.settings, customLayout: v } },
        true,
      ),
    );
  $("#editImageSettings").onclick = () =>
    jsonDialog("Image settings and conditioning", project.settings.image, (v) =>
      patch(
        "project",
        project.id,
        { settings: { ...project.settings, image: v } },
        true,
      ),
    );
  $("#connectCloudImages").onclick = () => action(async () => {
    const result = await api("cloud-image-connect", {});
    health = result.health;
    await patch("project", project.id, {settings: {...project.settings,
      image: {...project.settings.image, ...result.settings, fallbackEnabled: false}}}, true);
    note("Qwen cloud images connected. Existing shots and completed videos are preserved. New shots use character references automatically; change an existing shot in its editor to regenerate it with Qwen.");
  });
  $("#configureCloud").onclick = () => {
    const ready = health?.directorProviders?.["openai-luna"]?.installed;
    formDialog(
      "Cloud setup",
      `<p>Luna directs your story. Runpod runs the image workflow; its GPU and storage are billed separately.</p><ol><li><a href="https://platform.openai.com/" target="_blank" rel="noopener">Create your OpenAI API account</a>, add API billing, and <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">create a project API key</a>.</li><li>Enter that key below to connect Luna. Your key stays in the local helper, outside project exports and browser storage.</li><li><a href="https://console.runpod.io/" target="_blank" rel="noopener">Configure Runpod billing</a> and use one account for both the image worker and persistent storage. Choose an available RTX 5090 or RTX PRO 6000 after checking its live rate and region. See the <a href="./CLOUD-SETUP.md" target="_blank" rel="noopener">cloud setup guide</a> before deployment.</li></ol><p><a href="./CLOUD-IMAGE-EVALUATION.md" target="_blank" rel="noopener">Image model comparison and character-reference tests</a>: FLUX.2 Klein 4B is the current tested default for fantasy story images with character references. Alternative models remain configurable; changing a model requires its own generation and reference tests.</p><label>OpenAI API key ${ready ? "(already saved; leave blank to retain)" : ""}<input id="cloudOpenAIKey" type="password" autocomplete="off" spellcheck="false" placeholder="sk-…"></label><label>Runpod API key (optional; leave blank to retain)<input id="cloudRunpodKey" type="password" autocomplete="off" spellcheck="false" placeholder="Runpod API key"></label><p class="muted">Keys stay in your local helper and are excluded from project exports and browser storage. Saving an OpenAI key verifies Luna access and selects it for this project. Saving a Runpod key prepares worker management; it does not rent a GPU or download a model. Image generation remains on your current provider until a cloud workflow passes generation tests.</p>`,
      async () => {
        const key = $("#cloudOpenAIKey").value.trim();
        const runpodKey = $("#cloudRunpodKey").value.trim();
        if (!key && !runpodKey && !ready)
          throw new Error("Enter an OpenAI or Runpod API key to save its connection.");
        if (key || runpodKey) health = await api("config", {
          ...(key ? { openaiApiKey: key } : {}),
          ...(runpodKey ? { runpodApiKey: runpodKey } : {}),
        });
        if (!key && !ready) {
          note("Runpod key saved privately. GPU deployment and image validation are still required.");
          return;
        }
        await api("cloud-test", {});
        await patch(
          "project",
          project.id,
          {
            settings: {
              ...project.settings,
              director: {
                ...project.settings.director,
                provider: "openai-luna",
              },
            },
          },
          false,
        );
        note(
          "Luna connected and selected. Your image provider and completed assets are preserved.",
        );
      },
    );
  };
  $("#configureRuntimes").onclick = () =>
    action(async () => {
      const config = await api("config");
      jsonDialog("Local runtime configuration", config, (v) =>
        api("config", v).then((h) => {
          health = h;
          render();
        }),
      );
    });
  $("#introUpload").onclick = () =>
    action(async () => {
      await saveSettings();
      const file = await chooseFile();
      if (file) {
        const result = await upload(file);
        await patch(
          "project",
          project.id,
          { intro: { ...project.intro, visualPath: result.path } },
          true,
        );
      }
    });
  $("#introAudio").onclick = () => action(async () => { await saveSettings(); await submit("intro-audio"); });
  $("#introGenerate").onclick = () => action(async () => { await saveSettings(); await submit("intro-image"); });
  $("#introPreview").onclick = () => action(async () => { await saveSettings(); await submit("intro-render"); });
  $("#showModelNotes").onclick = () => {
    const a = document.createElement("a");
    a.href = "./STUDIO-IMPLEMENTATION.md";
    a.target = "_blank";
    a.click();
  };
}
async function saveSettings() {
  const savedDrafts=settingsDrafts.snapshot(project.id),settingsProjectId=project.id;
  const s = structuredClone(project.settings),
    intro = structuredClone(project.intro);
  s.style = $("#settingStyle").value;
  s.layoutMode = $("#settingLayout").value;
  s.generationMode = $("#settingGenerationMode").value;
  s.voice = $("#settingVoice").value;
  s.speed = Number($("#settingSpeed").value);
  s.soundEffects = $('#settingSoundEffects').value;
  s.narrationDelivery = $('#settingNarrationDelivery').value;
  s.emphasisPhrases = $('#settingEmphasisPhrases').value.split('\n').map(x=>x.trim()).filter(Boolean);
  s.director.provider = $("#settingDirectorProvider").value;
  s.director.reasoning = $("#settingReasoning").value;
  s.continuityStrictness = $("#settingContinuity").value;
  s.appearanceHandling = $("#settingAppearance").value;
  s.maxImageRetries = Number($("#settingRetries").value);
  s.qcPolicy = $("#settingQCPolicy").value;
  s.qcCheckLevel = $("#settingCheckLevel").value;
  s.qcSampleEvery = Number($("#settingSampleEvery").value);
  s.visionQC = s.qcCheckLevel !== "off";
  s.automaticRepair = !$("#settingRepair").disabled && $("#settingRepair").checked;
  s.economyPanels = $('#settingEconomyPanels').checked;
  s.focusedPrompts = $('#settingFocusedPrompts').checked;
  const apiLimit = $('#settingAPIBudget').value.trim();
  if (apiLimit) {
    const amount = Number(apiLimit);
    if (!Number.isFinite(amount) || amount <= 0) throw new Error('Enter a positive API spending limit.');
    s.budget = {...s.budget, openaiUSD:amount};
  } else if (s.budget?.openaiUSD != null) throw new Error('Keep or change the existing API limit; it cannot be cleared here.');
  s.watermark = {...s.watermark,
    enabled:$('#settingWatermarkEnabled').checked, type:$('#settingWatermarkType').value,
    text:$('#settingWatermarkText').value, imagePath:$('#settingWatermarkPath').value,
    position:$('#settingWatermarkPosition').value, widthPercent:Number($('#settingWatermarkWidth').value),
    opacity:Number($('#settingWatermarkOpacity').value)/100, marginPercent:Number($('#settingWatermarkMargin').value),
    color:$('#settingWatermarkColor').value};
  Object.assign(s.image, {
    provider: $("#settingImageProvider").value,
    model: $("#settingImageModel").value,
    workflow: $("#settingImageWorkflow").value,
    preset: $("#settingImagePreset").value,
    referenceStrength: Number($("#settingReferenceStrength").value),
    steps: Number($("#settingSteps").value),
    guidance: Number($("#settingGuidance").value),
    sampler: $("#settingSampler").value,
    scheduler: $("#settingScheduler").value,
    fallbackEnabled: $("#settingFallback").checked,
  });
  [s.image.width, s.image.height] = $("#settingResolution")
    .value.split("x")
    .map(Number);
  [s.video.width, s.video.height] = $("#settingVideoSize")
    .value.split("x")
    .map(Number);
  s.video.fps = Number($("#settingVideoFps").value);
  s.video.imageFit = $("#settingImageFit").value;
  s.video.motionMode = $("#settingMotionMode").value;
  s.video.zoomAmount = Number($("#settingZoomAmount").value) / 100;
  s.engagement=engagementValues(s.engagement);
  Object.assign(intro, {
    enabled: $("#introEnabled").checked,
    duration: Number($("#introDuration").value),
    placement: $("#introPlacement").value,
    showTitle: $("#introShowTitle").checked,
    endOnNarration: $("#introEndOnNarration").checked,
    title: $("#introTitle").value,
    subtitle: $("#introSubtitle").value,
    voiceText: $("#introVoiceText").value,
    visualPrompt: $("#introVisualPrompt").value,
  });
  await patch(
    "project",
    project.id,
    {
      name: $("#settingProjectName").value || project.name,
      settings: s,
      intro,
      _imageSelectedByUser: true,
    },
    false,
  );
  settingsDrafts.acknowledge(settingsProjectId,savedDrafts);settingsDraftStatus();
  note(
    "Project settings saved. Existing shots keep their own model settings; edit an individual shot to change its model.",
  );
}
async function testStory() {
  const fixture = await (await fetch("./studio-test-story.json")).json();
  fixture.id = id("pr-");
  project = await api("import", { project: fixture });
  chapterId = project.chapters[0].id;
  tab = "write";
  await cache();
  await refreshProjects();
  render();
}
async function poll() {
  if (!connected || polling || !project) return;
  polling = true;
  const requestedProjectId=project.id;
  try {
    queue = await api("queue");
    renderQueue();
    if (
      !dirty && !project._unsynced && project.id===requestedProjectId &&
      !document.querySelector("dialog.studio-form-dialog[open]") &&
      tab !== "settings"
    ) {
      const revision = await api("revision?id=" + requestedProjectId);
      if (revision.revision !== project.revision) {
        const latest = await api("project?id=" + requestedProjectId);
        const active = document.activeElement;
        if (canApplyBackgroundProject(project,latest,{
          requestedId:requestedProjectId,dirty,
          editing:Boolean(document.querySelector("dialog.studio-form-dialog[open]"))||(root.contains(active)&&["INPUT","TEXTAREA","SELECT"].includes(active.tagName)),
        })) {
          project = latest;
          await cache();
          if (["review", "cast", "timeline"].includes(tab)) render();
          else {
            refreshBackgroundSummary();
            $("#productionSaveState").textContent =
              "Background results saved · " + ch().status.replaceAll("_", " ");
          }
        }
      }
    }
  } catch (error) {
    connected = false;
    note(error.message + " · Reconnecting automatically.", true);
  } finally {
    polling = false;
  }
}
let savedId;
try{savedId=localStorage.getItem('qt-production-project');}catch{cacheWarning='Browser selection storage is unavailable.';}
try{if(savedId)project=await loadStudioProject(savedId);}catch{cacheWarning='Browser project recovery is unavailable. Connect the helper to access its saved projects.';}
if (!project) {
  let existing=[];
  try{existing=await listStudioProjects();}catch{cacheWarning='Browser project recovery is unavailable. Connect the helper to access its saved projects.';}
  project = existing[0] || {...offlineProject(), _connectionPlaceholder:true};
  if (existing.length) projects = existing;
}
chapterId = project.chapters[0].id;
try{await cache();}catch(error){cacheWarning=error.message;}
render();
if (key) await connect();
setInterval(() => void poll(), 1800);
setInterval(() => {
  if (!connected && key) void connect();
}, 15000);

function editPerson(person, main) {
  const identity = person.permanentIdentity,
    appearance = person.defaultAppearance;
  formDialog(
    "Character profile",
    `<label>Name<input id="profileName" value="${escape(person.name)}"></label><label>Description<textarea id="profileDescription" rows="3">${escape(person.description)}</textarea></label><label>Role<select id="profileType">${options(["main", "supporting", "temporary", "background", "group"], person.type)}</select></label><h3>Permanent identity</h3><div class="two-col">${Object.entries(
      identity,
    )
      .map(
        ([k, v]) =>
          `<label>${escape(k)}<input data-identity-field="${escape(k)}" value="${escape(v)}"></label>`,
      )
      .join(
        "",
      )}</div><h3>Default appearance</h3><p class="muted">Scenes can change clothing, hairstyle and injuries without changing permanent identity.</p><div class="two-col">${Object.entries(
      appearance,
    )
      .map(
        ([k, v]) =>
          `<label>${escape(k)}<input data-appearance-field="${escape(k)}" value="${escape(v)}"></label>`,
      )
      .join(
        "",
      )}</div><details><summary>All profile fields</summary><textarea id="profileAdvanced" class="json-editor">${escape(JSON.stringify(person, null, 2))}</textarea></details>`,
    async (modal) => {
      const v = JSON.parse($("#profileAdvanced").value);
      delete v.id;
      v.name = $("#profileName").value;
      v.description = $("#profileDescription").value;
      v.type = $("#profileType").value;
      modal
        .querySelectorAll("[data-identity-field]")
        .forEach(
          (i) => (v.permanentIdentity[i.dataset.identityField] = i.value),
        );
      modal
        .querySelectorAll("[data-appearance-field]")
        .forEach(
          (i) => (v.defaultAppearance[i.dataset.appearanceField] = i.value),
        );
      await patch(main ? "character" : "person", person.id, v, true);
    },
  );
}
function editShot(shot) {
  const value = structuredClone(shot);
  delete value.id;
  formDialog(
    "Shot editor",
    `<p class="muted">${escape(shot.narrationSegment)}</p><div class="two-col"><label>Start (seconds)<input id="shotStart" type="number" step=".01" value="${shot.start}"></label><label>End (seconds)<input id="shotEnd" type="number" step=".01" value="${shot.end}"></label></div><label>Visible action<textarea id="shotAction" rows="2">${escape(shot.action)}</textarea></label><div class="two-col"><label>Camera shot<select id="shotCamera">${options(["extreme wide", "wide", "medium wide", "medium", "medium close-up", "close-up", "extreme close-up", "over-the-shoulder", "POV", "profile", "establishing shot", "reaction shot", "insert shot", "silhouette", shot.camera.shot], shot.camera.shot)}</select></label><label>Camera angle<select id="shotAngle">${options(["eye level", "low angle", "high angle", "bird’s-eye", "worm’s-eye", "Dutch angle", shot.camera.angle], shot.camera.angle)}</select></label></div><label>Composition<input id="shotComposition" value="${escape(shot.camera.composition)}"></label><div class="two-col"><label>Motion<select id="shotMotion">${options(["static", "slow zoom in", "slow zoom out", "pan left", "pan right", "pan up", "pan down"], shot.motion)}</select></label><label>Transition<select id="shotTransition">${options(["cut", "crossfade"], shot.transition)}</select></label><label>Image provider<select id="shotProvider">${options(
      [
        ["existing", "Existing SD fallback"],
        ["native-flux", "Quantized FLUX Klein"],
        ["comfyui", "ComfyUI · local or cloud through SSH"],
      ],
      shot.imageProvider,
    )}</select></label><label>Image model<select id="shotModel">${options(health?.providers[shot.imageProvider]?.models || [shot.imageModel], shot.imageModel)}</select></label><label>Seed<input id="shotSeed" type="number" min="0" max="4294967295" value="${shot.generationSettings.seed}"></label><label>Workflow<input id="shotWorkflow" value="${escape(shot.workflow)}"></label></div><label>Image prompt<textarea id="shotPrompt" rows="5">${escape(shot.prompt)}</textarea></label><label>Negative prompt<textarea id="shotNegative" rows="2">${escape(shot.negativePrompt)}</textarea></label><details><summary>All shot fields and generation settings</summary><textarea id="shotAdvanced" class="json-editor">${escape(JSON.stringify(value, null, 2))}</textarea></details>`,
    async () => {
      const v = JSON.parse($("#shotAdvanced").value);
      v.start = Number($("#shotStart").value);
      v.end = Number($("#shotEnd").value);
      v.duration = v.end - v.start;
      v.action = $("#shotAction").value;
      v.camera = {
        ...v.camera,
        shot: $("#shotCamera").value,
        angle: $("#shotAngle").value,
        composition: $("#shotComposition").value,
      };
      v.motion = $("#shotMotion").value;
      v.transition = $("#shotTransition").value;
      v.imageProvider = $("#shotProvider").value;
      v.imageModel = $("#shotModel").value;
      v.workflow = $("#shotWorkflow").value;
      v.generationSettings.seed = Number($("#shotSeed").value);
      v.generationSettings.model = v.imageModel;
      v.prompt = $("#shotPrompt").value;
      v.negativePrompt = $("#shotNegative").value;
      delete v.id;
      await patch("shot", shot.id, v, true);
    },
  );
  $("#shotProvider").onchange = (e) => {
    const provider = health.providers[e.target.value];
    $("#shotModel").innerHTML = options(
      provider.models || ["unavailable"],
      provider.models?.[0],
    );
    $("#shotWorkflow").value = provider.workflow?.[0] || "unavailable";
    const v = JSON.parse($("#shotAdvanced").value);
    v.generationSettings = { ...v.generationSettings, ...provider.recommended };
    if (e.target.value === "native-flux" && shot.referenceImages?.length)
      v.generationSettings.width = v.generationSettings.height = 384;
    $("#shotAdvanced").value = JSON.stringify(v, null, 2);
    if (!provider.capabilities.supportsNegativePrompt)
      $("#shotNegative").value = "";
  };
}
