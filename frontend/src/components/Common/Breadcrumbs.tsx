// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { Fragment } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, Home } from 'lucide-react'

export type BreadcrumbItem = {
  /** 展示文本；当前页（最后一项）通常不用 to。 */
  label: string
  /** 提供 to 则渲染为链接，点击跳转；最后一项不渲染链接。 */
  to?: string
  /** 首项可显式放图标；不传则首项默认用 Home（当 to 为 '/' 时）。 */
  icon?: React.ReactNode
}

/**
 * 轻量面包屑。最后一项高亮为当前页，其余是浅色可点击链接；中间用 chevron 分隔。
 * 不包含容器内外边距，方便放入不同页面的 header 区域。
 */
export function Breadcrumbs({
  items,
  className = '',
  ariaLabel,
}: {
  items: BreadcrumbItem[]
  className?: string
  ariaLabel?: string
}) {
  if (items.length === 0) return null

  return (
    <nav aria-label={ariaLabel ?? 'Breadcrumb'} className={className}>
      <ol className="flex min-w-0 flex-wrap items-center gap-0.5 text-[13px]">
        {items.map((item, idx) => {
          const isLast = idx === items.length - 1
          const isRoot = idx === 0
          const icon =
            item.icon ?? (isRoot && item.to === '/' ? <Home className="h-3.5 w-3.5" aria-hidden /> : null)

          const content = (
            <span className={`inline-flex min-w-0 items-center gap-1 ${isLast ? 'truncate' : ''}`}>
              {icon}
              <span className={`${isLast ? 'truncate' : ''}`}>{item.label}</span>
            </span>
          )

          return (
            <Fragment key={`${item.label}-${idx}`}>
              <li className="flex min-w-0 items-center">
                {item.to && !isLast ? (
                  <Link
                    to={item.to}
                    className="inline-flex max-w-full items-center rounded-md px-1.5 py-1 text-slate-500 transition-colors hover:bg-slate-100/80 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe]"
                  >
                    {content}
                  </Link>
                ) : (
                  <span
                    aria-current={isLast ? 'page' : undefined}
                    className={`inline-flex max-w-full items-center px-1.5 py-1 ${
                      isLast ? 'font-medium text-slate-900' : 'text-slate-500'
                    }`}
                    title={isLast ? item.label : undefined}
                  >
                    {content}
                  </span>
                )}
              </li>
              {!isLast ? (
                <li className="flex items-center text-slate-300" aria-hidden>
                  <ChevronRight className="h-3.5 w-3.5 shrink-0" />
                </li>
              ) : null}
            </Fragment>
          )
        })}
      </ol>
    </nav>
  )
}

export default Breadcrumbs
