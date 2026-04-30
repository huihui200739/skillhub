// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { PluginMarkdown } from '@/components/Common/PluginMarkdown'
import { SiteFooter } from '@/components/Common/SiteFooter'
import { getPrivacyStatementViewUrl } from '@/utils/privacyStatementUrl'

export default function PrivacyStatementPage() {
  const { t } = useTranslation()
  const [text, setText] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    document.title = t('privacyPolicy.pageTitle')
    return () => {
      document.title = t('appHeader.brand')
    }
  }, [t])

  useEffect(() => {
    let cancelled = false
    const url = getPrivacyStatementViewUrl()
    fetch(url)
      .then(res => {
        if (!res.ok) throw new Error(String(res.status))
        return res.text()
      })
      .then(body => {
        if (!cancelled) setText(body)
      })
      .catch(() => {
        if (!cancelled) setErr('failed')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="flex min-h-dvh flex-col bg-slate-50">
      <header className="shrink-0 border-b border-slate-200 bg-white px-4 py-3">
        <Link to="/" className="text-sm font-medium text-blue-600 underline-offset-2 hover:underline">
          {t('privacyPolicy.backHome')}
        </Link>
      </header>
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
        {err && <p className="text-sm text-red-600">{t('privacyPolicy.loadError')}</p>}
        {text === null && !err && <p className="text-sm text-slate-600">{t('privacyPolicy.loading')}</p>}
        {text !== null && (
          <article className="rounded-lg border border-slate-200/80 bg-white px-5 py-6 shadow-sm">
            <PluginMarkdown source={text} className="prose prose-slate max-w-none prose-headings:scroll-mt-20" />
          </article>
        )}
      </main>
      <SiteFooter />
    </div>
  )
}
