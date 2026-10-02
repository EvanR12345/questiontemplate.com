// Estimates use completed inference sections, never saved audio from a prior run.
// The caller supplies active seconds (model loading and pauses excluded).
export class GenerationEstimate {
  constructor() { this.samples = []; this.last = { words: 0, seconds: 0 }; }
  record(words, seconds) {
    const delta = { words: words - this.last.words, seconds: seconds - this.last.seconds };
    if (delta.words <= 0 || delta.seconds <= 0) return;
    this.samples.push(delta);
    this.samples = this.samples.slice(-9);
    this.last = { words, seconds };
  }
  remaining(words, seconds) {
    if (words <= 0) return { seconds: 0, delayed: false };
    if (this.samples.length < 2) return null;
    // The first inference includes cold kernels / compilation. Prefer steady
    // speed once enough sections have actually finished.
    const samples = this.samples.length >= 3 ? this.samples.slice(1) : this.samples;
    const total = samples.reduce((a, b) => ({ words: a.words + b.words, seconds: a.seconds + b.seconds }), { words: 0, seconds: 0 });
    if (total.words < 25) return null;
    const section = total.seconds / samples.length;
    const waiting = Math.max(0, seconds - this.last.seconds);
    return {
      seconds: Math.max(section, words * total.seconds / total.words - waiting),
      delayed: waiting > section * 2.5,
    };
  }
}

export function measuredPace(words, audioSeconds, speed = 1) {
  if (words < 25 || audioSeconds < 8 || speed <= 0) return null;
  const baseWpm = words / audioSeconds * 60 / speed;
  return Number.isFinite(baseWpm) && baseWpm >= 30 && baseWpm <= 450 ? baseWpm : null;
}

export function narrationEstimate(words, speed, baseWpm) {
  const measured = Number.isFinite(baseWpm) && baseWpm >= 30 && baseWpm <= 450;
  const wpm = (measured ? baseWpm : 155) * speed;
  const seconds = words / wpm * 60;
  const margin = measured ? .1 : .25;
  return { wpm, seconds, low: seconds * (1 - margin), high: seconds * (1 + margin), measured };
}
