// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

type PublishDrawerContextValue = {
  /** 抽屉是否打开 */
  open: boolean
  /** 打开发布 Skill 抽屉 */
  openPublish: () => void
  /** 关闭抽屉 */
  closePublish: () => void
}

const Ctx = createContext<PublishDrawerContextValue | null>(null)

export function usePublishDrawer(): PublishDrawerContextValue {
  const ctx = useContext(Ctx)
  if (!ctx) {
    throw new Error('usePublishDrawer 必须在 <PublishDrawerProvider /> 内使用')
  }
  return ctx
}

/**
 * 仅负责维护抽屉开关状态，本文件**不导入**任何业务组件，避免 HMR 下因为
 * 跨模块热更新导致 context 与 Provider 分裂为两份实例。
 * 抽屉的实际渲染在 `App.tsx` 内通过 {@link usePublishDrawer} 消费状态完成。
 */
export function PublishDrawerProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)

  const openPublish = useCallback(() => setOpen(true), [])
  const closePublish = useCallback(() => setOpen(false), [])

  const value = useMemo(
    () => ({ open, openPublish, closePublish }),
    [open, openPublish, closePublish],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
