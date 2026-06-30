import type {Config} from '@docusaurus/types';
import type {Options as ClassicPresetOptions, ThemeConfig} from '@docusaurus/preset-classic';

const config: Config = {
  title: 'demiurge',
  tagline: 'Build self-evolving agents with independent Agent Cores, modular capabilities, and installable capability packages.',
  favicon: 'img/demiurge-icon-rounded.png',

  url: 'https://allenreder.github.io',
  baseUrl: '/demiurge-agent/',
  organizationName: 'AllenReder',
  projectName: 'demiurge-agent',

  onBrokenLinks: 'throw',
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'zh-CN'],
    localeConfigs: {
      en: {
        label: 'English',
      },
      'zh-CN': {
        label: '中文',
      },
    },
  },

  presets: [
    [
      'classic',
      {
        docs: {
          path: '../docs',
          routeBasePath: 'docs',
          sidebarPath: './sidebars.ts',
          editUrl: ({docPath}) =>
            `https://github.com/AllenReder/demiurge-agent/edit/main/docs/${docPath}`,
          showLastUpdateAuthor: false,
          showLastUpdateTime: true,
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies ClassicPresetOptions,
    ],
  ],

  plugins: [
    [
      '@easyops-cn/docusaurus-search-local',
      {
        hashed: true,
        indexDocs: true,
        indexBlog: false,
        indexPages: true,
        docsRouteBasePath: '/docs',
        docsDir: '../docs',
        language: ['en', 'zh'],
      },
    ],
  ],

  themeConfig: {
    image: 'img/demiurge-icon-1024.png',
    navbar: {
      title: 'demiurge',
      logo: {
        alt: 'demiurge logo',
        src: 'img/demiurge-icon-rounded.png',
      },
      items: [
        {
          to: '/docs/',
          label: 'Docs',
          position: 'left',
        },
        {
          to: '/docs/getting-started/quickstart',
          label: 'Quickstart',
          position: 'left',
        },
        {
          to: '/docs/authoring/agent-core-layout',
          label: 'Authoring',
          position: 'left',
        },
        {
          type: 'localeDropdown',
          position: 'right',
        },
        {
          href: 'https://github.com/AllenReder/demiurge-agent',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {
              label: 'Quickstart',
              to: '/docs/getting-started/quickstart',
            },
            {
              label: 'Agent Core',
              to: '/docs/concepts/host-and-agent-core',
            },
            {
              label: 'Authoring',
              to: '/docs/authoring/agent-core-layout',
            },
          ],
        },
        {
          title: 'Operations',
          items: [
            {
              label: 'Configuration',
              to: '/docs/operations/configuration',
            },
            {
              label: 'Telegram',
              to: '/docs/operations/telegram',
            },
            {
              label: 'Security',
              to: '/docs/concepts/security-model',
            },
          ],
        },
        {
          title: 'Project',
          items: [
            {
              label: 'GitHub',
              href: 'https://github.com/AllenReder/demiurge-agent',
            },
            {
              label: 'Releases',
              to: '/docs/releases/0.2.0',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} demiurge contributors.`,
    },
    prism: {
      additionalLanguages: ['bash', 'python', 'yaml'],
    },
  } satisfies ThemeConfig,
};

export default config;
