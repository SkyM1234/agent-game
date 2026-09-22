import { useEffect, useState } from 'react';
import { AGENTS } from './labels';
import { EvaluationAnalysis, type Analysis } from './EvaluationAnalysis';

export interface Run {
  run_id: string; created_at: string; status: string; strategies: string[];
  planned_matches: number; completed_matches: number; failed_matches: number;
}
interface Metrics {
  strategy: string; completed: number; failed: number; wins: number; draws: number; losses: number;
  win_rate: number | null; score_rate: number | null; completion_rate: number | null;
  first_valid_rate: number | null; repair_rate: number | null; fallback_rate: number | null;
  decision_p50_ms: number | null; decision_p95_ms: number | null; model_p95_ms: number | null;
  tokens_per_match: number | null; cost_per_match: number | null; damage_per_match: number | null;
}
interface Sample {
  index: number; scenario: string; repeat: number; mirrored: boolean; status: string;
  agents: Record<'p1' | 'p2', string>; replay_id: string | null; error_code?: string;
  result?: { winner: 'p1' | 'p2' | null; simulation_time: number; reason: string };
}
export interface Report extends Run {
  analysis?: Analysis;
  repeats: number; summary: Metrics[]; matches: Sample[]; methodology: string[];
  source: { commit: string | null; backend_sha256: string; python: string; rules_version: string };
  model: { model: string; prompt_version: string; temperature: number } | null;
  prices: { currency: string; input_per_million: number; output_per_million: number } | null;
  suite: { name: string; scenarios: { name: string; config: { time_limit_ms: number; starting_positions: number[]; characters: Record<string, { name: string; character_id?: string; skills?: Record<string, { display_name?: string }>; agent?: { version?: string; prompt?: string } }> } }[] };
}
const percent = (value: number | null) => value == null ? '—' : `${(value * 100).toFixed(1)}%`;
const number = (value: number | null, digits = 1) => value == null ? '—' : value.toFixed(digits);
const statusName: Record<string, string> = { running: '生成中', completed: '已结束', stopped: '已停止', interrupted: '已中断' };
async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`载入失败 (${response.status})`);
  return response.json();
}

