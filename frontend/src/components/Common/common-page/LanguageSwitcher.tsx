// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Globe } from 'lucide-react'
import { createPortal } from 'react-dom'
import type { SupportedLocale } from '@/i18n'

const LOCALE_CONFIG: { code: SupportedLocale; labelKey: 'common.lang.zh' | 'common.lang.en' }[] = [
  { code: 'zh-CN', labelKey: 'common.lang.zh' },
  { code: 'en-US', labelKey: 'common.lang.en' },
]

function resolveActive(code: SupportedLocale, resolved: string): boolean {
  const r = resolved || 'zh-CN'
  if (code === 'zh-CN') {
    return r === 'zh-CN' || r.startsWith('zh')
  }
  return r === 'en-US' || (r.startsWith('en') && !r.startsWith('zh'))
}

export const LanguageSwitcher = () => {
  const { i18n, t } = useTranslation()
  const resolved = i18n.resolvedLanguage ?? i18n.language
  const [open, setOpen] = useState(false)
  const btnRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [coords, setCoords] = useState<{ top: number; right: number } | null>(null)

  const current = LOCALE_CONFIG.find(c => resolveActive(c.code, resolved)) ?? LOCALE_CONFIG[0]

  useEffect(() => {
    if (!open) { setCoords(null); return }
    const el = btnRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setCoords({ top: r.bottom + 6, right: window.innerWidth - r.right })
    const onScrollOrResize = () => {
      const r2 = el.getBoundingClientRect()
      setCoords({ top: r2.bottom + 6, right: window.innerWidth - r2.right })
    }
    window.addEventListener('scroll', onScrollOrResize, true)
    window.addEventListener('resize', onScrollOrResize)
    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true)
      window.removeEventListener('resize', onScrollOrResize)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onClickOutside = (e: MouseEvent) => {
      const t = e.target as Node
      if (btnRef.current?.contains(t) || menuRef.current?.contains(t)) return
      setOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onClickOutside)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onClickOutside)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const menu =
    open && coords ? (
      <div
        ref={menuRef}
        style={{ position: 'fixed', top: coords.top, right: coords.right, zIndex: 1400 }}
        className="w-28 overflow-hidden rounded-lg border border-slate-200/90 bg-white py-1 shadow-[0_4px_16px_rgba(15,23,42,0.10)]"
        role="menu"
      >
        {LOCALE_CONFIG.map(({ code, labelKey }) => {
          const active = resolveActive(code, resolved)
          return (
            <button
              key={code}
              type="button"
              role="menuitem"
              onClick={() => { void i18n.changeLanguage(code); setOpen(false) }}
              className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm transition-colors hover:bg-slate-50 ${
                active ? 'font-medium text-slate-800' : 'text-slate-500'
              }`}
            >
              <span>{t(labelKey)}</span>
              {active && <Check className="h-3.5 w-3.5 text-[#1d4ed8]" aria-hidden />}
            </button>
          )
        })}
      </div>
    ) : null

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('common.lang.switchAria')}
        onClick={() => setOpen(prev => !prev)}
        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe]"
      >
        <Globe className="h-4 w-4 shrink-0" aria-hidden />
      </button>
      {typeof document !== 'undefined' && menu ? createPortal(menu, document.body) : null}
    </>
  )
}

export default LanguageSwitcher
