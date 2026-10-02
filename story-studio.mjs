import { pairingKey, helperJson } from "./helper-connection.mjs?v=queue-1";
import { nativeRequest } from "./native-client.mjs?v=queue-1";
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
  saveTimer;
const mediaCache = new Map();
const root = document.createElement("section");
root.id = "productionStudio";
root.className = "production";
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
  if (project) {
    await saveStudioProject(project);
    localStorage.setItem("qt-production-project", project.id);
  }
}
async function media(path) {
  if (!path) return "";
  const k = project.id + "/" + path;
  if (mediaCache.get(k)?.expires > Date.now()) return mediaCache.get(k).url;
  const result = await api("media-link", { project: project.id, path });
  mediaCache.set(k, { url: result.url, expires: Date.now() + 3000000 });
  return result.url;
}
async function fillMedia() {
  for (const node of root.querySelectorAll("[data-asset]")) {
    try {
      node.src = await media(node.dataset.asset);
    } catch (error) {
      node.alt = "Asset preview unavailable: " + error.message;
    }
  }
}
async function refreshProjects() {
  projects = connected ? await api("projects") : await listStudioProjects();
}
async function connect() {
  key = pairingKey();
  try {
    health = await api("health");
    connected = true;
    queue = health.queue;
    await refreshProjects();
    if (project?._unsynced) {
      try {
        await api("project?id=" + project.id);
        note(
          "Offline edits are saved in this browser. Use “Sync offline edits” after checking the project; existing helper generations are preserved until you choose to sync.",
        );
      } catch {
        const local = structuredClone(project);
        delete local._unsynced;
        if (
          !local._imageSelectedByUser &&
          !local.chapters.some((c) => c.scenes.length) &&
          health.providers["native-flux"]?.validated
        )
          local.settings.image = {
            ...local.settings.image,
            ...health.providers["native-flux"].recommended,
            provider: "native-flux",
            model: "flux2-klein-4b-q4",
            workflow: "reference-edit",
          };
        project = await api("import", { project: local });
      }
    } else if (project) {
      try {
        project = await api("project?id=" + project.id);
      } catch {
        if (
          project.chapters.length === 1 &&
          !project.chapters[0].sourceText &&
          !project.characters.length &&
          health.providers["native-flux"]?.validated
        )
          project = await api("create", { name: project.name });
        else project = await api("import", { project });
      }
    } else if (projects.length)
      project = await api("project?id=" + projects[0].id);
    else project = await api("create", { name: "My story" });
    chapterId = ch()?.id;
    await cache();
    render();
  } catch (error) {
    connected = false;
    if (!project) {
      projects = await listStudioProjects();
      if (projects.length) project = projects[0];
    }
    render();
    note(error.message, true);
  }
}
async function mutate(path, body, redraw = true) {
  const p = await api(path, { project: project.id, ...body });
  if (p?.chapters) {
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
      dirty = false;
    } catch (error) {
      if (!error.message.includes("Cannot reach the NVIDIA helper"))
        throw error;
      connected = false;
    }
  }
  if (!connected) {
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
    dirty = false;
    if (redraw) render();
  }
  const state = $("#productionSaveState");
  if (state)
    state.textContent = connected
      ? "Saved to helper and browser"
      : "Saved in browser · helper offline";
}
async function action(fn) {
  try {
    await flush();
    await fn();
  } catch (error) {
    note(error.message, true);
  }
}
async function flush() {
  if (dirty) {
    clearTimeout(saveTimer);
    await saveEditor();
  }
}
async function saveEditor() {
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
  dirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => action(saveEditor), 600);
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
      automaticRepair: true,
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
        fps: 24,
        crf: 21,
        imageFit: "contain",
      },
    },
    intro: {
      enabled: false,
      duration: 15,
      placement: "full_story_only",
      title: "My story",
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
  for (const button of root.querySelectorAll("[data-chapter]")) {
    const chapter = project.chapters.find(
      (item) => item.id === button.dataset.chapter,
    );
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
  root.innerHTML = `<div class="project-bar"><div><div class="kicker">Story production studio</div><h1>${escape(project.name)}</h1><div class="connection-line">${connected ? `${escape(health.hardware.gpu)} · ${health.hardware.vramGB} GB VRAM · ${health.hardware.ramGB} GB RAM · shared Audio + Studio helper` : "Helper offline · your text is saved in this browser"}</div></div><div class="toolbar"><select id="productionProject" aria-label="Project">${options(
    projects.map((p) => [p.id, p.name]),
    project.id,
  )}</select><button id="productionNewProject">New project</button><button id="productionConnect">${connected ? "Reconnect" : "Connect helper"}</button><button id="productionBackup">Export project</button><label style="margin:0"><button id="productionImport">Import</button><input id="productionImportFile" type="file" accept="application/json,.json" hidden></label></div></div>
  <div id="productionNotice" class="notice" role="status" aria-live="polite" hidden></div>
  <div class="full-video-bar"><div><strong>Finished entering your chapters?</strong><p class="muted">Generate narration, direct every chapter, create missing character references and images, then render one full video. Detected main characters are confirmed automatically. Your review settings and manual edits are preserved.</p><div id="fullVideoStatus" role="status" aria-live="polite"></div></div><button class="primary" id="productionFullVideo" ${connected ? "" : "disabled"}>Generate full video</button></div>
  ${project._unsynced ? '<div class="notice">This browser has offline edits.<button id="syncOffline">Sync offline edits</button></div>' : ""}
  ${project.warnings
    .filter((w) => !w.resolved)
    .map(
      (w) =>
        `<div class="notice"><strong>${escape(w.message)}</strong><p>${escape(w.categories.join(", "))}</p><button data-warning="${w.id}" data-choice="update">Update affected chapters</button> <button data-warning="${w.id}" data-choice="keep">Keep existing chapters</button></div>`,
    )
    .join("")}
  <div class="studio-grid"><aside class="chapter-rail">${project.chapters.map((x) => `<button class="chapter-button ${x.id === c.id ? "selected" : ""}" data-chapter="${x.id}"><span>${x.number.toString().padStart(2, "0")}</span><strong>${escape(x.name)}</strong><small>${escape(x.status.replaceAll("_", " "))}${x.continuityNeedsReview ? " · review continuity" : ""}</small></button>`).join("")}<button class="chapter-add" id="productionAddChapter">+ New chapter</button></aside>
  <div class="production-main"><div class="chapter-heading"><div><h2>${escape(c.name)}</h2><div class="subtitle">${escape(c.status.replaceAll("_", " "))} · ${escape(project.settings.image.model)} · ${escape(project.settings.image.workflow)}</div></div><div class="toolbar"><button id="productionDuplicate">Duplicate</button><button id="productionMoveUp" aria-label="Move chapter earlier">↑</button><button id="productionMoveDown" aria-label="Move chapter later">↓</button><button class="danger" id="productionDelete">Delete chapter</button></div></div>
  <nav class="workspace-tabs" role="tablist">${[
    ["write", "1 · Story"],
    ["narration", "2 · Narration"],
    ["cast", "3 · Characters"],
    ["review", "4 · Review & layout"],
    ["timeline", "5 · Timeline & video"],
    ["settings", "Settings"],
  ]
    .map(
      ([v, label]) =>
        `<button role="tab" aria-selected="${tab === v}" data-tab="${v}">${label}</button>`,
    )
    .join("")}</nav>
  <section id="productionContent">${content()}</section><div class="queue-panel" id="productionQueue"></div><p class="save-state" id="productionSaveState">Saved assets stay in the helper output folder. Refresh restores this project.</p></div></div>`;
  wire();
  renderQueue();
  void fillMedia();
}
function content() {
  const c = ch();
  switch (tab) {
    case "write":
      return `<label>Chapter name<input id="chapterName" value="${escape(c.name)}"></label><label>Story<textarea class="story-editor" id="chapterStory" placeholder="Paste this chapter’s story. Characters and continuity carry forward from earlier chapters.">${escape(c.sourceText)}</textarea></label><div class="toolbar"><span id="wordCount" class="muted">${wordStats(c.sourceText)}</span><button id="productionReviewNarration">Review clean narration</button><button class="primary" id="productionAnalyze">Analyze chapter</button><button id="productionExample">Load two-chapter test story</button><button id="productionLegacy">Import existing comic story</button></div><div class="steps"><span>Clean narration</span><span>Continuous audio</span><span>AI direction</span><span>Review</span><span>Images</span><span>Video</span></div><p class="muted">Analyze prepares audio and a scene plan. Review it before generating images. Existing chapter assets remain saved when you reanalyze.</p>${stats()}${c.errors
        .slice(-2)
        .map((e) => `<div class="notice error">${escape(e.message)}</div>`)
        .join("")}`;
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
  return `<h2>${c.scenes.length ? "Chapter analysis complete" : "Scene plan"}</h2>${stats()}<div class="notice">Narration is separate from chapter metadata. Intro: ${project.intro.enabled ? "ON · " + project.intro.duration + " seconds · " + project.intro.placement.replaceAll("_", " ") : "OFF"}. Images use ${escape(project.settings.image.model)}.</div><div class="toolbar"><button data-go="narration">Review narration</button><button data-go="cast">Review characters</button><button data-go="settings">Review image settings</button><button class="primary" id="productionGenerate">Generate missing images</button><button id="productionRetryFailed">Regenerate failed images</button><button id="productionAnalyze">Regenerate chapter plan</button></div><label class="inline"><input id="productionAutoContinue" type="checkbox" ${project.settings.autoContinue ? "checked" : ""}>Auto continue to images after future analyses</label>${c.proposedPlan ? '<div class="notice">A new plan is ready. Existing manual shots were preserved.<button id="applyProposedPlan">Apply proposed plan</button></div>' : ""}<div class="page-row"><span class="muted">Scenes ${Math.min(c.scenes.length, scenePage * 10 + 1)}–${Math.min(c.scenes.length, scenePage * 10 + 10)} of ${c.scenes.length}</span><div class="toolbar"><button id="scenePrevious" ${scenePage === 0 ? "disabled" : ""}>Previous</button><button id="sceneNext" ${(scenePage + 1) * 10 >= c.scenes.length ? "disabled" : ""}>Next</button></div></div>${page.map(sceneRow).join("") || '<p class="muted">Paste a chapter and select Analyze. Qwen will plan scenes around the actual narration timing.</p>'}<details><summary>Director decisions and diagnosis metadata</summary><button id="editChapterPlan">Edit chapter plan JSON</button><pre style="max-height:280px;overflow:auto;white-space:pre-wrap">${escape(JSON.stringify(c.analysis, null, 2))}</pre></details>`;
}
function sceneRow(scene) {
  return `<article class="scene-item"><div class="scene-head"><div><h3>Scene ${ch().scenes.indexOf(scene) + 1} · ${escape(scene.purpose)}</h3><span class="scene-time">${time(scene.start)} → ${time(scene.end)}</span></div><span class="type-tag">${scene.origin || "AI"}</span></div><p class="narration-excerpt">${escape(scene.shots.map((s) => s.narrationSegment).join(" "))}</p><p class="muted">${escape(scene.location)} · ${escape(scene.mood)} · ${scene.shots.length} shots</p><p class="muted">Characters: ${escape(names(scene.characters))}</p><p class="muted">${escape(scene.pacingReason)}</p>${scene.appearanceChanges?.length ? `<div class="notice"><strong>Planned appearance / object changes</strong>${scene.appearanceChanges.map((e) => `<p>${escape(people().find((p) => p.id === e.characterId)?.name || e.characterId)} · ${escape(JSON.stringify(e.to))}<br><small>Story evidence: ${escape(e.reason)}</small></p>`).join("")}${!scene.appearanceChangesReviewed ? `<button data-accept-changes="${scene.id}">Accept planned changes</button>` : "Reviewed"}</div>` : ""}<div class="toolbar"><button data-scene="${scene.id}" data-scene-action="edit">Edit scene</button><button data-scene="${scene.id}" data-scene-action="add">Add shot</button><button data-scene="${scene.id}" data-scene-action="split">Split scene</button><button data-scene="${scene.id}" data-scene-action="merge">Merge with next</button><button data-scene="${scene.id}" data-scene-action="up">↑</button><button data-scene="${scene.id}" data-scene-action="down">↓</button><button data-scene="${scene.id}" data-scene-action="plan">Regenerate scene plan</button></div><div class="shot-grid">${scene.shots.map(shotRow).join("")}</div></article>`;
}
function shotRow(shot) {
  return `<article class="shot-card">${shot.imagePath ? `<img data-asset="${escape(shot.imagePath)}" alt="${escape(shot.action)}">` : `<div class="shot-empty">${escape(shot.status.replaceAll("_", " "))}</div>`}<h4>${time(shot.start)} → ${time(shot.end)} · ${escape(shot.camera.shot)}</h4><p>${escape(shot.action)}</p><p>${escape(shot.imageModel)} · ${escape(shot.workflow)} · ${escape(shot.qc?.status || "PENDING")}</p><div class="shot-cast">${people()
    .map(
      (p) =>
        `<label><input type="checkbox" data-shot-cast="${shot.id}" value="${p.id}" ${shot.characters.some((c) => c.id === p.id) ? "checked" : ""}>${escape(p.name)} <small>${escape(p.type)}</small></label>`,
    )
    .join(
      "",
    )}<button data-shot="${shot.id}" data-shot-action="none">N/A</button></div><div class="toolbar"><button data-shot="${shot.id}" data-shot-action="edit">Edit shot</button><button data-shot="${shot.id}" data-shot-action="generate">${shot.imagePath ? "Regenerate" : "Generate"}</button><button data-shot="${shot.id}" data-shot-action="repair" ${shot.imagePath ? "" : "disabled"}>Repair</button><button data-shot="${shot.id}" data-shot-action="qc" ${shot.imagePath ? "" : "disabled"}>Visual QC</button>${shot.imagePath && shot.qc?.pass !== true ? `<button data-shot="${shot.id}" data-shot-action="accept-image">Accept image</button>` : ""}<button data-shot="${shot.id}" data-shot-action="delete">Delete</button></div>${shot.generationError ? `<p class="notice error">${escape(shot.generationError)}</p>` : ""}</article>`;
}
function timeline() {
  const c = ch();
  return `<h2>Chapter timeline</h2><p class="muted">Chapter narration begins at 00:00. Click a shot to edit its timing, motion or transition.</p>${c.audio.path ? `<audio controls data-asset="${escape(c.audio.path)}"></audio>` : ""}<div class="timeline">${shots()
    .map(
      (s) =>
        `<button data-shot="${s.id}" data-shot-action="edit" style="width:${Math.max(70, (s.end - s.start) * 7)}px">${time(s.start)}<small>${escape(s.camera.shot)}</small><small>${(s.end - s.start).toFixed(1)}s · ${escape(s.motion)}</small></button>`,
    )
    .join(
      "",
    )}</div><div class="toolbar"><button class="primary" id="productionRenderChapter">Render chapter</button><button class="primary" id="productionRenderFull">Render full story</button><button data-go="settings">Intro and video settings</button><button id="validateTimeline">Validate timing</button></div>${c.render.path ? `<div class="section-box"><h3>${escape(c.name)} video · ${time(c.render.duration)}${c.renderStale ? " · Previous render; rebuild for changes" : ""}</h3><video controls data-asset="${escape(c.render.path)}"></video><button id="downloadChapterVideo">Download chapter video</button></div>` : ""}${project.render.path ? `<div class="section-box"><h3>Full story · ${time(project.render.duration)}${project.renderStale ? " · Previous render; rebuild for changes" : ""}</h3><video controls data-asset="${escape(project.render.path)}"></video><button id="downloadFullVideo">Download full story</button></div>` : ""}<div class="section-box"><h3>Full story order</h3><p>${project.intro.enabled && project.intro.placement === "full_story_only" ? `Intro ${project.intro.duration}s → ` : ""}${project.chapters.map((c) => escape(c.name) + (project.intro.enabled && project.intro.placement === "every_chapter" ? " + intro" : "")).join(" → ")}</p><p class="muted">Unchanged rendered chapters are reused. Intro OFF starts directly with Chapter 1.</p></div>`;
}
function settings() {
  const s = project.settings,
    i = s.image;
  const providers = health?.providers || {};
  const selected = providers[i.provider];
  const models = selected?.models || [i.model];
  return `<h2>Project settings</h2><label>Project name<input id="settingProjectName" value="${escape(project.name)}"></label><div class="two-col"><label>Visual style<select id="settingStyle">${options(["cinematic illustration", "anime", "manga", "storybook", "photorealistic cinema", "full-color manhwa", "dark fantasy"], s.style)}</select></label><label>Director layout<select id="settingLayout">${options(["AUTO", "CINEMATIC", "DYNAMIC", "MANGA / ANIME", "STORYBOOK", "CUSTOM"], s.layoutMode)}</select></label><label>Generation mode<select id="settingGenerationMode">${options(["QUICK", "BALANCED", "MAX QUALITY"], s.generationMode)}</select></label><label>Image quality preset<select id="settingImagePreset">${options(["Fast Local", "Balanced Quality", "Maximum Quality", "Character Reference", "Image Repair", "Anime/Manga", "Cinematic", "Storybook"], i.preset)}</select></label></div>
  <div class="section-box"><h3>Image generator</h3><div class="two-col"><label>Image provider<select id="settingImageProvider">${options(
    [
      ["existing", "Existing SD fallback / DreamShaper"],
      ["native-flux", "Quantized FLUX.2 Klein 4B"],
      ["comfyui", "Local ComfyUI workflow"],
    ],
    i.provider,
  )}</select></label><label>Image model<select id="settingImageModel">${options(models, i.model)}</select></label><label>Workflow<select id="settingImageWorkflow">${options(selected?.workflow || [i.workflow], i.workflow)}</select></label><label>SD reference strength<input ${i.provider === "native-flux" ? "disabled" : ""} id="settingReferenceStrength" type="number" min="0" max="1" step=".05" value="${i.referenceStrength}"></label></div><p class="muted">${selected?.installed ? "Installed" : "Unavailable: configure this local backend before generating."} ${selected?.validated === false ? "This native configuration has not passed laptop validation yet." : ""} ${escape(selected?.capabilities?.referenceLimitations || "")}</p><button id="showModelNotes">Model evaluation and diagnosis</button></div>
  <div class="section-box"><h3>Voice</h3><div class="two-col"><label>Existing Kokoro voice<select id="settingVoice">${options(["am_michael", "af_heart", "af_bella", "af_nicole", "am_fenrir", "am_puck", "bm_george", "bf_emma"], s.voice)}</select></label><label>Speaking speed<input id="settingSpeed" type="number" min=".5" max="2" step=".1" value="${s.speed}"></label></div></div>
  <div class="section-box"><h3>Optional intro</h3><label class="inline"><input id="introEnabled" type="checkbox" ${project.intro.enabled ? "checked" : ""}>Enable intro</label><div class="two-col"><label>Duration: <span id="introDurationValue">${project.intro.duration}</span> seconds<input id="introDuration" type="range" min="10" max="20" step="1" value="${project.intro.duration}"></label><label>Placement<select id="introPlacement">${options(
    [
      ["full_story_only", "Full story only"],
      ["every_chapter", "Every chapter"],
    ],
    project.intro.placement,
  )}</select></label><label>Title<input id="introTitle" value="${escape(project.intro.title)}"></label><label>Subtitle<input id="introSubtitle" value="${escape(project.intro.subtitle)}"></label></div><label>Intro visual description<textarea id="introVisualPrompt" rows="2">${escape(project.intro.visualPrompt || "")}</textarea></label><label>Optional explicit intro voice text<textarea id="introVoiceText" rows="2">${escape(project.intro.voiceText)}</textarea></label><div class="toolbar"><button id="introUpload">Choose background image</button><button id="introGenerate">Generate intro visual</button><button id="introAudio">Generate separate intro voice</button><button id="introPreview">Preview intro</button></div>${project.intro.visualPath ? `<img data-asset="${escape(project.intro.visualPath)}" alt="Intro background" style="max-width:260px;margin-top:12px">` : ""}</div>
  <details><summary>Advanced AI settings</summary><div class="two-col"><label>Director provider<select id="settingDirectorProvider">${options([["local-qwen", "Local Qwen3.5-4B Q4_K_M"]], s.director.provider)}</select></label><label>Director reasoning<select id="settingReasoning">${options(["Fast", "Balanced", "High"], s.director.reasoning)}</select></label><label>Continuity strictness<select id="settingContinuity">${options(["Low", "Medium", "High"], s.continuityStrictness)}</select></label><label>Appearance changes<select id="settingAppearance">${options(["Automatic", "Review changes", "Strict"], s.appearanceHandling)}</select></label><label>Max image retries<input id="settingRetries" type="number" min="0" max="10" value="${s.maxImageRetries}"></label><label>Resolution<select id="settingResolution">${options(
    [
      ["384x384", "384 × 384 · references / repair"],
      ["448x448", "448 × 448 · native balanced"],
      ["512x512", "512 × 512 · SD fallback"],
      ["768x512", "768 × 512"],
      ["512x768", "512 × 768"],
    ],
    i.width + "x" + i.height,
  )}</select></label><label>Steps<input id="settingSteps" type="number" min="1" max="50" value="${i.steps}"></label><label>Guidance<input id="settingGuidance" type="number" min="1" max="14" step=".5" value="${i.guidance}"></label><label>Sampler<input id="settingSampler" value="${escape(i.sampler)}"></label><label>Scheduler<input id="settingScheduler" value="${escape(i.scheduler)}"></label></div><label class="inline"><input id="settingVision" type="checkbox" ${s.visionQC ? "checked" : ""}>Vision QC (requires director vision projector)</label><label class="inline"><input id="settingRepair" type="checkbox" ${s.automaticRepair ? "checked" : ""}>Automatic repair within retry limit</label><label class="inline"><input id="settingFallback" type="checkbox" ${i.fallbackEnabled ? "checked" : ""}>Enable explicit SD 1.5 fallback when the chosen provider fails</label><button id="editCustomLayout">Edit custom pacing targets</button> <button id="editImageSettings">Edit conditioning / LoRA / full generation settings</button> <button id="configureRuntimes">Configure local runtime paths</button><p class="muted">Unsupported settings are rejected before jobs are queued. No silent model substitution.</p></details>
  <details><summary>Video output</summary><div class="two-col"><label>Video size<select id="settingVideoSize">${options(
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
  )}</select></label><label>Frames per second<select id="settingVideoFps">${options(
    [
      ["24", "24"],
      ["25", "25"],
      ["30", "30"],
    ],
    String(s.video.fps),
  )}</select></label></div></details><button class="primary" id="productionSaveSettings">Save project settings</button>`;
}
function renderQueue() {
  const el = $("#productionQueue");
  if (!el) return;
  if (!queue) {
    el.innerHTML =
      '<p class="muted">Connect the helper to see production jobs. Completed assets stay saved after refresh.</p>';
    return;
  }
  const fullButton = $("#productionFullVideo");
  const activeFullRun = queue.jobs.find(
    (j) =>
      j.project === project.id &&
      j.kind === "produce-story" &&
      ["QUEUED", "RUNNING"].includes(j.status),
  );
  if (fullButton) {
    fullButton.disabled = !connected || !!activeFullRun;
    fullButton.textContent = activeFullRun
      ? "Full video in progress"
      : "Generate full video";
  }
  const run = project.production,
    runStatus = $("#fullVideoStatus");
  if (runStatus && run) {
    runStatus.innerHTML = `<span>${escape(run.status === "COMPLETE" ? "Full video ready" : ["FAILED", "CANCELLED"].includes(run.status) ? run.status + " · " + run.message : run.stage || run.message || run.status)}</span>${run.status === "COMPLETE" ? '<button class="video-result-button" id="openFullVideo">Open finished video</button>' : ""}`;
    const open = $("#openFullVideo");
    if (open)
      open.onclick = () =>
        action(async () => {
          tab = "timeline";
          render();
        });
    if (activeFullRun?.started) {
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
      const entries = run.timings.filter((t) => !t.detail),
        totals = {};
      for (const entry of entries)
        totals[entry.stage] = (totals[entry.stage] || 0) + entry.seconds;
      runStatus.insertAdjacentHTML(
        "beforeend",
        `<details><summary>Measured production times</summary><p class="muted">Recorded wall time, including loading, retries and pauses. Reused assets take only their validation time.</p><table><thead><tr><th>Stage</th><th>Time</th></tr></thead><tbody>${Object.entries(
          totals,
        )
          .map(
            ([stage, seconds]) =>
              `<tr><td>${escape(stage)}</td><td>${time(seconds)}</td></tr>`,
          )
          .join(
            "",
          )}</tbody></table><details><summary>Every stage and director pass</summary>${run.timings.map((t) => `<p class="muted">${t.chapter ? "Chapter " + t.chapter + " · " : ""}${escape(t.stage)}${t.shot ? " · " + escape(t.shot) : ""} · ${t.seconds.toFixed(1)} s${t.reused ? " · reused" : ""}${t.status === "FAILED" ? " · failed attempt" : ""}</p>`).join("")}</details></details>`,
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
    imageJobs = jobs.filter((j) => j.kind === "image"),
    imageDone =
      counts?.image.COMPLETE ??
      imageJobs.filter((j) => j.status === "COMPLETE").length,
    imageTotal = counts
      ? Object.values(counts.image).reduce((a, b) => a + b, 0)
      : imageJobs.length,
    total = counts
      ? Object.values(counts.all).reduce((a, b) => a + b, 0)
      : jobs.length,
    failureCount = counts?.all.FAILED ?? failed.length,
    currentShot = shots(
      project.chapters.find((c) => c.id === current?.chapter),
    ).find((s) => s.id === current?.shot),
    currentScene = project.chapters
      .find((c) => c.id === current?.chapter)
      ?.scenes.find((s) => s.id === currentShot?.sceneId);
  el.innerHTML = `<div class="toolbar"><h3 style="flex:1">Production queue${imageTotal ? " · " + imageDone + " / " + imageTotal + " images" : ""}</h3><span class="muted">${done} complete · ${pending} waiting · ${failureCount} failed · ETA ${queue.etaSeconds == null ? "—" : time(queue.etaSeconds)}</span></div><p class="muted" role="status">${queue.paused ? "PAUSED · " : ""}${escape((currentShot && currentScene ? "Scene " + (project.chapters.find((c) => c.id === current.chapter).scenes.indexOf(currentScene) + 1) + " — Shot " + (currentScene.shots.indexOf(currentShot) + 1) + " · " : "") + (current?.message || "Ready"))}</p><progress max="${Math.max(1, total)}" value="${done}"></progress><div class="toolbar"><button data-control="pause" ${queue.paused ? "disabled" : ""}>Pause</button><button data-control="resume" ${queue.paused ? "" : "disabled"}>Resume</button><button data-control="cancel-current" ${current ? "" : "disabled"}>${current?.kind === "produce-story" ? "Cancel full run" : "Cancel current"}</button><button data-control="cancel-all" ${current || pending ? "" : "disabled"}>Cancel all queued</button><button data-control="retry">Retry failed / cancelled</button></div><div class="queue-jobs">${jobs
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
    .join("")}</div>`;
  el.querySelectorAll("[data-control]").forEach(
    (b) =>
      (b.onclick = () =>
        action(async () => {
          queue = await api("control", { action: b.dataset.control });
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
  await saveSettingsIfVisible();
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
  $("#productionFullVideo").onclick = () =>
    action(async () => {
      await saveSettingsIfVisible();
      queue = await api("jobs", { project: project.id, kind: "produce-story" });
      renderQueue();
      note(
        "Full video queued. Keep the shared helper running. Pause, cancel or retry here; completed work is saved and reused.",
      );
    });
  $("#productionConnect").onclick = () => action(connect);
  $("#productionNewProject").onclick = () => action(newProject);
  $("#productionProject").onchange = (e) =>
    action(async () => {
      project = connected
        ? await api("project?id=" + e.target.value)
        : await loadStudioProject(e.target.value);
      chapterId = project.chapters[0].id;
      scenePage = 0;
      await cache();
      render();
    });
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
        })),
  );
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
  $("#productionBackup").onclick = () =>
    action(async () => {
      downloadBlob(
        new Blob([JSON.stringify(project, null, 2)], {
          type: "application/json",
        }),
        project.name + ".json",
      );
      note(
        "Project JSON exported. Generated files remain in the helper output folder; copy that project folder for a complete asset backup.",
      );
    });
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
  if (tab === "settings") wireSettings();
}
async function shotAction(sid, act) {
  const s = shots().find((s) => s.id === sid);
  if (act === "generate") return submit("image", [sid]);
  if (act === "qc") return submit("qc", [sid]);
  if (act === "accept-image")
    return patch(
      "shot",
      sid,
      {
        qc: {
          ...s.qc,
          status: "PASSED",
          pass: true,
          reviewer: "MANUAL",
          reviewedAt: Date.now() / 1000,
        },
        status: "PASSED",
      },
      true,
    );
  if (act === "none") return patch("shot", sid, { characters: [] }, true);
  if (act === "edit") {
    editShot(s);
    return;
  }
  if (act === "repair") {
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
  const modal = document.createElement("div");
  modal.className = "dialog-backdrop";
  modal.innerHTML = `<div class="dialog-content" role="dialog" aria-modal="true" aria-label="${escape(title)}"><div class="toolbar"><h2 style="flex:1">${escape(title)}</h2><button class="dialog-close">Close</button></div>${body}<p class="notice error dialog-error" hidden></p><div class="toolbar"><button class="primary dialog-save">Save</button></div></div>`;
  root.append(modal);
  modal.querySelector(".dialog-close").onclick = () => modal.remove();
  modal.querySelector(".dialog-save").onclick = async () => {
    try {
      await onSave();
      modal.remove();
    } catch (error) {
      const el = modal.querySelector(".dialog-error");
      el.hidden = false;
      el.textContent = error.message;
    }
  };
  modal.querySelector("input,textarea,select,button")?.focus();
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
  if (connected) project = await api("create", { name: "My story" });
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
function wireSettings() {
  $("#productionSaveSettings").onclick = () => action(saveSettings);
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
  $("#introAudio").onclick = () => action(() => submit("intro-audio"));
  $("#introGenerate").onclick = () => action(() => submit("intro-image"));
  $("#introPreview").onclick = () => action(() => submit("intro-render"));
  $("#showModelNotes").onclick = () => {
    const a = document.createElement("a");
    a.href = "./STUDIO-IMPLEMENTATION.md";
    a.target = "_blank";
    a.click();
  };
}
async function saveSettings() {
  const s = structuredClone(project.settings),
    intro = structuredClone(project.intro);
  s.style = $("#settingStyle").value;
  s.layoutMode = $("#settingLayout").value;
  s.generationMode = $("#settingGenerationMode").value;
  s.voice = $("#settingVoice").value;
  s.speed = Number($("#settingSpeed").value);
  s.director.provider = $("#settingDirectorProvider").value;
  s.director.reasoning = $("#settingReasoning").value;
  s.continuityStrictness = $("#settingContinuity").value;
  s.appearanceHandling = $("#settingAppearance").value;
  s.maxImageRetries = Number($("#settingRetries").value);
  s.visionQC = $("#settingVision").checked;
  s.automaticRepair = $("#settingRepair").checked;
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
  Object.assign(intro, {
    enabled: $("#introEnabled").checked,
    duration: Number($("#introDuration").value),
    placement: $("#introPlacement").value,
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
  try {
    queue = await api("queue");
    renderQueue();
    if (
      !dirty &&
      !root.querySelector(".dialog-backdrop") &&
      tab !== "settings"
    ) {
      const revision = await api("revision?id=" + project.id);
      if (revision.revision !== project.revision) {
        const latest = await api("project?id=" + project.id);
        const active = document.activeElement;
        if (
          !root.contains(active) ||
          !["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName)
        ) {
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
const savedId = localStorage.getItem("qt-production-project");
if (savedId) project = await loadStudioProject(savedId);
if (!project) {
  const existing = await listStudioProjects();
  project = existing[0] || offlineProject();
  if (existing.length) projects = existing;
}
chapterId = project.chapters[0].id;
await cache();
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
    async () => {
      const v = JSON.parse($("#profileAdvanced").value);
      delete v.id;
      v.name = $("#profileName").value;
      v.description = $("#profileDescription").value;
      v.type = $("#profileType").value;
      root
        .querySelectorAll("[data-identity-field]")
        .forEach(
          (i) => (v.permanentIdentity[i.dataset.identityField] = i.value),
        );
      root
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
        ["comfyui", "ComfyUI workflow"],
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
