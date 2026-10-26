/**
 * lib/audio.ts
 * Pure Web Audio API tone generator for critical safety alerts.
 * Generates an urgent short beep without any external audio libraries or sound files.
 */

let audioCtx: AudioContext | null = null

export function playCriticalBeep(): void {
  try {
    const AudioContextClass =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext

    if (!AudioContextClass) return

    if (!audioCtx || audioCtx.state === 'closed') {
      audioCtx = new AudioContextClass()
    }

    if (audioCtx.state === 'suspended') {
      audioCtx.resume()
    }

    const now = audioCtx.currentTime

    const osc = audioCtx.createOscillator()
    const gain = audioCtx.createGain()

    osc.type = 'sine'
    // Urgent 2-tone step: A5 (880Hz) to D6 (1174Hz)
    osc.frequency.setValueAtTime(880, now)
    osc.frequency.setValueAtTime(1174.66, now + 0.1)

    // Gain envelope for a crisp 250ms beep
    gain.gain.setValueAtTime(0.25, now)
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.28)

    osc.connect(gain)
    gain.connect(audioCtx.destination)

    osc.start(now)
    osc.stop(now + 0.3)
  } catch {
    // Gracefully handle browser policy or user gesture restrictions
  }
}
