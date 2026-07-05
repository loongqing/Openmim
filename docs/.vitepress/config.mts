import { defineConfig } from 'vitepress'

// https://vitepress.dev/reference/site-config
export default defineConfig({
  title: "Openmim",
  description: "An open-source AI bot development platform.",
  lang: 'zh-CN',
  themeConfig: {
    logo: '/logo.png',
    // https://vitepress.dev/reference/default-theme-config
    nav: [
      { text: '首页', link: '/' },
      { text: '快速开始', link: '/guide/getting-started' },
      { text: '配置参考', link: '/config/overview' },
    ],

    sidebar: [
      {
        text: '指南',
        items: [
          { text: '什么是 Openmim？', link: '/guide/what-is-openmim' },
          { text: '快速开始', link: '/guide/getting-started' },
          { text: '项目结构', link: '/guide/project-structure' },
          { text: '贡献指南', link: '/guide/contributing' },
        ]
      },
      {
        text: '核心功能',
        items: [
          { text: '记忆系统', link: '/features/memory' },
          { text: '聚焦插话', link: '/features/focus' },
          { text: 'Agentic 能力', link: '/features/agentic' },
          { text: '拟人化设计', link: '/features/humanization' },
          { text: 'Business 模式', link: '/features/business' },
          { text: 'Guest 模式', link: '/features/guest-mode' },
          { text: '可玩性功能', link: '/features/playables' },
        ]
      },
      {
        text: '配置参考',
        items: [
          { text: '配置总览', link: '/config/overview' },
          { text: 'LLM 配置', link: '/config/llm' },
          { text: '沙箱配置', link: '/config/sandbox' },
          { text: 'Web Panel', link: '/config/web-panel' },
          { text: '群组设置', link: '/config/group-settings' },
          { text: '定制化', link: '/config/customization' },
        ]
      },
      {
        text: '进阶',
        items: [
          { text: '插件系统', link: '/advanced/plugins' },
          { text: 'Skills 市场', link: '/advanced/skills' },
          { text: '管理面板', link: '/advanced/admin-panel' },
        ]
      },
    ],

    socialLinks: [
      { icon: 'github', link: 'https://github.com/loongqing/Openmim' }
    ],

    footer: {
      message: '基于 python-telegram-bot 构建',
      copyright: 'Copyright © 2024-present Openmim Contributors'
    },

    search: {
      provider: 'local'
    }
  }
})
