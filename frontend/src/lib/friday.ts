/**
 * lib/friday.ts
 * Friday on the screen: one small store shared by the Friday page and the rest of the app.
 *
 *   listen()  -> records the microphone (the blob follows YOUR voice), stop() sends it to /assistant/voice-command
 *   ask(text) -> typed order to /assistant/command
 *   then      -> the answer is spoken in the Rumik voice (/assistant/speak) and the blob follows HER voice;
 *                without Rumik, the browser's own voice speaks and the blob pulses with each word
 *   big actions (alert everyone, evacuate, fire station) -> she asks, and "yes" / "no" goes to /assistant/confirm
 *
 * The loudness (0..1) is read by the blob every frame through level(), so React never re-renders for audio.
 */
import { useSyncExternalStore } from 'react'
import { assistantCommand, assistantConfirm, getAuthToken } from './api'
import type { FridayState } from '@/components/friday/FridayBlob'

const API_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/+$/, '')

export interface FridayTurn {
  at: number
  you: string
  friday: string
  intent?: string
  status?: string
}

interface Snapshot {
  state: FridayState
  heard: string // what you said (live caption)
  answer: string // what Friday said
  confirmToken: string | null // waiting for "yes" on a big action
  turns: FridayTurn[]
  error: string | null
  cameraIds: string[] | null // "show cameras 3, 4 and 12": the page switches to them
  openPath: string | null // "show the recording of the fire": the page to open (an incident, recordings)
  voice: string | null // who spoke last: "Rumik Silk" or the browser voice's name
}

let snap: Snapshot = { state: 'idle', heard: '', answer: '', confirmToken: null, turns: [], error: null, cameraIds: null, openPath: null, voice: null }
const listeners = new Set<() => void>()
function set(patch: Partial<Snapshot>) {
  snap = { ...snap, ...patch }
  listeners.forEach(l => l())
}

export function useFriday(): Snapshot {
  return useSyncExternalStore(cb => (listeners.add(cb), () => listeners.delete(cb)), () => snap)
}

/* ── loudness ───────────────────────────────────────────── */

let ctx: AudioContext | null = null
let analyser: AnalyserNode | null = null
let pulse = 0 // used by the browser-voice fallback, which gives no audio samples
const buf = new Uint8Array(1024)

function audioContext(): AudioContext {
  if (!ctx) ctx = new AudioContext()
  if (ctx.state === 'suspended') void ctx.resume()
  return ctx
}

/** 0..1, read every animation frame by the blob. */
export function level(): number {
  if (analyser) {
    analyser.getByteTimeDomainData(buf)
    let sum = 0
    for (let i = 0; i < buf.length; i++) {
      const v = (buf[i] - 128) / 128
      sum += v * v
    }
    return Math.min(1, Math.sqrt(sum / buf.length) * 4)
  }
  pulse *= 0.9
  return pulse
}

/* ── speaking ───────────────────────────────────────────── */

let currentSource: AudioBufferSourceNode | null = null
let speakTurn = 0 // each speak() gets a number; an older one that is still fetching audio never plays

function stopSpeaking() {
  try {
    currentSource?.stop()
  } catch {
    /* already stopped */
  }
  currentSource = null
  window.speechSynthesis?.cancel()
}

// Friday is a woman's voice. Windows, Chrome and Edge name their voices, so pick by name (Indian English first)
// and never fall back to a male one (Ravi, David, Mark, Prabhat...).
const FEMALE = /heera|neerja|swara|kalpana|zira|aria|jenny|sonia|libby|natasha|samantha|karen|moira|tessa|veena|female|woman/i
const MALE = /ravi|prabhat|david|mark|guy|ryan|george|daniel|alex|fred|male(?!.*female)/i

async function browserVoices(): Promise<SpeechSynthesisVoice[]> {
  const now = window.speechSynthesis.getVoices()
  if (now.length) return now
  return new Promise(resolve => {  // Chrome loads the list a moment after the page
    const done = () => resolve(window.speechSynthesis.getVoices())
    window.speechSynthesis.addEventListener('voiceschanged', done, { once: true })
    window.setTimeout(done, 800)
  })
}

export function pickFemaleVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const english = voices.filter(v => /^en/i.test(v.lang))
  const female = english.filter(v => FEMALE.test(v.name) && !/male(?!.*female)/i.test(v.name.replace(/female/gi, '')))
  return female.find(v => /en-IN/i.test(v.lang)) ?? female[0] ?? english.find(v => !MALE.test(v.name)) ?? null
}

async function speakWithBrowser(text: string): Promise<void> {
  if (!('speechSynthesis' in window)) return
  const voice = pickFemaleVoice(await browserVoices())
  set({ voice: voice ? `browser voice: ${voice.name}` : 'browser voice' })
  await new Promise<void>(resolve => {
    const u = new SpeechSynthesisUtterance(text)
    u.voice = voice
    if (!voice) u.pitch = 1.25 // no named female voice installed: at least a higher pitch
    u.rate = 1.05
    u.onboundary = () => (pulse = 0.75)
    const tick = window.setInterval(() => (pulse = Math.max(pulse, 0.25 + Math.random() * 0.25)), 120)
    u.onend = u.onerror = () => {
      window.clearInterval(tick)
      resolve()
    }
    window.speechSynthesis.speak(u)
  })
}

