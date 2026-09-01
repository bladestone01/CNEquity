---
title: 可日更、可溯源的 A 股研究数据湖
description: 多源采集、本地 Parquet、PIT、历史 Universe 与行级溯源。零注册，自托管。
hide:
  - navigation
  - toc
---

<div class="cne-home" markdown>

<section class="cne-hero">
  <div class="cne-hero__copy">
    <p class="cne-eyebrow">OPEN · SELF-HOSTED · POINT-IN-TIME</p>
    <h1>把分散的 A 股数据，<br><span>变成自己的研究底座</span></h1>
    <p class="cne-lead">CNEquity 将多源行情、基本面、事件与宏观数据持续落到本地 Parquet，统一处理复权、历史 Universe 和 PIT，让每一次研究都可回查、可续跑、可解释。</p>
    <div class="cne-actions">
      <a class="cne-button cne-button--primary" href="getting-started/quickstart/">一分钟开始</a>
      <a class="cne-button cne-button--secondary" href="datasets/catalog/">浏览 42 个数据集</a>
    </div>
    <div class="cne-proof" aria-label="项目特性">
      <span><strong>零注册</strong> 无 API Token</span>
      <span><strong>开放格式</strong> Parquet</span>
      <span><strong>行级溯源</strong> source / version / time</span>
    </div>
  </div>

  <div class="cne-terminal" aria-label="CNEquity 快速安装示例">
    <div class="cne-terminal__bar">
      <span></span><span></span><span></span>
      <b>CNEquity · illustrative flow</b>
    </div>
    <div class="cne-terminal__body">
      <p><i>$</i> pip install cnequity</p>
      <p><i>$</i> cne demo</p>
      <div class="cne-terminal__status"><span></span> TDX connection OK</div>
      <div class="cne-terminal__rows">
        <span>instruments</span><span>curated</span><strong>5 rows</strong>
        <span>daily_bars</span><span>curated</span><strong>150 rows</strong>
        <span>quality</span><span>audit</span><strong>PASS</strong>
      </div>
      <p class="cne-terminal__done">✓ Local lake ready · 5 symbols · 30 sessions</p>
    </div>
  </div>
</section>

<section class="cne-strip" aria-label="项目规模">
  <div><strong>42</strong><span>注册数据集</span></div>
  <div><strong>39 + 3</strong><span>Curated + Derived</span></div>
  <div><strong>3</strong><span>Python · DuckDB · Polars</span></div>
  <div><strong>1</strong><span>日更命令</span></div>
</section>

<section class="cne-section cne-section--intro">
  <p class="cne-kicker">WHY CNEQUITY</p>
  <h2>不是再包一层取数接口，<br>而是把研究口径放进数据层</h2>
  <div class="cne-feature-grid">
    <article>
      <span class="cne-feature-no">01</span>
      <h3>历史不会悄悄改变</h3>
      <p>退市股、历史成分和 PIT 进入统一查询合同，避免用今天的股票名单解释过去。</p>
      <a href="recipes/pit-rebalance/">查看 PIT Recipe →</a>
    </article>
    <article>
      <span class="cne-feature-no">02</span>
      <h3>每一行都能追到来源</h3>
      <p>所有 curated 行保留来源、数据版本和采集时间；结果出现差异时，有证据可查。</p>
      <a href="datasets/contract/">查看数据合同 →</a>
    </article>
    <article>
      <span class="cne-feature-no">03</span>
      <h3>数据源故障不等于重来</h3>
      <p>分批写入、失败续跑、主备源与质量审计，让日更任务在真实网络环境里长期运行。</p>
      <a href="operations/runbook/">查看运行手册 →</a>
    </article>
  </div>
</section>

<section class="cne-section cne-evidence">
  <div class="cne-evidence__copy">
    <p class="cne-kicker">THE INVISIBLE ERROR</p>
    <h2>同一个策略，<br>为什么收益会翻倍？</h2>
    <p>只使用今天仍然上市的股票回看 2016–2021 年，同一等权买入持有策略的收益从 <strong>5.9%</strong> 变成 <strong>12.0%</strong>。那些退市股票并非收益为零，而是根本没有进入计算。</p>
    <a class="cne-text-link" href="recipes/research-baseline/">了解可复查的研究基线 →</a>
  </div>
  <div class="cne-evidence__chart">
    <img src="assets/survivorship-gap.zh.svg" alt="当前股票名单与历史完整股票池造成的收益差异">
  </div>
</section>

<section class="cne-section">
  <p class="cne-kicker">CHOOSE YOUR PATH</p>
  <h2>从一次成功查询开始</h2>
  <div class="cne-path-grid">
    <article>
      <span>01 · TRY</span>
      <h3>一分钟试玩</h3>
      <p>拉取 5 只股票最近约 30 个交易日的真数据；网络受限时可用确定性离线样例验证完整链路。</p>
      <pre><code>pip install cnequity
cne demo</code></pre>
      <a href="getting-started/quickstart/">打开快速开始 →</a>
    </article>
    <article>
      <span>02 · BUILD</span>
      <h3>建立全市场数据湖</h3>
      <p>默认覆盖全市场最近三年，之后每天只需一条命令增量更新；中断后可从失败批次续跑。</p>
      <pre><code>cne config init
cne init
cne run daily</code></pre>
      <a href="operations/runbook/">查看生产运行方式 →</a>
    </article>
    <article>
      <span>03 · QUERY</span>
      <h3>接入研究与 AI Agent</h3>
      <p>同一份开放数据可被 Python、DuckDB、Polars 或只读 MCP 使用，采集与消费彼此独立。</p>
      <pre><code>from cnequity.query import load
bars = load("daily_bars")</code></pre>
      <a href="reference/mcp/">查看 MCP 接入 →</a>
    </article>
  </div>
</section>

<section class="cne-section cne-datasets">
  <div>
    <p class="cne-kicker">DATA COVERAGE</p>
    <h2>从市场参考到风险事件，<br>保持一套查询契约</h2>
  </div>
  <div class="cne-tag-cloud" aria-label="数据范围">
    <a href="datasets/catalog/#l0">证券主数据</a>
    <a href="datasets/catalog/#l1">日线与分钟线</a>
    <a href="datasets/catalog/#l2">公告与公司行为</a>
    <a href="datasets/catalog/#l3">财报与估值</a>
    <a href="datasets/catalog/#l4">资金与龙虎榜</a>
    <a href="datasets/catalog/#l5">指数与行业成分</a>
    <a href="datasets/catalog/#l6">宏观与市场宽度</a>
    <a href="datasets/catalog/#l7">新闻与情绪</a>
    <a href="datasets/catalog/#l8">解禁与监管事件</a>
  </div>
</section>

<section class="cne-cta">
  <div>
    <p class="cne-kicker">YOUR DATA, YOUR HISTORY</p>
    <h2>让下一次研究，从可信的数据底座开始。</h2>
  </div>
  <div class="cne-actions">
    <a class="cne-button cne-button--primary" href="getting-started/installation/">开始安装</a>
    <a class="cne-button cne-button--secondary" href="https://github.com/rootSunc/cnequity">GitHub 仓库</a>
  </div>
</section>

</div>
