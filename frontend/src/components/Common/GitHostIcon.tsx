// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { GitBranch } from 'lucide-react'
import { type KnownGitHostBrandId, resolveKnownGitHostBrandId } from '@/utils/gitHostBrand'

const BRAND_SRC: Record<KnownGitHostBrandId, string> = {
  github: 'github.svg',
  gitee: 'gitee.svg',
  gitcode: 'gitcode.svg',
}

function brandIconSrc(brand: KnownGitHostBrandId): string {
  const base = import.meta.env.BASE_URL.endsWith('/') ? import.meta.env.BASE_URL : `${import.meta.env.BASE_URL}/`
  return `${base}git-hosts/${BRAND_SRC[brand]}`
}

export type GitHostIconProps = {
  /** 仓库克隆地址（https / http / git@ 均可）；空字符串则视为无匹配。 */
  repoUrl: string
  /** 命中品牌时用于 `<img>` 的 class（尺寸建议与 fallback 一致）。 */
  className?: string
  /** 未命中品牌时用于 `GitBranch` 的 class。不传则使用 `className`。 */
  fallbackClassName?: string
}

/** 热门托管商显示站点 SVG 图标，否则显示默认分支图标。 */
export function GitHostIcon({ repoUrl, className = 'h-5 w-5 shrink-0', fallbackClassName }: GitHostIconProps) {
  const brand = resolveKnownGitHostBrandId(repoUrl)
  if (brand) {
    return (
      <img
        key={brand}
        src={brandIconSrc(brand)}
        alt=""
        className={`object-contain ${className}`.trim()}
        draggable={false}
        aria-hidden
      />
    )
  }
  return <GitBranch className={fallbackClassName ?? className} aria-hidden />
}
