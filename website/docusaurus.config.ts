import type {Config} from '@docusaurus/types';
import type {Options as ClassicPresetOptions, ThemeConfig} from '@docusaurus/preset-classic';

const config: Config = {
  title: 'Demiurge',
  tagline: 'Build file-backed, self-evolving Agent Cores under a host-owned runtime boundary.',
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
      title: 'Demiurge',
      logo: {
        alt: 'Demiurge logo',
        src: 'img/demiurge-icon-rounded.png',
      },
      items: [
        {
          to: '/docs/',
          label: 'Docs',
          position: 'left',
        },
        {
          to: '/docs/tutorials/quick-start',
          label: 'Quick Start',
          position: 'left',
        },
        {
          to: '/docs/explanation/agent-slots',
          label: 'Agent Slots',
          position: 'left',
        },
        {
          to: '/docs/how-to/install-packages',
          label: 'Packages',
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
              label: 'Quick Start',
              to: '/docs/tutorials/quick-start',
            },
            {
              label: 'Customize a Core',
              to: '/docs/tutorials/customize-agent-core',
            },
            {
              label: 'Contracts',
              to: '/docs/reference/contracts/authored-surface',
            },
          ],
        },
        {
          title: 'How-to',
          items: [
            {
              label: 'Provider Setup',
              to: '/docs/how-to/configure-provider',
            },
            {
              label: 'Packages',
              to: '/docs/how-to/install-packages',
            },
            {
              label: 'Security',
              to: '/docs/explanation/security-model',
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
              to: '/docs/releases/0.5.0',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Demiurge contributors.`,
    },
    prism: {
      additionalLanguages: ['bash', 'python', 'yaml'],
    },
  } satisfies ThemeConfig,
};

export default config;
