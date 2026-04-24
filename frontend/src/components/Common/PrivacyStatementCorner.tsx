import { useTranslation } from 'react-i18next'
import { FileText } from 'lucide-react'
import { getPrivacyStatementViewUrl } from '@/utils/privacyStatementUrl'

/** 全站右下角：隐私协议（新标签打开服务端 Markdown）。 */
export function PrivacyStatementCorner() {
  const { t } = useTranslation()
  const viewUrl = getPrivacyStatementViewUrl()

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-40 md:bottom-6 md:right-6"
      role="region"
      aria-label={t('privacyPolicy.cornerRegion')}
    >
      <a
        href={viewUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-slate-200/90 bg-white/95 px-3.5 py-2 text-sm font-medium text-slate-700 shadow-lg shadow-slate-900/10 backdrop-blur-sm underline-offset-2 transition-colors hover:border-slate-300 hover:bg-white hover:text-slate-900 hover:underline"
      >
        <FileText className="h-3.5 w-3.5 shrink-0 opacity-70" aria-hidden />
        {t('privacyPolicy.button')}
      </a>
    </div>
  )
}
