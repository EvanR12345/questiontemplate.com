// Keep one prepared section and at most one synthesis request ahead. The
// consumer can encode the current PCM while the native GPU renders the next.
export async function* streamSynthesis(batches, render, {
  gate = async () => true, prefetch = false, canPrefetch = () => true,
} = {}) {
  const input = batches[Symbol.asyncIterator]();
  let next = input.next(), flight;
  next.catch(() => {});
  try {
    while (await gate()) {
      const item = await next;
      if (item.done) break;
      next = input.next();
      next.catch(() => {});
      const batch = item.value;
      if (!batch.ids) { yield { batch, audio: null }; continue; }
      const audio = await (flight || render(batch));
      flight = null;
      if (prefetch && canPrefetch()) {
        // A preparation failure belongs to the following section. Commit the
        // current audio first, then propagate that failure on the next step.
        let following;
        try { following = await next; } catch {}
        if (following && !following.done && following.value.ids && canPrefetch()) {
          flight = Promise.resolve().then(() => render(following.value));
          flight.catch(() => {});
        }
      }
      yield { batch, audio };
    }
  } finally {
    // A canceled prefetched section is not committed. Drain its request before
    // reporting completion so the helper is available to the next recording.
    if (flight) await flight.catch(() => {});
    await next.catch(() => {});
    await input.return?.();
  }
}
