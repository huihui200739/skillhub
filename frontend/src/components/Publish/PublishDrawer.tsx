import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from 'react-query'
import { X } from 'lucide-react'
import { PublishForm } from './PublishForm'

type PublishDrawerProps = {
  open: boolean
  onClose: () => void
}

/** 右侧发布抽屉：仅用于发布 Skill；点击遮罩或 ESC 关闭。 */
export function PublishDrawer({ open, onClose }: PublishDrawerProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!open) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = prevOverflow
    }
  }, [open, onClose])

  const handleSuccess = () => {
    void queryClient.invalidateQueries({ queryKey: ['my-published-skills'] })
    void queryClient.invalidateQueries({ queryKey: ['publish-my-plugins'] })
    void queryClient.invalidateQueries({ queryKey: ['market-configs'] })
  }

  return (
    <div
      className={`fixed inset-0 z-50 ${open ? 'pointer-events-auto' : 'pointer-events-none'}`}
      role="dialog"
      aria-modal="true"
      aria-hidden={!open}
      aria-label={t('publish.titleSkill')}
    >
      <button
        type="button"
        aria-label={t('appHeader.closePublish')}
        onClick={onClose}
        tabIndex={open ? 0 : -1}
        className={`absolute inset-0 h-full w-full cursor-default bg-slate-900/45 transition-opacity duration-200 ${
          open ? 'opacity-100' : 'opacity-0'
        }`}
      />

      <aside
        className={`absolute inset-y-0 right-0 flex h-full w-full max-w-[520px] flex-col bg-white shadow-[-20px_0_40px_-12px_rgba(15,23,42,0.25)] transition-transform duration-300 ease-out ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
        onClick={e => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3 px-7 pb-4 pt-6">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="inline-flex h-6 w-1 rounded-full bg-[linear-gradient(180deg,#1E54F9_0%,#852EFE_100%)]" />
              <h2 className="text-[17px] font-semibold text-[#0F172A]">
                {t('publish.titleSkill')}
              </h2>
            </div>
            <p className="mt-2 pl-3 text-[12px] leading-5 text-[#6B7280]">
              {t('publish.introSkill')}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('appHeader.closePublish')}
            className="-mt-1 -mr-2 rounded-md p-1.5 text-[#94A3B8] transition-colors hover:bg-slate-100 hover:text-[#0F172A]"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </header>

        <div className="mx-7 mb-2 h-px bg-gradient-to-r from-transparent via-slate-200 to-transparent" />

        {open ? <PublishForm onCancel={onClose} onSuccess={handleSuccess} /> : null}
      </aside>
    </div>
  )
}

export default PublishDrawer
