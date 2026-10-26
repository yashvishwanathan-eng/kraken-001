/**
 * lib/auth.tsx
 * Authentication context and provider for RingGuard.
 * Manages PIN-based login session, user profile, and automatic 401 handling.
 */

import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import type { AuthUser, LoginResponse } from './types'
import {
  getAuthToken,
  getStoredUser,
  loginWithPin,
  clearAuthSession,
} from './api'
import { wsClient } from './ws'

interface AuthContextType {
  user: AuthUser | null
  token: string | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (pin: string) => Promise<LoginResponse>
  logout: () => void
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() => getAuthToken())
  const [user, setUser] = useState<AuthUser | null>(() => getStoredUser())
  const [isLoading, setIsLoading] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()

  const logout = useCallback(() => {
    clearAuthSession()
    setToken(null)
    setUser(null)
    wsClient.disconnect()
    if (location.pathname !== '/login') {
      navigate('/login', { replace: true })
    }
  }, [navigate, location.pathname])

  // Handle 401 unauthorized events dispatched from api.ts or ws.ts
  useEffect(() => {
    const handleUnauthorized = () => {
      logout()
    }
    window.addEventListener('auth:unauthorized', handleUnauthorized)
    return () => {
      window.removeEventListener('auth:unauthorized', handleUnauthorized)
    }
  }, [logout])

  // Connect / disconnect WebSocket when auth state changes
  useEffect(() => {
    if (token) {
      wsClient.connect()
    } else {
      wsClient.disconnect()
    }
  }, [token])

  const login = async (pin: string): Promise<LoginResponse> => {
    setIsLoading(true)
    try {
      const res = await loginWithPin(pin)
      setToken(res.token)
      setUser(res.user)
      return res
    } finally {
      setIsLoading(false)
    }
  }

  const value: AuthContextType = {
    user,
    token,
    isAuthenticated: Boolean(token),
    isLoading,
    login,
    logout,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return ctx
}
