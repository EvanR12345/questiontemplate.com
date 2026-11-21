export function createLibraryCopies({list, read, save, report, now = Date.now}) {
  const wanted = new Map(), failed = new Map();
  let running;
  async function update() {
    let local;
    try { local = new Map((await list()).map(row => [row.id, row])); }
    catch { report('Browser storage unavailable. Reconnect the helper to open cloud projects.'); return; }
    for (;;) {
      let next;
      let ready = 0;
      for (const [id, revision] of wanted) {
        const row = local.get(id);
        if (row?.unsynced || row?.revision >= revision) { ready++; continue; }
        const last = failed.get(id);
        if (!next && !(last?.revision === revision && now() - last.at < 60000)) next = {id, revision};
      }
      if (!next) {
        report(ready === wanted.size ? `Saved for editing without the helper on this browser: ${ready} projects.` :
          `Ready for offline editing: ${ready} of ${wanted.size}. Reconnect to retry the other copies.`);
        return;
      }
      const {id, revision} = next;
      report(`Saving offline project copies: ${ready} of ${wanted.size} ready.`);
      try {
        const project = await read(id, revision);
        if (project.id !== id || !Number.isSafeInteger(project.revision) || project.revision < revision ||
            !Array.isArray(project.chapters)) throw Error('Incomplete project checkpoint');
        await save(project);
        local.set(id, {id, revision:project.revision});
        failed.delete(id);
      } catch { failed.set(id, {revision, at:now()}); }
    }
  }
  return rows => {
    for (const row of rows) {
      if (!row.loadError && Number.isSafeInteger(row.revision)) wanted.set(row.id, Math.max(row.revision, wanted.get(row.id) ?? 0));
    }
    if (!running) running = update().finally(() => { running = undefined; });
    return running;
  };
}
