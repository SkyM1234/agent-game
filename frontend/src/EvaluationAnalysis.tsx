import { useEffect, useState } from 'react';
import { AGENTS } from './labels';
import type { Report, Run } from './Evaluations';

interface AnalysisRow {
  strategy: string; character_id?: string; character_name?: string; opponent?: string;
  opponent_character_id?: string; opponent_character_name?: string;
  completed: number; failed: number; wins: number; draws: number; losses: number;
  win_rate: number | null; score_rate: number | null; duration_seconds: number | null;
  time_limit_matches: number; knockout_matches: number; model_decisions: number; tokens_per_match: number | null;
  behavior_matches: number;
  backward_rate: number | null; rest_rate: number | null; hits: number | null; misses: number | null;
  recommendation_comparisons: number; recommendation_deviations: number;
  deviation_rate: number | null; deviation_match_mean: number | null; deviation_matches: number;
}
export interface Analysis {
  version: number; methodology: string[]; strategies: AnalysisRow[]; roles: AnalysisRow[]; matchups: AnalysisRow[];
}
const pct = (n: number | null) => n == null ? '—' : `${(n * 100).toFixed(1)}%`;
const num = (n: number | null, digits = 1) => n == null ? '—' : n.toFixed(digits);
const name = (s: string) => AGENTS[s] || s;
const key = (row: AnalysisRow) => `${row.strategy}/${row.character_id || ''}`;
const record = (row: AnalysisRow) => `${row.wins} / ${row.draws} / ${row.losses}`;

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value !== null && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(',')}}`;
  return JSON.stringify(value) ?? 'null';
}
function combatConfig(report: Report) {
  return report.suite.scenarios.map(scenario => {
    const config = structuredClone(scenario.config);
    for (const character of Object.values(config.characters)) {
      if (character.agent) { delete character.agent.prompt; delete character.agent.version; }
    }
    return config;
  });
}
function differences(current: Report, previous: Report): string[] {
  const checks: [string, unknown, unknown][] = [
    ['战斗配置', combatConfig(current), combatConfig(previous)],
    ['模型参数', current.model, previous.model],
    ['后端代码', current.source.backend_sha256, previous.source.backend_sha256],
    ['规则版本', current.source.rules_version, previous.source.rules_version],
    ['参评策略', [...current.strategies].sort(), [...previous.strategies].sort()],
    ['重复次数', current.repeats, previous.repeats],
  ];
  return checks.filter(([, a, b]) => canonical(a) !== canonical(b)).map(([label]) => label);
}
function delta(current: number | null, previous: number | null, percent = false) {
  if (current == null || previous == null) return '—';
  const difference = (current - previous) * (percent ? 100 : 1);
  return `${difference > 0 ? '+' : ''}${difference.toFixed(1)}${percent ? ' 个百分点' : ''}`;
}

export function EvaluationAnalysis({ report, runs }: { report: Report; runs: Run[] }) {
  const [group, setGroup] = useState('strategy');
  const [baselineId, setBaselineId] = useState(() => runs.find(run => run.created_at < report.created_at && run.status !== 'running')?.run_id || '');
  const [baseline, setBaseline] = useState<Report | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    setBaseline(null); setError('');
    if (!baselineId) return;
    const controller = new AbortController();
    fetch(`/api/evaluations/${baselineId}`, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(`对照报告载入失败 (${response.status})`);
      return response.json() as Promise<Report>;
    }).then(value => { if (!controller.signal.aborted) setBaseline(value); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [baselineId]);
  const analysis = report.analysis;
  if (!analysis) return <p className="evaluation-note">此报告尚无行为统计，请更新后端后刷新。</p>;
  const rows = group === 'role' ? analysis.roles : analysis.strategies;
  const oldRows = baseline?.analysis ? (group === 'role' ? baseline.analysis.roles : baseline.analysis.strategies) : [];
  const changed = baseline ? differences(report, baseline) : [];
  return <section className="evaluation-analysis" aria-label="战斗行为分析">
    <div className="evaluation-heading"><h3>战斗行为分析</h3><label>统计维度 <select value={group} onChange={event => setGroup(event.target.value)}>
      <option value="strategy">策略汇总</option><option value="role">按人物</option>
    </select></label></div>
    <div className="evaluation-table-wrap" tabIndex={0} role="region" aria-label="行为统计，可横向滚动">
      <table className="evaluation-table"><caption>{group === 'role' ? '各人物的战绩与动作表现' : '各策略的战斗行为与时长'}</caption>
        <thead><tr><th>策略 / 人物</th><th>胜 / 平 / 负</th><th>胜率 / 得分率</th><th>时长 / 场</th><th>击倒 / 超时场次</th><th>推荐偏离（加权）</th><th>推荐偏离（逐场平均）</th><th>后退 / 恢复占比</th><th>命中 / 落空事件</th><th>模型决策数</th><th>行为覆盖</th></tr></thead>
        <tbody>{rows.map(row => <tr key={key(row)}>
          <th scope="row">{name(row.strategy)}{row.character_name && <small>{row.character_name}</small>}</th>
          <td>{record(row)}<small>完成 {row.completed} · 失败 {row.failed}</small></td><td>{pct(row.win_rate)} / {pct(row.score_rate)}</td>
          <td>{num(row.duration_seconds)} s</td><td>{row.knockout_matches} / {row.time_limit_matches}</td>
          <td>{pct(row.deviation_rate)}<small>{row.recommendation_deviations} / {row.recommendation_comparisons} 次决策</small></td>
          <td>{pct(row.deviation_match_mean)}<small>{row.deviation_matches} 场有可比较决策</small></td>
          <td>{pct(row.backward_rate)} / {pct(row.rest_rate)}</td><td>{row.hits ?? '—'} / {row.misses ?? '—'}</td>
          <td>{row.model_decisions}</td><td>{row.behavior_matches} / {row.completed} 场</td>
        </tr>)}</tbody></table>
    </div>
    {analysis.strategies.some(row => row.behavior_matches < row.completed) && <p className="evaluation-note" role="status">部分回放缺失或无法读取，行为指标仅覆盖可用回放；缺失数据未按零统计。</p>}
    <p className="evaluation-note">推荐偏离不等于错误。加权值按决策计，逐场平均给每场相同权重。动作占比按提交动作计，命中与落空按回放事件计。</p>
    <details className="evaluation-method"><summary>双方交手结果</summary>
      <div className="evaluation-table-wrap" tabIndex={0} role="region" aria-label="双方交手结果，可横向滚动"><table className="evaluation-table">
        <caption>按双方人物区分交手结果（胜负相对于第一列，合并镜像站位与重复场次）</caption><thead><tr><th>策略 / 人物</th><th>对手策略 / 人物</th><th>胜 / 平 / 负</th><th>胜率</th><th>时长 / 场</th></tr></thead>
        <tbody>{analysis.matchups.map(row => <tr key={`${key(row)}/${row.opponent}/${row.opponent_character_id || ''}`}><th scope="row">{name(row.strategy)}<small>{row.character_name || '人物未记录'}</small></th><td>{name(row.opponent!)}<small>{row.opponent_character_name || '人物未记录'}</small></td><td>{record(row)}<small>完成 {row.completed} · 失败 {row.failed}</small></td><td>{pct(row.win_rate)}</td><td>{num(row.duration_seconds)} s</td></tr>)}</tbody>
      </table></div>
    </details>
    <h3>与历史实验对比</h3>
    <label className="evaluation-picker">对照实验（默认上一份已结束报告）<select value={baselineId} onChange={event => setBaselineId(event.target.value)}>
      <option value="">不对比</option>{runs.filter(run => run.run_id !== report.run_id).map(run => <option key={run.run_id} value={run.run_id}>{new Date(run.created_at).toLocaleString('zh-CN')} · #{run.run_id.slice(0, 8)}</option>)}
    </select></label>
    {error && <p role="alert" className="error-banner">{error}</p>}
    {baselineId && !baseline && !error && <p role="status">正在读取对照报告并补算历史指标…</p>}
    {baseline && <>
      <p className="evaluation-note">{changed.length ? `配置差异：${changed.join('、')}。以下为描述性对照，不能将变化只归因于提示词。` : '战斗配置、模型参数、后端代码、规则版本、参评策略与重复次数一致；人物提示词及其版本不参与此一致性判断。'} 模型采样仍可能波动。</p>
      <p className="evaluation-note">人物提示词版本：{baseline.suite.scenarios.flatMap(s => Object.values(s.config.characters).map(c => c.agent?.version || '未记录')).join(' / ')} → {report.suite.scenarios.flatMap(s => Object.values(s.config.characters).map(c => c.agent?.version || '未记录')).join(' / ')}。数值按“对照 → 当前”展示。</p>
      {!baseline.analysis ? <p className="evaluation-note">对照报告暂无分析指标。</p> : <div className="evaluation-table-wrap" tabIndex={0} role="region" aria-label="历史指标对比，可横向滚动"><table className="evaluation-table">
        <caption>对照 → 当前（随统计维度切换）</caption><thead><tr><th>策略 / 人物</th><th>胜 / 平 / 负</th><th>胜率</th><th>得分率</th><th>推荐偏离（加权）</th><th>时长 / 场</th><th>后退 / 恢复占比</th><th>命中 / 落空事件</th><th>模型决策数</th><th>Token / 场</th></tr></thead>
        <tbody>{rows.map(row => {
          const old = oldRows.find(item => key(item) === key(row));
          return <tr key={key(row)}><th scope="row">{name(row.strategy)}<small>{row.character_name}</small></th>
            {!old ? <td colSpan={9}>对照实验没有此策略 / 人物</td> : <>
              <td>{record(old)} → {record(row)}</td><td>{pct(old.win_rate)} → {pct(row.win_rate)}<small>{delta(row.win_rate, old.win_rate, true)}</small></td>
              <td>{pct(old.score_rate)} → {pct(row.score_rate)}<small>{delta(row.score_rate, old.score_rate, true)}</small></td>
              <td>{pct(old.deviation_rate)} → {pct(row.deviation_rate)}<small>行为覆盖 {old.behavior_matches}/{old.completed} → {row.behavior_matches}/{row.completed}</small></td>
              <td>{num(old.duration_seconds)} → {num(row.duration_seconds)} s</td>
              <td>{pct(old.backward_rate)} / {pct(old.rest_rate)} → {pct(row.backward_rate)} / {pct(row.rest_rate)}</td>
              <td>{old.hits ?? '—'} / {old.misses ?? '—'} → {row.hits ?? '—'} / {row.misses ?? '—'}</td>
              <td>{old.model_decisions} → {row.model_decisions}</td><td>{num(old.tokens_per_match, 0)} → {num(row.tokens_per_match, 0)}</td>
            </>}</tr>;
        })}</tbody></table></div>}
    </>}
    <details className="evaluation-method"><summary>行为指标统计口径</summary><ul>{analysis.methodology.map(text => <li key={text}>{text}</li>)}</ul></details>
  </section>;
}
