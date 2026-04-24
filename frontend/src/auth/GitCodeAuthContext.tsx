import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import axios from 'axios'
import { fetchGitCodeMe } from '@/api/auth'
import {
  clearGitCodeSession,
  getStoredGitCodeToken,
  getStoredGitCodeUser,
  setGitCodeSession,
  type GitCodeUser,
} from './gitcodeStorage'

type GitCodeAuthState = {
  token: string | null
  user: GitCodeUser | null
  isAuthenticated: boolean
  /** Skill 上架审核管理员（配置文件用户名，与 /auth/me 一致） */
  isMarketModerationAdmin: boolean
  login: (token: string, user: GitCodeUser) => void
  logout: () => void
}

const GitCodeAuthContext = createContext<GitCodeAuthState | null>(null)

export function GitCodeAuthProvider({ children }: { children: ReactNode }) {
  /**
   * 首帧即读 sessionStorage，避免刷新 /profile 等页时先渲染「未登录」、误跳 /login，
   * 再在登录页被当成已登录却未写入 postLoginRedirect 而落到默认「/」市场首页。
   */
  const [token, setToken] = useState<string | null>(() => getStoredGitCodeToken())
  const [user, setUser] = useState<GitCodeUser | null>(() => getStoredGitCodeUser())

  /**
   * 避免 React.StrictMode（dev）双挂载导致 /auth/me 请求两次。
   * useRef 在 StrictMode 的 mount → 模拟 unmount → 再 mount 过程中保留值，
   * 第二次 effect 执行时通过 guard 直接跳过；不做 abort，让请求自然完成。
   */
  const didRefreshRef = useRef(false)

  useEffect(() => {
    if (didRefreshRef.current) return
    didRefreshRef.current = true

    const t = getStoredGitCodeToken()
    const u = getStoredGitCodeUser()
    setToken(t)
    setUser(u)
    if (!t) return

    fetchGitCodeMe(t)
      .then(profile => {
        setUser(profile)
        setGitCodeSession(t, profile)
      })
      .catch(err => {
        if (axios.isCancel(err)) return
        const name = (err && (err.name as string)) || ''
        if (name === 'CanceledError' || name === 'AbortError') return
        clearGitCodeSession()
        setToken(null)
        setUser(null)
      })
  }, [])

  const login = useCallback((t: string, u: GitCodeUser) => {
    setGitCodeSession(t, u)
    setToken(t)
    setUser(u)
  }, [])

  const logout = useCallback(() => {
    clearGitCodeSession()
    setToken(null)
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({
      token,
      user,
      isAuthenticated: Boolean(token),
      isMarketModerationAdmin: Boolean(user?.is_market_moderation_admin),
      login,
      logout,
    }),
    [token, user, login, logout],
  )

  return <GitCodeAuthContext.Provider value={value}>{children}</GitCodeAuthContext.Provider>
}

export function useGitCodeAuth(): GitCodeAuthState {
  const ctx = useContext(GitCodeAuthContext)
  if (!ctx) {
    throw new Error('useGitCodeAuth must be used within GitCodeAuthProvider')
  }
  return ctx
}
