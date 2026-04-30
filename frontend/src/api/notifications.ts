// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { getApiClient } from './client'

export type SiteNotificationItem = {
  id: number
  template: string
  message: string
  created_at_ms: number
  read: boolean
}

export type SiteNotificationListData = {
  items: SiteNotificationItem[]
  unread_count: number
}

type ResponseEnvelope<T> = { code: number; message?: string; data?: T }

export async function fetchNotifications(): Promise<SiteNotificationListData | null> {
  const { data } = await getApiClient().get<ResponseEnvelope<SiteNotificationListData>>('/notifications')
  if (data.code !== 200 || !data.data) {
    return null
  }
  return data.data
}

export async function markAllNotificationsRead(): Promise<boolean> {
  const { data } = await getApiClient().post<ResponseEnvelope<{ updated?: number }>>('/notifications/read-all')
  return data.code === 200
}