/** Speak an answer: Rumik voice when it is set up, otherwise the browser's own voice. */
export async function speak(text: string, style: 'calm' | 'urgent' = 'calm'): Promise<void> {
  if (!text) return
  const turn = ++speakTurn
  stopSpeaking()
  set({ state: snap.state === 'alarm' ? 'alarm' : 'speaking', answer: text })
  try {
    const res = await fetch(`${API_URL}/assistant/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getAuthToken() ?? ''}` },
      body: JSON.stringify({ text, style }),
    })
    if (!res.ok) throw new Error(`speak ${res.status}`)
    const audio = await res.arrayBuffer()
    if (turn !== speakTurn) return // a newer answer came while this one was loading: never two voices at once
    set({ voice: 'Rumik Silk' })
    await playAudio(audio)
  } catch {
    if (turn !== speakTurn) return
    analyser = null
    await speakWithBrowser(text)
  }
  if (snap.state === 'speaking') set({ state: 'idle' })
}

/** Play a WAV (or any decodable audio) through an analyser so the blob follows it. */
export async function playAudio(data: ArrayBuffer): Promise<void> {
  const ac = audioContext()
  const audio = await ac.decodeAudioData(data)
  const source = ac.createBufferSource()
  source.buffer = audio
  analyser = ac.createAnalyser()
  analyser.fftSize = 1024
  source.connect(analyser)
  analyser.connect(ac.destination)
  currentSource = source
  await new Promise<void>(resolve => {
    source.onended = () => resolve()
    source.start()
  })
  analyser = null
}

/* ── listening ──────────────────────────────────────────── */

let recorder: MediaRecorder | null = null
let micStream: MediaStream | null = null

export async function listen(): Promise<void> {
  if (recorder) return
  stopSpeaking()
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
  } catch {
    set({ error: 'Microphone blocked: allow it in the browser, or type instead.' })
    return
  }
  const ac = audioContext()
  analyser = ac.createAnalyser()
  analyser.fftSize = 1024
  ac.createMediaStreamSource(micStream).connect(analyser) // not to the speakers: no echo
  const chunks: Blob[] = []
  recorder = new MediaRecorder(micStream)
  recorder.ondataavailable = e => e.data.size && chunks.push(e.data)
  recorder.onstop = () => void send(new Blob(chunks, { type: recorder?.mimeType || 'audio/webm' }))
  recorder.start()
  set({ state: 'listening', heard: '', error: null })
}

export function stop(): void {
  if (!recorder) return
  recorder.stop()
  micStream?.getTracks().forEach(t => t.stop())
  analyser = null
}

async function send(audio: Blob): Promise<void> {
  recorder = null
  set({ state: 'thinking' })
  const form = new FormData()
  form.append('audio', audio, 'speech.webm')
  try {
    const res = await fetch(`${API_URL}/assistant/voice-command`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${getAuthToken() ?? ''}` },
      body: form,
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `error ${res.status}`)
    const out = await res.json()
    await answered(out.transcript || '…', out)
  } catch (e) {
    set({ state: 'idle', error: e instanceof Error ? e.message : 'Friday could not hear you' })
  }
}

/* ── typed orders and confirmations ─────────────────────── */

export async function ask(text: string): Promise<void> {
  const order = text.trim()
  if (!order) return
  stopSpeaking()
  if (snap.confirmToken && /^(yes|yeah|yep|confirm|do it|no|cancel|stop)\b/i.test(order)) {
    return confirm(order, /^(yes|yeah|yep|confirm|do it)/i.test(order))
  }
  set({ state: 'thinking', heard: order, error: null })
  try {
    await answered(order, await assistantCommand(order))
  } catch (e) {
    set({ state: 'idle', error: e instanceof Error ? e.message : 'Friday is not reachable' })
  }
}

async function confirm(said: string, yes: boolean): Promise<void> {
  const token = snap.confirmToken!
  set({ state: 'thinking', heard: said, confirmToken: null })
  try {
    const out = await assistantConfirm(token, yes)
    await answered(said, { intent: 'confirm', status: out.status, answer: out.answer })
  } catch (e) {
    set({ state: 'idle', error: e instanceof Error ? e.message : 'That confirmation expired' })
  }
}

async function answered(said: string, out: { intent?: string; status?: string; answer: string; confirm_token?: string | null; data?: Record<string, unknown> }) {
  const ids = out.data && Array.isArray(out.data.camera_ids) && out.intent === 'show_camera' ? (out.data.camera_ids as string[]) : null
  set({
    heard: said,
    confirmToken: out.status === 'needs_confirmation' ? out.confirm_token ?? null : null,
    turns: [{ at: Date.now(), you: said, friday: out.answer, intent: out.intent, status: out.status }, ...snap.turns].slice(0, 30),
    cameraIds: ids,
    openPath: out.data && typeof out.data.open_path === 'string' ? out.data.open_path : null,
  })
  await speak(out.answer)
}

/* ── from the wake-word listener and alarms (WebSocket "friday" notices) ── */

export function showState(state: FridayState, fields: { heard?: string; answer?: string } = {}): void {
  set({ state, ...fields })
}

/* ── one speaker per user ─────────────────────────────────
 * The same user may have the app open twice (Edge and another browser). The server tells each dashboard
 * whether it is the newest one; only that one speaks alarms and wake-word answers, so nothing is heard twice. */
let speakerHere = true
export function setSpeaker(active: boolean): void {
  speakerHere = active
}
export function isSpeaker(): boolean {
  return speakerHere
}

export function clearCameraRequest(): void {
  set({ cameraIds: null, openPath: null })
}
