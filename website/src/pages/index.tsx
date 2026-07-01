import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import useBaseUrl from '@docusaurus/useBaseUrl';
import Heading from '@theme/Heading';

type Copy = {
  badge: string;
  title: string;
  subtitle: string;
  primary: string;
  secondary: string;
  github: string;
  alpha: string;
  previewLabel: string;
  pillarsTitle: string;
  pillarsBody: string;
  pillars: Array<{title: string; body: string}>;
  flowTitle: string;
  flowBody: string;
  flowItems: string[];
  installTitle: string;
  installBody: string;
};

const copy: Record<string, Copy> = {
  en: {
    badge: 'Alpha developer preview',
    title: 'Build file-backed, self-evolving Agent Cores.',
    subtitle:
      'Demiurge keeps the host runtime in control while Agent Cores, slot modules, and package repositories stay readable, installable, and evolvable as files.',
    primary: 'Read the manual',
    secondary: 'Quick Start',
    github: 'View on GitHub',
    alpha:
      'APIs, runtime layout, and authoring contracts may still change while the project is in alpha.',
    previewLabel: 'agent core surface',
    pillarsTitle: 'A manual-first runtime for people and Agent Cores.',
    pillarsBody:
      'The public manual documents both user workflows and the stable contracts that the evolver core can read before editing a candidate core.',
    pillars: [
      {
        title: 'Host-owned harness',
        body:
          'The host owns sessions, turns, provider requests, tool execution, approvals, state, delivery, and rollback.',
      },
      {
        title: 'Independent Agent Cores',
        body:
          'Each core carries identity, instructions, modules, skills, tools, schedules, MCP declarations, lib code, and tests.',
      },
      {
        title: 'Modular capabilities',
        body:
          'Input and output modules shape prompts, responses, artifacts, and routes without taking over host-owned capabilities.',
      },
      {
        title: 'Capability packages',
        body:
          'Package recipes install reusable tools, IO modules, skills, libraries, and child cores into runtime agent cores.',
      },
    ],
    flowTitle: 'The core boundary',
    flowBody:
      'Demiurge is built around one rule: agent cores can evolve quickly, but risky effects stay behind host controls.',
    flowItems: [
      'User or channel input enters the host runner.',
      'Input modules add current-turn context.',
      'The host assembles context and calls the provider.',
      'Tools run through host registry, workspace, and approval checks.',
      'Output modules deliver text, media, artifacts, or structured results.',
    ],
    installTitle: 'Quick Start',
    installBody: 'Use the fake provider first to verify the runtime without an API key.',
  },
  'zh-CN': {
    badge: 'Alpha 开发者预览',
    title: '打造文件化、可自进化的 Agent Core',
    subtitle:
      'Demiurge 让 host runtime 保持控制，同时让 Agent Core、slot modules 和 package repositories 作为可读、可安装、可演进的文件存在。',
    primary: '阅读手册',
    secondary: '快速开始',
    github: '查看 GitHub',
    alpha: '项目仍处于 alpha 阶段，API、runtime 布局和 authoring contract 可能继续变化。',
    previewLabel: 'agent core surface',
    pillarsTitle: '同时服务人类作者和 Agent Core 的手册。',
    pillarsBody: '公开手册既覆盖用户 workflow，也记录 evolver core 修改 candidate core 前可读取的稳定 contracts。',
    pillars: [
      {
        title: 'Host-owned harness',
        body: 'host 负责 session、turn、provider request、工具执行、审批、状态、delivery 和 rollback。',
      },
      {
        title: '独立 Agent Core',
        body: '每个 core 承载个性、指令、模块、skills、tools、schedules、MCP 声明、lib 和 tests。',
      },
      {
        title: '模块化能力',
        body: 'input/output 模块可以塑造 prompt、回复、artifact 和路由，同时不接管 host-owned capabilities。',
      },
      {
        title: '能力包管理',
        body: 'Package recipes 可以把可复用 tools、IO 模块、skills、libraries 和子 Core 安装进 runtime agent core。',
      },
    ],
    flowTitle: '核心边界',
    flowBody: 'Demiurge 的核心规则是：agent core 可以快速演进，但危险效果必须经过 host 控制。',
    flowItems: [
      '用户或 channel input 进入 host runner。',
      'input modules 添加当前 turn context。',
      'host 组装 context 并调用 provider。',
      'tools 通过 host registry、workspace 和 approval checks 执行。',
      'output modules 交付文本、媒体、artifact 或结构化结果。',
    ],
    installTitle: '快速开始',
    installBody: '先使用 fake provider 验证 runtime，不需要 API key。',
  },
};

