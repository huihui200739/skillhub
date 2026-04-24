import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { AlertCircle, ArrowRight, Loader2, ShieldCheck, Sparkles } from 'lucide-react'
import {
  exchangeGitCodeOAuthSession,
  getOAuthGitCodeStartUrl,
  GITCODE_OAUTH_PENDING_KEY,
} from '@/api/auth'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { POST_LOGIN_REDIRECT_KEY, sanitizePostLoginPath } from '@/auth/postLoginRedirect'
import { AppHeader } from '@/components/Common/AppHeader'
import jiuwenLogo from '@/assets/jiuwen-logo.png'

export default function LoginPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const { login, isAuthenticated } = useGitCodeAuth()
  const [commonError, setCommonError] = useState('')
  const [exchanging, setExchanging] = useState(false)

  useEffect(() => {
    if (!isAuthenticated) return
    if (location.pathname !== '/login') return
    let next = '/'
    try {
      const raw = sessionStorage.getItem(POST_LOGIN_REDIRECT_KEY)
      if (raw) {
        sessionStorage.removeItem(POST_LOGIN_REDIRECT_KEY)
        next = sanitizePostLoginPath(raw, '/')
      }
    } catch {
      /* ignore */
    }
    navigate(next, { replace: true })
  }, [isAuthenticated, navigate, location.pathname])

  useEffect(() => {
    const oauthErr = searchParams.get('oauth_error')
    if (!oauthErr) return
    try {
      setCommonError(decodeURIComponent(oauthErr.replace(/\+/g, ' ')))
    } catch {
      setCommonError(oauthErr)
    }
    navigate('/login', { replace: true })
  }, [searchParams, navigate])

  useEffect(() => {
    const fromUrl = searchParams.get('oauth_session')
    if (fromUrl) {
      sessionStorage.setItem(GITCODE_OAUTH_PENDING_KEY, fromUrl)
      navigate('/login', { replace: true })
      return
    }
    const sid = sessionStorage.getItem(GITCODE_OAUTH_PENDING_KEY)
    if (!sid) return
    sessionStorage.removeItem(GITCODE_OAUTH_PENDING_KEY)
    setExchanging(true)
    setCommonError('')
    exchangeGitCodeOAuthSession(sid)
      .then(data => {
        login(data.access_token, data.user)
      })
      .catch(e => {
        const msg = e instanceof Error ? e.message : t('auth.oauth.genericError')
        setCommonError(msg)
      })
      .finally(() => setExchanging(false))
  }, [searchParams, navigate, login, t])

  const startGitCode = () => {
    setCommonError('')
    window.location.href = getOAuthGitCodeStartUrl()
  }

  return (
    <div className="flex h-dvh min-h-0 flex-col overflow-hidden bg-gradient-to-br from-[#E3F2FD] to-[#F3E9FF]">
      <AppHeader showPublish={false} />
      <main className="relative flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-4 py-10">
        <div
          aria-hidden
          className="pointer-events-none absolute -top-24 -left-24 h-72 w-72 rounded-full bg-[#1E54F9] opacity-[0.08] blur-3xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-32 -right-20 h-80 w-80 rounded-full bg-[#852EFE] opacity-[0.10] blur-3xl"
        />

        <div className="animate-notice-in relative w-full max-w-md">
          <div className="relative overflow-hidden rounded-3xl border border-white/60 bg-white/80 p-8 shadow-[0_24px_60px_-20px_rgba(79,70,229,0.25)] backdrop-blur-xl">
            <span
              aria-hidden
              className="pointer-events-none absolute inset-x-0 top-0 h-[3px] bg-gradient-to-r from-[#1E54F9] via-[#6366F1] to-[#852EFE]"
            />

            <div className="mb-6 flex flex-col items-center text-center">
              <div className="relative mb-4 flex h-16 w-16 items-center justify-center">
                <span
                  aria-hidden
                  className="absolute -inset-1 rounded-2xl bg-gradient-to-br from-[#1E54F9]/25 to-[#852EFE]/25 blur-lg"
                />
                <div className="relative flex h-16 w-16 items-center justify-center rounded-2xl border border-white/80 bg-white shadow-[0_10px_30px_-10px_rgba(79,70,229,0.35)] ring-1 ring-slate-100">
                  <img
                    src={jiuwenLogo}
                    alt=""
                    aria-hidden
                    draggable={false}
                    className="h-10 w-10 select-none"
                  />
                </div>
              </div>
              <h1 className="text-[22px] font-bold tracking-tight text-slate-900">
                {t('auth.login.title')}
              </h1>
              <p className="mt-2 text-[13.5px] leading-relaxed text-slate-500">
                {t('auth.login.subtitle')}
              </p>
            </div>

            {commonError ? (
              <div className="mb-4 flex items-start gap-2 rounded-xl border border-red-200/80 bg-red-50/80 px-3 py-2.5 text-sm text-red-800">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" aria-hidden />
                <span className="leading-relaxed">{commonError}</span>
              </div>
            ) : null}

            {exchanging ? (
              <div className="mb-4 flex items-center justify-center gap-2 rounded-xl border border-indigo-100/80 bg-indigo-50/60 px-3 py-2.5 text-sm text-indigo-700">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                <span>{t('auth.oauth.exchanging')}</span>
              </div>
            ) : null}

            <button
              type="button"
              disabled={exchanging}
              onClick={startGitCode}
              className="group relative inline-flex h-12 w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-gradient-to-r from-[#1E54F9] to-[#852EFE] px-5 text-[15px] font-semibold text-white shadow-[0_10px_24px_-10px_rgba(79,70,229,0.65)] transition-all hover:shadow-[0_14px_28px_-10px_rgba(79,70,229,0.75)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe] disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span
                aria-hidden
                className="absolute inset-0 bg-gradient-to-r from-white/0 via-white/20 to-white/0 opacity-0 transition-opacity group-hover:opacity-100"
              />
              <Sparkles className="relative h-4 w-4" aria-hidden />
              <span className="relative">{t('auth.login.gitcodeButton')}</span>
              <ArrowRight className="relative h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
            </button>

            <div className="mt-5 flex items-start gap-2 rounded-xl bg-slate-50/80 px-3 py-2.5">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" aria-hidden />
              <p className="text-[12px] leading-relaxed text-slate-500">
                {t('auth.login.hintGitcodeSession')}
              </p>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