export function Evaluations({ onReplay, onDeleted }: {
  onReplay: (runId: string, replayId: string) => Promise<void>;
  onDeleted: (runId: string) => void;
}) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState('');
  const [report, setReport] = useState<Report | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [opening, setOpening] = useState(false);
  const [filter, setFilter] = useState('all');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    read<Run[]>('/api/evaluations', controller.signal).then(items => {
      setRuns(items);
      setSelected(current => items.some(item => item.run_id === current) ? current : items[0]?.run_id ?? '');
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);
  useEffect(() => {
    setReport(null); setFilter('all'); setConfirmDelete(false);
    if (!selected) return;
    const controller = new AbortController();
    setError('');
    read<Report>(`/api/evaluations/${selected}`, controller.signal).then(setReport)
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [selected, refresh]);
  async function openReplay(replayId: string) {
    setOpening(true); setError('');
    try { await onReplay(selected, replayId); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '回放载入失败'); }
    finally { setOpening(false); }
  }
  async function deleteReport() {
    if (!report || report.status === 'running' || deleting) return;
    setDeleting(true); setError('');
    try {
      const response = await fetch(`/api/evaluations/${selected}`, { method: 'DELETE' });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(typeof body?.detail === 'string' ? body.detail : `删除失败 (${response.status})`);
      }
      const remaining = runs.filter(run => run.run_id !== selected);
      setRuns(remaining); setReport(null); setSelected(remaining[0]?.run_id ?? '');
      setConfirmDelete(false); onDeleted(selected);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '删除报告失败'); }
    finally { setDeleting(false); }
  }
  return <section className="evaluation-page" aria-label="评测结果">
    <div className="evaluation-heading"><div><span className="eyebrow">STRATEGY BENCHMARK</span><h2>策略对照评测</h2>
      <p>比较固定局面中的战斗结果、决策速度与模型用量。</p></div>
      <div className="evaluation-actions">
        <button className="primary-button" onClick={() => setRefresh(value => value + 1)} disabled={loading || opening || confirmDelete || deleting}>刷新报告</button>
        {report && <button className="primary-button cancel-button" onClick={() => setConfirmDelete(true)}
          disabled={loading || opening || deleting || confirmDelete || report.status === 'running'}
          title={report.status === 'running' ? '评测生成中，结束后可删除' : '删除当前报告及关联回放'}>删除报告</button>}
      </div></div>
    {confirmDelete && report && <div className="evaluation-delete-confirm" role="alertdialog" aria-labelledby="delete-evaluation-title" aria-describedby="delete-evaluation-description">
      <h3 id="delete-evaluation-title">删除这份评测报告？</h3>
      <p id="delete-evaluation-description">{new Date(report.created_at).toLocaleString('zh-CN')} · #{report.run_id.slice(0, 8)} 的报告及全部关联回放将永久删除，无法恢复。</p>
      <div className="evaluation-actions">
        <button className="primary-button setup-cancel" autoFocus disabled={deleting} onClick={() => setConfirmDelete(false)}>取消删除</button>
        <button className="primary-button cancel-button" disabled={deleting} onClick={() => void deleteReport()}>{deleting ? '正在删除…' : '确认删除'}</button>
      </div>
    </div>}
    {error && <p className="error-banner" role="alert">{error}</p>}
    {loading && <p role="status">正在载入评测报告…</p>}
    {!loading && !error && !runs.length && <div className="evaluation-empty"><h3>暂无评测报告</h3>
      <p>完成一次后端评测后，刷新即可查看策略对比与逐场回放。</p>
      <p>本地策略评测无需模型密钥；运行方式见项目 README 的“策略对照评测”。</p></div>}
    {!!runs.length && <label className="evaluation-picker">选择实验
      <select value={selected} disabled={opening || deleting || confirmDelete || loading} onChange={event => setSelected(event.target.value)}>
        {runs.map(run => <option key={run.run_id} value={run.run_id}>{new Date(run.created_at).toLocaleString('zh-CN')} · {run.strategies.map(name => AGENTS[name] || name).join(' / ')} · {statusName[run.status] || run.status}</option>)}
      </select></label>}
    {selected && !report && !error && <p role="status">正在读取实验详情…</p>}
    {report && <>
      <div className="evaluation-cards">
        <article><span>完成 / 计划</span><strong>{report.completed_matches} / {report.planned_matches}</strong><small>{statusName[report.status] || report.status} · 失败 {report.failed_matches} 场</small></article>
        <article><span>固定局面</span><strong>{report.suite.scenarios.length} 组</strong><small>交换角色 · 镜像出生位置 · 重复 {report.repeats} 次</small></article>
        <article><span>模型</span><strong>{report.model?.model || '本地策略'}</strong><small>{report.model ? `${report.model.prompt_version} · 温度 ${report.model.temperature}` : '无模型请求'}</small></article>
      </div>
      <div className="evaluation-table-wrap" tabIndex={0} role="region" aria-label="策略指标对比，可横向滚动">
        <table className="evaluation-table"><caption>当前实验的策略对比（性能与用量仅统计完成场次）</caption>
          <thead><tr><th scope="col">策略</th><th scope="col">胜 / 平 / 负</th><th scope="col">完成率</th><th scope="col">胜率</th><th scope="col">得分率</th><th scope="col">首次调用成功</th><th scope="col">修正 / 保底</th><th scope="col">决策 P50 / P95</th><th scope="col">模型 P95</th><th scope="col">Token / 场</th><th scope="col">估算费用 / 场</th><th scope="col">伤害 / 场</th></tr></thead>
          <tbody>{report.summary.map(row => <tr key={row.strategy}>
            <th scope="row">{AGENTS[row.strategy] || row.strategy}<small>完成 {row.completed} · 失败 {row.failed}</small></th>
            <td>{row.wins} / {row.draws} / {row.losses}</td><td>{percent(row.completion_rate)}</td><td>{percent(row.win_rate)}</td><td>{percent(row.score_rate)}</td>
            <td>{percent(row.first_valid_rate)}</td><td>{percent(row.repair_rate)} / {percent(row.fallback_rate)}</td>
            <td>{number(row.decision_p50_ms)} / {number(row.decision_p95_ms)} ms</td><td>{number(row.model_p95_ms)}{row.model_p95_ms != null ? ' ms' : ''}</td>
            <td>{number(row.tokens_per_match, 0)}</td><td>{row.cost_per_match == null ? '未定价' : `${number(row.cost_per_match, 4)} ${report.prices?.currency || ''}`}</td><td>{number(row.damage_per_match)}</td>
          </tr>)}</tbody></table>
      </div>
      <p className="evaluation-note">“—”表示不适用或无样本。胜率不计平局；得分率给平局计半分。小样本和短局仅用于初步对照，本地策略重复运行不增加独立样本。</p>
      <EvaluationAnalysis key={`${report.run_id}/${refresh}`} report={report} runs={runs} />
      <details className="evaluation-method"><summary>实验配置与统计口径</summary>
        <p>{report.suite.name} · 规则 {report.source.rules_version} · Python {report.source.python}</p>
        <p>提交：{report.source.commit || '未记录'}<br />后端代码摘要：{report.source.backend_sha256}</p>
        <p>{report.prices ? `单价：输入 ${report.prices.input_per_million} / 输出 ${report.prices.output_per_million} ${report.prices.currency} / 百万 Token` : '未设置模型单价，模型费用不估算。'}</p>
        <ul>{report.suite.scenarios.map(scenario => <li key={scenario.name}>{scenario.name}：{scenario.config.time_limit_ms / 1000}s · 出生位置 {scenario.config.starting_positions.join(' / ')} · {Object.values(scenario.config.characters).map(character => `${character.name}${character.agent ? ` (${character.agent.version})` : ''}`).join(' / ')}</li>)}</ul>
        <ul>{report.methodology.map(text => <li key={text}>{text}</li>)}</ul>
        <a href={`/api/evaluations/${selected}/export`} download>下载完整报告与配置快照</a>
      </details>
      <div className="evaluation-heading"><h3>逐场结果与回放</h3><label>筛选策略 <select value={filter} onChange={event => setFilter(event.target.value)}>
        <option value="all">全部</option>{report.strategies.map(name => <option key={name} value={name}>{AGENTS[name] || name}</option>)}
      </select></label></div>
      <div className="evaluation-matches">{report.matches.filter(match => filter === 'all' || Object.values(match.agents).includes(filter)).map(match => <article key={match.index}>
        <div><small>#{match.index} · {match.scenario} · 第 {match.repeat} 次 · {match.mirrored ? '镜像站位' : '原始站位'}</small>
          <h4>{AGENTS[match.agents.p1]} <span>vs</span> {AGENTS[match.agents.p2]}</h4>
          <p>{match.status === 'failed' ? `失败：${match.error_code}` : `${match.result?.winner ? `${AGENTS[match.agents[match.result.winner]]} (${match.result.winner.toUpperCase()}) 获胜` : '平局'} · ${match.result?.simulation_time.toFixed(2)}s · ${match.result?.reason === 'time_limit' ? '时间结束' : '击倒'}`}</p></div>
        {match.replay_id && <button className="primary-button" disabled={opening || deleting || confirmDelete} onClick={() => void openReplay(match.replay_id!)}>查看回放 #{match.index}</button>}
      </article>)}</div>
    </>}
  </section>;
}
