/**
 * pages/Login.tsx
 * Pro dark bento PIN login page.
 * Solid near-black (#0b0b0c) card on #000 page, 20px radius, 1px border at rgba(255,255,255,0.08).
 * No glows, no gradients.
 */

import React, { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Shield, Lock, Delete, ArrowRight, AlertCircle } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { APP_NAME, APP_TAGLINE } from '@/lib/constants'

const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === 'true'

export function LoginPage() {
  const [pin, setPin] = useState('')
  const [error, setError] = useState<string | null>(null)
  const { login, isAuthenticated, isLoading } = useAuth()
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isAuthenticated) {
      navigate('/live', { replace: true })
    }
  }, [isAuthenticated, navigate])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const handleKeyPress = (digit: string) => {
    if (pin.length < 8) {
      setError(null)
      setPin(prev => prev + digit)
    }
  }

  const handleDelete = () => {
    setError(null)
    setPin(prev => prev.slice(0, -1))
  }

  const handleClear = () => {
    setError(null)
    setPin('')
  }

  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    if (!pin || pin.length < 4) {
      setError('PIN must be at least 4 digits')
      return
    }

    try {
      setError(null)
      await login(pin)
      navigate('/live', { replace: true })
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Invalid PIN. Please try again.'
      if (msg.includes('401')) {
        setError('Incorrect PIN. Please try again.')
      } else if (msg.includes('429')) {
        setError('Too many attempts. Please wait 1 minute.')
      } else {
        setError(msg)
      }
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      handleSubmit()
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-black text-white">
      <div className="w-full max-w-sm">
        {/* Card */}
        <div
          className="rounded-[20px] border p-8 relative overflow-hidden"
          style={{
            background: '#0b0b0c',
            borderColor: 'rgba(255, 255, 255, 0.08)',
          }}
        >
          {/* Brand & Header */}
          <div className="flex flex-col items-center text-center mb-6">
            <div className="w-12 h-12 rounded-[14px] flex items-center justify-center mb-3 bg-white/10 border border-white/15">
              <Shield className="w-6 h-6 text-white" />
            </div>
            <h1 className="text-[28px] font-semibold tracking-[-0.02em] text-white">
              {APP_NAME}
            </h1>
            <p className="text-xs text-[var(--text-secondary)] mt-1 font-normal">{APP_TAGLINE}</p>

            {USE_MOCKS && (
              <div className="mt-3 inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-mono text-[var(--text-secondary)] bg-white/5 border border-white/10">
                <span>Mock Mode Active — PIN: any 4 digits</span>
              </div>
            )}
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* PIN Display Input */}
            <div>
              <label
                htmlFor="pin-input"
                className="block text-[11px] font-mono font-medium uppercase tracking-wider text-[var(--text-muted)] mb-2 text-center"
              >
                Security PIN
              </label>

              <div className="relative">
                <input
                  id="pin-input"
                  ref={inputRef}
                  type="password"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  maxLength={8}
                  value={pin}
                  onChange={e => {
                    const val = e.target.value.replace(/\D/g, '')
                    setPin(val)
                    setError(null)
                  }}
                  onKeyDown={handleKeyDown}
                  className="w-full text-center text-xl tracking-[0.5em] font-mono py-2.5 px-4 rounded-[12px] border bg-black text-white focus:outline-none focus:border-white/40 transition-colors placeholder:tracking-normal placeholder:font-sans placeholder:text-xs"
                  style={{
                    borderColor: error ? '#f43f5e' : 'rgba(255, 255, 255, 0.1)',
                  }}
                  placeholder="••••"
                  autoFocus
                />
                <div className="absolute left-3.5 top-1/2 -translate-y-1/2 text-[var(--text-muted)] pointer-events-none">
                  <Lock className="w-3.5 h-3.5" />
                </div>
              </div>

              {/* PIN Indicator Dots */}
              <div className="flex justify-center gap-2 mt-3" aria-hidden="true">
                {[0, 1, 2, 3].map(idx => (
                  <div
                    key={idx}
                    className="w-2 h-2 rounded-full transition-all duration-150"
                    style={{
                      background:
                        pin.length > idx
                          ? '#ffffff'
                          : 'rgba(255, 255, 255, 0.15)',
                    }}
                  />
                ))}
              </div>
            </div>

            {/* Error Message */}
            {error && (
              <div
                className="flex items-center gap-2 p-2.5 rounded-[10px] text-xs font-mono border"
                style={{
                  background: 'rgba(244, 63, 94, 0.1)',
                  borderColor: 'rgba(244, 63, 94, 0.25)',
                  color: '#f43f5e',
                }}
                role="alert"
              >
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            {/* Numeric Keypad */}
            <div className="grid grid-cols-3 gap-2 pt-1">
              {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map(num => (
                <button
                  key={num}
                  type="button"
                  onClick={() => handleKeyPress(num)}
                  disabled={isLoading}
                  className="h-11 rounded-[12px] text-base font-mono font-medium border bg-black text-white hover:bg-white/10 active:scale-95 transition-all cursor-pointer"
                  style={{
                    borderColor: 'rgba(255, 255, 255, 0.08)',
                  }}
                >
                  {num}
                </button>
              ))}
              <button
                type="button"
                onClick={handleClear}
                disabled={isLoading || pin.length === 0}
                className="h-11 rounded-[12px] text-xs font-mono font-medium border bg-black text-[var(--text-secondary)] hover:text-white hover:bg-white/10 transition-colors cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
                style={{
                  borderColor: 'rgba(255, 255, 255, 0.08)',
                }}
                title="Clear input"
              >
                Clear
              </button>
              <button
                type="button"
                onClick={() => handleKeyPress('0')}
                disabled={isLoading}
                className="h-11 rounded-[12px] text-base font-mono font-medium border bg-black text-white hover:bg-white/10 active:scale-95 transition-all cursor-pointer"
                style={{
                  borderColor: 'rgba(255, 255, 255, 0.08)',
                }}
              >
                0
              </button>
              <button
                type="button"
                onClick={handleDelete}
                disabled={isLoading || pin.length === 0}
                className="h-11 rounded-[12px] flex items-center justify-center border bg-black text-[var(--text-secondary)] hover:text-white hover:bg-white/10 transition-colors cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
                style={{
                  borderColor: 'rgba(255, 255, 255, 0.08)',
                }}
                title="Backspace"
                aria-label="Backspace"
              >
                <Delete className="w-4 h-4" />
              </button>
            </div>

            {/* Login Button */}
            <button
              type="submit"
              disabled={isLoading || pin.length < 4}
              className="w-full flex items-center justify-center gap-2 h-10 px-4 rounded-[12px] text-xs font-semibold bg-white text-[#0b0b0c] hover:bg-white/90 active:scale-[0.99] transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {isLoading ? (
                <span>Authenticating...</span>
              ) : (
                <>
                  <span>Sign In</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </>
              )}
            </button>
          </form>

          {/* Footer notice */}
          <div className="mt-5 text-center text-[11px] font-mono text-[var(--text-muted)]">
            Authorized personnel only. Sessions expire after 12h.
          </div>
        </div>
      </div>
    </div>
  )
}