function HomepageHeader() {
  const {i18n} = useDocusaurusContext();
  const text = copy[i18n.currentLocale] ?? copy.en;
  const logoUrl = useBaseUrl('/img/demiurge-icon-rounded.png');
  return (
    <header className="hero hero--demiurge">
      <div className="container hero__inner">
        <div className="hero__copy">
          <div className="hero__badge">{text.badge}</div>
          <Heading as="h1" className="hero__title">
            {text.title}
          </Heading>
          <p className="hero__subtitle">{text.subtitle}</p>
          <div className="hero__actions">
            <Link className="button button--lg heroButton heroButton--primary" to="/docs/">
              {text.primary}
            </Link>
            <Link className="button button--lg heroButton heroButton--soft" to="/docs/tutorials/quick-start">
              {text.secondary}
            </Link>
            <Link className="button button--lg heroButton heroButton--ghost" href="https://github.com/AllenReder/demiurge-agent">
              {text.github}
            </Link>
          </div>
          <p className="hero__note">{text.alpha}</p>
        </div>
        <div className="corePreview" aria-label="Demiurge agent core layout">
          <div className="corePreview__header">
            <img src={logoUrl} alt="" className="corePreview__logo" />
            <span>{text.previewLabel}</span>
          </div>
          <pre>{`assistant/
  agent.yaml
  agent/
    SOUL.md
    bootstrap/
    input/
    output/
    tools/
    skills/
    schedules/
    mcp/
    lib/
    tests/`}</pre>
        </div>
      </div>
    </header>
  );
}

function Pillars() {
  const {i18n} = useDocusaurusContext();
  const text = copy[i18n.currentLocale] ?? copy.en;
  return (
    <section className="section section--pillars">
      <div className="container">
        <div className="sectionHeader">
          <Heading as="h2">{text.pillarsTitle}</Heading>
          <p>{text.pillarsBody}</p>
        </div>
        <div className="featureGrid">
          {text.pillars.map((pillar) => (
            <article className="featureCard" key={pillar.title}>
              <Heading as="h3">{pillar.title}</Heading>
              <p>{pillar.body}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function BoundaryFlow() {
  const {i18n} = useDocusaurusContext();
  const text = copy[i18n.currentLocale] ?? copy.en;
  return (
    <section className="section section--boundary">
      <div className="container split">
        <div>
          <Heading as="h2">{text.flowTitle}</Heading>
          <p className="sectionLead">{text.flowBody}</p>
        </div>
        <ol className="flowList">
          {text.flowItems.map((item) => (
            <li className="flowStep" key={item}>
              {item}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function InstallBlock() {
  const {i18n} = useDocusaurusContext();
  const text = copy[i18n.currentLocale] ?? copy.en;
  return (
    <section className="section section--install">
      <div className="container installBlock">
        <div>
          <Heading as="h2">{text.installTitle}</Heading>
          <p>{text.installBody}</p>
        </div>
        <pre>{`scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake`}</pre>
      </div>
    </section>
  );
}

export default function Home(): JSX.Element {
  const {i18n} = useDocusaurusContext();
  const text = copy[i18n.currentLocale] ?? copy.en;
  return (
    <Layout title="Demiurge" description={text.subtitle}>
      <HomepageHeader />
      <main>
        <Pillars />
        <BoundaryFlow />
        <InstallBlock />
      </main>
    </Layout>
  );
}
