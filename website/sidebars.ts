import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docs: [
    'README',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/quickstart',
        'getting-started/configure-provider',
        'getting-started/update-and-doctor',
      ],
    },
    {
      type: 'category',
      label: 'Concepts',
      collapsed: false,
      items: [
        'concepts/host-and-agent-core',
        'concepts/runtime-home',
        'concepts/sessions-and-context',
        'concepts/security-model',
      ],
    },
    {
      type: 'category',
      label: 'Authoring',
      collapsed: false,
      items: [
        'authoring/agent-core-layout',
        'authoring/bootstrap-modules',
        'authoring/input-modules',
        'authoring/output-modules',
        'authoring/authored-tools',
        'authoring/skills',
        'authoring/mcp',
        'authoring/schedules',
        'authoring/packages',
        'authoring/testing-agent-cores',
      ],
    },
    {
      type: 'category',
      label: 'Operations',
      collapsed: false,
      items: [
        'operations/configuration',
        'operations/channels',
        'operations/telegram',
        'operations/package-management',
        'operations/troubleshooting',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      collapsed: true,
      items: [
        'reference/cli',
        'reference/agent-yaml',
        'reference/slot-yaml',
        'reference/tools',
        'reference/capabilities',
        'reference/history-policy-and-delivery',
        'reference/package-recipes',
        'reference/runtime-layout',
      ],
    },
    {
      type: 'category',
      label: 'Developer Guide',
      collapsed: true,
      items: [
        'developer-guide/architecture',
        'developer-guide/runner-and-context',
        'developer-guide/tool-runtime',
        'developer-guide/delivery-runtime',
        'developer-guide/scheduler',
        'developer-guide/mcp-runtime',
        'developer-guide/package-installer',
      ],
    },
    {
      type: 'category',
      label: 'Releases',
      collapsed: true,
      items: ['releases/0.2.0'],
    },
  ],
};

export default sidebars;
