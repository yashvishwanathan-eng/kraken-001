/**
 * pages/Assistant.tsx: Friday's page
 * The blue blob in the middle moves with her voice (and with yours while you talk). Below it: her name, what
 * she is doing, what you said and what she answered. Hold Space (or the mic) to talk, or type. When she asks
 * to confirm a big action, Yes / No appear. "Show cameras 3, 4 and 12" opens the Live page with those cameras.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Mic, Send } from 'lucide-react'
import { FridayBlob } from '@/components/friday/FridayBlob'
import { ask, clearCameraRequest, level, listen, stop, useFriday } from '@/lib/friday'
import { cn } from '@/lib/utils'

const STATE_TEXT = {
  idle: 'Say “Hey Friday”, hold Space, or type',
  listening: 'Listening…',
  thinking: 'Thinking…',
  speaking: 'Speaking',
  alarm: 'Alarm',
} as const

const EXAMPLES = ['Summary for the past hour', 'Who is on duty', 'Show cameras 1 and 2', 'Status report']

export function AssistantPage() {
  const friday = useFriday()
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const holding = useRef(false)
  const getLevel = useCallback(() => level(), [])

  // Hold Space anywhere on the page (not while typing) to talk
  useEffect(() => {
    const typing = (e: KeyboardEvent) => (e.target as HTMLElement)?.closest('input, textarea, select, [contenteditable]')
    const down = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || e.repeat || typing(e)) return
      e.preventDefault()
      holding.current = true
      void listen()
    }
    const up = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || !holding.current) return
      holding.current = false
      stop()
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [])

  // "Show cameras 3, 4 and 12": open them on the Live page
  useEffect(() => {
    if (!friday.cameraIds || friday.state === 'speaking') return
    const ids = friday.cameraIds
    clearCameraRequest()
    navigate(`/live?cameras=${encodeURIComponent(ids.join(','))}`)
  }, [friday.cameraIds, friday.state, navigate])

  const submit = (value: string) => {
    setText('')
    void ask(value)
  }

  const alarm = friday.state === 'alarm'

  return (
    <div className="relative -m-5 flex min-h-[calc(100dvh-60px)] flex-col items-center overflow-hidden px-5 pb-8">
      {/* soft blue (or red) light behind the blob */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 top-[18%] h-[60vh] w-[60vh] -translate-x-1/2 rounded-full blur-3xl transition-colors duration-700"
        style={{ background: alarm ? 'rgba(239,68,68,0.16)' : 'rgba(37,99,235,0.18)' }}
      />

      <FridayBlob state={friday.state} getLevel={getLevel} className="relative z-0 mt-2 h-[46vh] min-h-[260px] w-full max-w-[640px]" />

      <section className="relative z-10 -mt-2 flex w-full max-w-2xl flex-col items-center text-center" aria-live="polite">
        <h1 className="font-mono text-[11px] uppercase tracking-[0.35em]" style={{ color: alarm ? '#f87171' : '#7dd3fc' }}>
          Friday
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">{STATE_TEXT[friday.state]}</p>

        <AnimatePresence mode="wait">
          {friday.heard && (
            <motion.p
              key={`you-${friday.heard}`}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="mt-5 text-[15px] text-[var(--text-secondary)]"
            >
              “{friday.heard}”
            </motion.p>
          )}
        </AnimatePresence>
        <AnimatePresence mode="wait">
          {friday.answer && (
            <motion.p
              key={`friday-${friday.answer}`}
              initial={{ opacity: 0, y: 8, filter: 'blur(4px)' }}
              animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.35 }}
              className="mt-3 text-balance text-xl font-medium leading-snug text-white md:text-2xl"
            >
              {friday.answer}
            </motion.p>
          )}
        </AnimatePresence>

        {friday.confirmToken && (
          <div className="mt-5 flex gap-2">
            <button type="button" onClick={() => submit('yes')} className="rounded-full bg-white px-5 py-2 text-sm font-semibold text-black hover:bg-white/90 cursor-pointer">Yes</button>
            <button type="button" onClick={() => submit('no')} className="rounded-full border border-border px-5 py-2 text-sm text-white hover:bg-white/5 cursor-pointer">No</button>
          </div>
        )}
        {friday.error && <p className="mt-3 text-xs" style={{ color: 'var(--critical)' }}>{friday.error}</p>}
        {friday.voice && (
          <p className="mt-3 font-mono text-[10px] text-[var(--text-muted)]" title={friday.voice === 'Rumik Silk' ? '' : 'Rumik is not set up or did not answer: set RUMIK_API_KEY and RUMIK_GATEWAY_URL, then run python -m tools.check_rumik'}>
            Voice: {friday.voice}{friday.voice === 'Rumik Silk' ? '' : ' (Rumik not reachable)'}
          </p>
        )}
      </section>

      <form
        className="relative z-10 mt-8 flex w-full max-w-xl items-center gap-2 rounded-full border border-border bg-[#0b0b0c]/90 p-1.5 pl-4 backdrop-blur"
        onSubmit={e => {
          e.preventDefault()
          submit(text)
        }}
      >
        <input
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder="Ask Friday…"
          aria-label="Ask Friday"
          className="min-w-0 flex-1 bg-transparent text-sm text-white placeholder:text-white/30 focus:outline-none"
        />
        <button
          type="button"
          aria-label="Hold to talk"
          onPointerDown={() => void listen()}
          onPointerUp={stop}
          onPointerLeave={() => friday.state === 'listening' && stop()}
          className={cn(
            'grid size-9 place-items-center rounded-full transition-colors cursor-pointer',
            friday.state === 'listening' ? 'bg-sky-400 text-black' : 'text-[#7dd3fc] hover:bg-white/5',
          )}
        >
          <Mic className="size-4" />
        </button>
        <button type="submit" aria-label="Send" disabled={!text.trim()} className="grid size-9 place-items-center rounded-full bg-white text-black disabled:opacity-30 cursor-pointer">
          <Send className="size-4" />
        </button>
      </form>

      <div className="relative z-10 mt-3 flex flex-wrap justify-center gap-1.5">
        {EXAMPLES.map(q => (
          <button key={q} type="button" onClick={() => submit(q)} className="rounded-full border border-border px-3 py-1 text-[11px] text-muted-foreground hover:text-white cursor-pointer">
            {q}
          </button>
        ))}
      </div>

      {friday.turns.length > 1 && (
        <ol className="relative z-10 mt-8 w-full max-w-2xl space-y-3 border-t border-border pt-5">
          {friday.turns.slice(1, 8).map(t => (
            <li key={t.at} className="text-left text-[13px]">
              <p className="text-muted-foreground">“{t.you}”</p>
              <p className="mt-0.5 text-white">{t.friday}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
