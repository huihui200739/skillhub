// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import typography from '@tailwindcss/typography'

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: { extend: {} },
  plugins: [typography],
}
