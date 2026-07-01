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
  composeTitle: string;
  composeItems: string[];
  governanceTitle: string;
  governanceItems: string[];
  loopItems: string[];
  pillarsTitle: string;
  pillarsBody: string;
  pillars: Array<{title: string; body: string}>;
  demoTitle: string;
  demoBody: string;
  demos: Array<{title: string; body: string; src: string; label: string}>;
  flowTitle: string;
  flowBody: string;
  flowItems: string[];
  installTitle: string;
  installBody: string;
};

const copy: Record<string, Copy> = {
  en: {
    badge: 'Alpha developer preview',
    title: 'Build self-evolving agents with Agent Slots.',
    subtitle:
      'Agent Slots are governed interaction boundaries where an Agent Core can shape the loop, compose tools, skills, MCP, state, or child agents, and evolve without changing the host harness.',
    primary: 'Understand Agent Slots',
    secondary: 'Quick Start',
    github: 'View on GitHub',
    alpha:
      'APIs, runtime layout, and authoring contracts may still change while the project is in alpha.',
    previewLabel: 'agent slot loop',
    composeTitle: 'Slots can compose',
    composeItems: ['tools', 'skills', 'MCP', 'state', 'child agents'],
    governanceTitle: 'Host governance',
    governanceItems: ['approvals', 'delivery', 'rollback'],
    loopItems: ['Inbound', 'Bootstrap / input slots', 'Provider + tool loop', 'Output slots'],
    pillarsTitle: 'A runtime shaped around evolvable agent boundaries.',
    pillarsBody:
      'Demiurge keeps feature behavior in Agent Core files and packages while the host owns the risky runtime machinery.',
    pillars: [
      {
        title: 'Agent Slots',
        body:
          'Slots define where Core-defined behavior enters the loop and what governed effects it may request.',
      },
      {
        title: 'Package-composed capabilities',
        body:
          'Packages install slots, tools, skills, libraries, and child cores without adding feature code to the harness.',
      },
      {
        title: 'Host-governed effects',
        body:
          'Provider calls, tool dispatch, approvals, state, delivery, promotion, and rollback stay host-owned.',
      },
      {
        title: 'Versionable Core files',
        body:
          'Agent Cores stay readable, diffable, testable, and promotable as ordinary files.',
      },
    ],
    demoTitle: 'How Agent Slots Work',
    demoBody:
      'Input and output behavior can be installed as package-owned Agent Slots while the host keeps provider access, approvals, and delivery under control.',
    demos: [
      {
        title: 'Speech-to-text input',
        body:
          'An STT package adds an input slot that turns voice into governed turn context without changing the host loop.',
        src: '/media/slot-packages/stt-package-demo.mp4',
        label: 'STT package Agent Slot demo',
      },
      {
        title: 'Text-to-speech output',
        body:
          'A TTS package adds an output slot that renders spoken replies while delivery remains host-owned.',
        src: '/media/slot-packages/tts-package-demo.mp4',
        label: 'TTS package Agent Slot demo',
      },
    ],
    flowTitle: 'How Slots fit the loop',
    flowBody:
      'Tools act. Skills guide. MCP connects. Slots decide where behavior enters the agent loop.',
    flowItems: [
      'User, channel, or schedule input enters the host runner.',
      'Bootstrap and input slots shape session and turn context.',
      'The provider and tool loop runs under host governance.',
      'Slots can compose tools, MCP, state, skills, or child agents.',
      'Output slots deliver text, media, artifacts, or structured results.',
      'The evolver can propose slot changes; the host gates promotion.',
    ],
    installTitle: 'Try it locally',
    installBody: 'Use the fake provider first to verify the runtime without an API key.',
  },
  'zh-CN': {
    badge: 'Alpha 开发者预览',
    title: '用 Agent Slots 构建可自进化 Agent',
    subtitle:
      'Agent Slots 是受 Host 治理的交互边界，让 Agent Core 可以塑造 loop，组合 tools、skills、MCP、state 或子 Agent，并在不修改 harness 的情况下演进。',
    primary: '理解 Agent Slots',
    secondary: '快速开始',
    github: '查看 GitHub',
    alpha: '项目仍处于 alpha 阶段，API、runtime 布局和 authoring contract 可能继续变化。',
    previewLabel: 'agent slot loop',
    composeTitle: 'Slots 可以组合',
    composeItems: ['tools', 'skills', 'MCP', 'state', '子 Agent'],
    governanceTitle: 'Host 治理',
    governanceItems: ['approvals', 'delivery', 'rollback'],
    loopItems: ['Inbound', 'Bootstrap / input slots', 'Provider + tool loop', 'Output slots'],
    pillarsTitle: '围绕可演化 Agent 边界设计的 runtime。',
    pillarsBody: 'Demiurge 把具体能力留在 Agent Core 文件和 packages 中，把高风险 runtime 机制留给 Host 治理。',
    pillars: [
      {
        title: 'Agent Slots',
        body: 'Slot 定义 Core 定义的行为逻辑在哪里介入 loop，以及它可以请求哪些受治理的效果。',
      },
      {
        title: 'Package 组合能力',
        body: 'Package 可以安装 slots、tools、skills、libraries 和子 Core，而不把具体 feature 写进 harness。',
      },
      {
        title: 'Host 治理效果',
        body: 'Provider calls、tool dispatch、approvals、state、delivery、promotion 和 rollback 都保持 host-owned。',
      },
      {
        title: '可版本化 Core 文件',
        body: 'Agent Core 作为普通文件保持可读、可 diff、可测试、可 promote。',
      },
    ],
    demoTitle: 'Agent Slots 如何工作',
    demoBody:
      'Input 和 output 行为可以作为 package-owned Agent Slots 安装；provider access、approvals 和 delivery 仍由 Host 治理。',
    demos: [
      {
        title: 'Speech-to-text input',
        body: 'STT package 添加 input slot，把语音转成受治理的 turn context，不需要改 host loop。',
        src: '/media/slot-packages/stt-package-demo.mp4',
        label: 'STT package Agent Slot 演示',
      },
      {
        title: 'Text-to-speech output',
        body: 'TTS package 添加 output slot，把回复渲染成语音，同时 delivery 仍保持 host-owned。',
        src: '/media/slot-packages/tts-package-demo.mp4',
        label: 'TTS package Agent Slot 演示',
      },
    ],
    flowTitle: 'Slots 如何进入 loop',
    flowBody: 'Tool 负责行动，Skill 负责指导，MCP 负责连接；Slot 决定行为在 agent loop 的哪里介入。',
    flowItems: [
      '用户、channel 或 schedule input 进入 host runner。',
      'Bootstrap 和 input slots 塑造 session 与 turn context。',
      'Provider 和 tool loop 在 Host 治理下运行。',
      'Slots 可以组合 tools、MCP、state、skills 或子 Agent。',
      'Output slots 交付文本、媒体、artifact 或结构化结果。',
      'Evolver 可以提出 slot 修改，host 负责 gate 和 promotion。',
    ],
    installTitle: '本地试运行',
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
            <Link className="button button--lg heroButton heroButton--primary" to="/docs/explanation/agent-slots">
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
        <div className="slotMap" aria-label="Demiurge Agent Slot loop">
          <div className="slotMap__header">
            <img src={logoUrl} alt="" className="slotMap__logo" />
            <span>{text.previewLabel}</span>
          </div>
          <div className="slotMap__loop">
            {text.loopItems.map((item, index) => (
              <div className={`slotNode slotNode--${index === 0 ? 'inbound' : index === 2 ? 'host' : 'slot'}`} key={item}>
                {item}
              </div>
            ))}
          </div>
          <div className="slotMap__compose">
            <span>{text.composeTitle}</span>
            <div>
              {text.composeItems.map((item) => (
                <b key={item}>{item}</b>
              ))}
            </div>
          </div>
          <div className="slotMap__governance">
            <span>{text.governanceTitle}</span>
            <div>
              {text.governanceItems.map((item) => (
                <b key={item}>{item}</b>
              ))}
            </div>
          </div>
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

function PackageDemoCard({
  demo,
}: {
  demo: {title: string; body: string; src: string; label: string};
}) {
  const videoUrl = useBaseUrl(demo.src);
  return (
    <article className="demoPanel">
      <video
        aria-label={demo.label}
        autoPlay
        loop
        muted
        playsInline
        preload="metadata"
        src={videoUrl}
      />
      <div className="demoPanel__copy">
        <Heading as="h3">{demo.title}</Heading>
        <p>{demo.body}</p>
      </div>
    </article>
  );
}

function PackageDemos() {
  const {i18n} = useDocusaurusContext();
  const text = copy[i18n.currentLocale] ?? copy.en;
  return (
    <section className="section section--demos" id="slot-package-demos">
      <div className="container">
        <div className="sectionHeader sectionHeader--wide">
          <Heading as="h2">{text.demoTitle}</Heading>
          <p>{text.demoBody}</p>
        </div>
        <div className="demoGrid">
          {text.demos.map((demo) => (
            <PackageDemoCard demo={demo} key={demo.src} />
          ))}
        </div>
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
        <PackageDemos />
        <BoundaryFlow />
        <InstallBlock />
      </main>
    </Layout>
  );
}
