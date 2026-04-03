const SOUNDS_ENABLED_KEY = "genlab-sounds-enabled";

export function isSoundEnabled(): boolean {
  return localStorage.getItem(SOUNDS_ENABLED_KEY) === "true";
}

export function toggleSound(): boolean {
  const current = isSoundEnabled();
  localStorage.setItem(SOUNDS_ENABLED_KEY, current ? "false" : "true");
  return !current;
}

// Singleton AudioContext — avoids leaking one per playSound() call
let _audioCtx: AudioContext | null = null;
function getAudioContext(): AudioContext {
  if (!_audioCtx) _audioCtx = new AudioContext();
  return _audioCtx;
}

export function playSound(type: "approve" | "reject" | "publish" | "notification"): void {
  if (!isSoundEnabled()) return;
  // Use Web Audio API for a simple tone
  try {
    const ctx = getAudioContext();
    // Resume AudioContext if suspended due to browser autoplay policy
    if (ctx.state === "suspended") {
      void ctx.resume();
    }
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);

    const frequencies: Record<string, number> = {
      approve: 880,
      reject: 330,
      publish: 1047,
      notification: 660,
    };

    osc.frequency.value = frequencies[type] ?? 440;
    osc.type = "sine";
    gain.gain.value = 0.05; // Very quiet
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);

    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.15);
  } catch {
    // Audio not available — silent fallback
  }
}
