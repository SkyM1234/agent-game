import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowDownToLine, ArrowRight, ArrowUpFromLine, ChevronLeft, ChevronRight,
  CircleAlert, Crosshair, Crystal, Footprints, Hand, Heart, History, Pause, Play,
  Plus, RotateCcw, Shield, SkipBack, SkipForward, Square, Swords, Timer, Trash2, X, Zap } from './PixelIcon';
import { ArenaView } from './arena/ArenaView';
import { Evaluations } from './Evaluations';
import { actionText, actorName, AGENTS, eventText, frameAt, PHASES, SKILLS } from './labels';
import type { AgentOption, Character, CharacterId, Frame, GameConfig, IncompleteMatch, Item, MatchPreview, Player, PromptVariant, Replay, ReplayItem, Skill, Weapon } from './types';
import { assetPath, CHARACTER_ART, characterId, DEFAULT_CHARACTERS, promptVariant } from './characters';
import { DEFAULT_WEAPONS, effectiveSkills, modifierSummary, resourceLimit, weaponAsset, weaponId } from './weapons';
import { DEFAULT_ITEMS, itemAsset, itemEffect, itemIds } from './items';

const icons = { jab: Hand, heavy_punch: Swords, kick: Footprints, guard: Shield, dash: Zap, move: ArrowRight, rest: Heart };
const players: Player[] = ['p1', 'p2'];
const amountText = (value: number) => Number(value.toFixed(3)).toString();
const teamStyle = (id: CharacterId) => ({ '--team': CHARACTER_ART[id].hex,
  '--team-ink': id === 'crimson_blade' ? '#984366' : '#356888' } as React.CSSProperties);

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : `请求失败 (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

function FighterPanel({ player, frame, config, agent }: { player: Player; frame: Frame; config: GameConfig; agent: string }) {
  const fighter = frame.fighters[player];
  const action = fighter.action;
  const available = frame.tools[player];
  const availableSkillCount = available.available.filter(name => name !== 'use_item').length;
  const id = characterId(config, player);
  const character = CHARACTER_ART[id];
  const skills = effectiveSkills(config, player);
  const maxHealth = resourceLimit(config, player, 'health');
  const maxStamina = resourceLimit(config, player, 'stamina');
  const maxMana = resourceLimit(config, player, 'mana');
  const weapon = config.equipment?.[player];
  const inventory = config.items?.[player] ?? [];
  return <aside className={`fighter-panel ${player}`} data-testid={`fighter-${player}`} style={teamStyle(id)}>
    <div className="fighter-tab"><span>{player === 'p1' ? 'PLAYER 01' : 'PLAYER 02'}</span><span>{character.title}</span></div>
    <div className="fighter-heading">
      <img className="fighter-portrait" src={assetPath(id, 'portrait.png')} alt={character.name} />
      <div><span className="eyebrow">{player === 'p1' ? 'A' : 'B'} / {character.title}</span>
        <h2>{character.name}</h2></div>
      <span className="agent-tag" title={AGENTS[agent] || agent}>{AGENTS[agent] || agent}</span>
    </div>
    {weapon && <div className="equipped-weapon" title={weapon.description}>
      <img src={weaponAsset(weapon)} alt="" /><span><small>装备武器</small><strong>{weapon.name}</strong></span>
    </div>}
    {inventory.length > 0 && <div className="equipped-items">
      {inventory.map(item => <div key={item.item_id} title={item.description}>
        <img src={itemAsset(item)} alt="" /><span><strong>{item.name}</strong><small>{itemEffect(item)}</small></span>
        <b>×{fighter.items?.[item.item_id] ?? 0}</b>
      </div>)}
    </div>}
    {(fighter.shield ?? 0) > 0 && <div className="shield-status"><Shield size={13} /> 护盾 {fighter.shield} · {fighter.shield_turns} 回合</div>}
    <div className="resource">
      <div><span><Heart size={13} /> 生命值</span><strong>{fighter.health.toFixed(1)} <small>/ {maxHealth}</small></strong></div>
      <div className="meter"><i style={{ width: `${fighter.health / maxHealth * 100}%` }} /></div>
    </div>
    <div className="resource stamina">
      <div><span><Zap size={13} /> 体力</span><strong>{fighter.stamina.toFixed(1)} <small>/ {maxStamina}</small></strong></div>
      <div className="meter"><i style={{ width: `${fighter.stamina / maxStamina * 100}%` }} /></div>
    </div>
    <div className="resource mana" data-testid="mana">
      <div><span><Crystal size={13} /> 魔力</span><strong>{fighter.mana.toFixed(1)} <small>/ {maxMana}</small></strong></div>
      <div className="meter"><i style={{ width: `${fighter.mana / maxMana * 100}%` }} /></div>
    </div>
    <div className="section-label"><span>技能</span><span>{availableSkillCount} / 7 可用</span></div>
    <div className="skill-list">
      {(Object.keys(SKILLS) as Skill[]).map(skill => {
        const spec = skills[skill];
        const active = action?.skill === skill;
        const reason = available.unavailable[skill];
        const cooldown = fighter.cooldowns?.[skill] ?? 0;
        const status = frame.result ? '已结束' : active ? PHASES[action.phase] : cooldown > 0 ? `${cooldown} 回合` : reason === 'insufficient_mana' ? '魔力不足' : reason === 'insufficient_stamina' ? '体力不足' : reason === 'action_locked' ? '锁定' : '可用';
        const Icon = icons[skill];
        const movement = skill === 'move' || skill === 'dash';
        const distance = amountText(spec.speed * spec.active_ms / 1000);
        const staminaRestore = amountText(spec.stamina_restore ?? config.rest_stamina_per_second * spec.active_ms / 1000);
        const manaRestore = amountText(spec.mana_restore ?? config.rest_mana_per_second * spec.active_ms / 1000);
        const effect = spec.teleport ? `${spec.windup_ms ? '前摇结束后' : '立即'}传送到指定位置；落点范围 ${amountText(config.fighter_radius)}～${amountText(config.arena_width - config.fighter_radius)}。可越过对手，落点不可重叠。`
          : movement ? `移动距离 ${distance}（受对手和边界限制）` : skill === 'rest'
          ? `恢复体力 ${staminaRestore}、魔力 ${manaRestore}（完整施放，受资源上限限制）`
          : skill === 'guard' ? `正面伤害减免 ${amountText((1 - config.guard_damage_multiplier) * 100)}%`
          : `伤害 ${spec.health_damage}；射程 ${spec.reach}`;
        return <details key={`${id}-${skill}`} className="skill-item" data-skill={skill} data-disabled={Boolean(reason)}>
          <summary className={`skill-row ${reason && !active ? 'unavailable' : ''} ${active ? 'executing' : ''}`}>
            <Icon size={15} /><span>{spec.display_name ?? SKILLS[skill]}</span><span className="skill-state">{status}</span><ChevronRight className="skill-chevron" size={11} />
          </summary>
          <div className="skill-detail">
            <p>{effect}</p>
            <p>体力消耗 {spec.stamina_cost} · 魔力消耗 {spec.mana_cost}</p>
            <p>冷却 {spec.cooldown_turns} 回合</p>
            <p>前摇 {amountText(spec.windup_ms / 1000)}s · 生效 {amountText(spec.active_ms / 1000)}s · 后摇 {amountText(spec.recovery_ms / 1000)}s</p>
          </div>
        </details>;
      })}
    </div>
    <div className="current-action"><span>当前动作</span><strong>{actionText(action, config, player)}</strong>
      <small>{frame.result ? '对局结束' : action ? `${PHASES[action.phase]} · ${(action.remaining_ms / 1000).toFixed(2)}s` : '等待行动'}</small>
    </div>
  </aside>;
}

export default function App() {
  const [page, setPage] = useState<'arena' | 'evaluations'>('arena');
  const [replayBase, setReplayBase] = useState('/api/replays');
  const [replay, setReplay] = useState<Replay | null>(null);
  const [history, setHistory] = useState<ReplayItem[]>([]);
  const [incomplete, setIncomplete] = useState<IncompleteMatch[]>([]);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [strategies, setStrategies] = useState({ p1: 'test', p2: 'test' });
  const [characters, setCharacters] = useState<Record<Player, CharacterId>>(DEFAULT_CHARACTERS);
  const [promptVariants, setPromptVariants] = useState<Record<Player, PromptVariant>>({ p1: 'neutral', p2: 'neutral' });
  const [weapons, setWeapons] = useState<Record<Player, string>>(DEFAULT_WEAPONS);
  const [itemSlots, setItemSlots] = useState<Record<Player, [string, string]>>(DEFAULT_ITEMS);
  const [catalog, setCatalog] = useState<Character[]>([]);
  const [weaponCatalog, setWeaponCatalog] = useState<Weapon[]>([]);
  const [itemCatalog, setItemCatalog] = useState<Item[]>([]);
  const [configuring, setConfiguring] = useState(false);
  const [preview, setPreview] = useState<MatchPreview | null>(null);
  const [loading, setLoading] = useState('正在载入');
  const [error, setError] = useState('');
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [filter, setFilter] = useState('key');
  const [options, setOptions] = useState<AgentOption[]>([]);
  const [live, setLive] = useState(false);
  const [waiting, setWaiting] = useState<Player[]>([]);
  const [sessionNote, setSessionNote] = useState('');
  const socketRef = useRef<WebSocket | null>(null);
  const liveRef = useRef(false);
  const nextPending = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const timeRef = useRef(0);
  const duration = replay?.summary.result?.simulation_time ?? replay?.frames.at(-1)?.simulation_time ?? 0;
  const itemChoices = useMemo(() => ({
    p1: itemSlots.p1.filter(Boolean), p2: itemSlots.p2.filter(Boolean),
  }), [itemSlots]);

  function display(record: Replay, autoplay = false) {
    setReplayBase('/api/replays');
    setReplay(record); timeRef.current = 0; setTime(0); setPlaying(autoplay);
    setConfirmDeleteId(null);
    restoreSelections(record);
    setConfiguring(false);
    setPreview(null);
    setSessionNote('');
  }
  function restoreSelections(record: Replay) {
    setStrategies({ p1: record.agents.p1, p2: record.agents.p2 });
    setCharacters({ p1: characterId(record.config, 'p1'), p2: characterId(record.config, 'p2') });
    setPromptVariants({ p1: promptVariant(record.config, 'p1'), p2: promptVariant(record.config, 'p2') });
    setWeapons({
      p1: weaponId(record.config, 'p1') ?? DEFAULT_WEAPONS.p1,
      p2: weaponId(record.config, 'p2') ?? DEFAULT_WEAPONS.p2,
    });
    setItemSlots({ p1: itemIds(record.config, 'p1'), p2: itemIds(record.config, 'p2') });
  }
  function beginConfiguration() {
    setPlaying(false); setError(''); setConfiguring(true);
    setPreview(replay ? { config: replay.config, frame: replay.frames[0] } : null);
  }
  function cancelConfiguration() {
    if (replay) restoreSelections(replay);
    setConfiguring(false);
    setPreview(null);
  }
  async function refreshHistory() {
    const [finished, running] = await Promise.all([
      request<ReplayItem[]>('/api/replays'), request<IncompleteMatch[]>('/api/matches/incomplete'),
    ]);
    setHistory(finished); setIncomplete(running);
  }
  function selectCharacter(player: Player, characterId: CharacterId) {
    setCharacters(current => ({ ...current, [player]: characterId }));
    const profession = catalog.find(character => character.character_id === characterId)?.profession;
    const compatible = weaponCatalog.find(weapon => weapon.profession === profession);
    if (compatible) setWeapons(current => ({ ...current, [player]: compatible.weapon_id }));
  }
  function selectItem(player: Player, slot: 0 | 1, itemId: string) {
    setItemSlots(current => {
      const next: [string, string] = [...current[player]];
      if (itemId && next[slot === 0 ? 1 : 0] === itemId) next[slot === 0 ? 1 : 0] = '';
      next[slot] = itemId;
      return { ...current, [player]: next };
    });
  }

  useEffect(() => {
    let disposed = false;
    async function loadInitial() {
      try {
        const [items, running, agents, roster, armory, consumables] = await Promise.all([
          request<ReplayItem[]>('/api/replays'), request<IncompleteMatch[]>('/api/matches/incomplete'),
          request<AgentOption[]>('/api/agents'), request<Character[]>('/api/characters'), request<Weapon[]>('/api/weapons'), request<Item[]>('/api/items'),
        ]);
        if (!disposed) { setOptions(agents); setCatalog(roster); setWeaponCatalog(armory); setItemCatalog(consumables); }
        if (!disposed) { setHistory(items); setIncomplete(running); }
        if (items.length) {
          const record = await request<Replay>(`/api/replays/${items[0].replay_id}`);
          if (!disposed) display(record);
        } else if (!disposed) setConfiguring(true);
      } catch (reason) { if (!disposed) setError(reason instanceof Error ? reason.message : '载入失败'); }
      finally { if (!disposed) setLoading(''); }
    }
    void loadInitial();
    return () => { disposed = true; socketRef.current?.close(); };
  }, []);

  useEffect(() => {
    if (!configuring || !catalog.length || !weaponCatalog.length || !itemCatalog.length) return;
    const valid = players.every(player => {
      const profession = catalog.find(character => character.character_id === characters[player])?.profession;
      return weaponCatalog.some(weapon => weapon.weapon_id === weapons[player] && weapon.profession === profession);
    });
    if (!valid) return;
    if (players.some(player => itemChoices[player].some(itemId => !itemCatalog.some(item => item.item_id === itemId)))) return;
    const controller = new AbortController();
    request<MatchPreview>('/api/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ characters, prompt_variants: promptVariants, weapons, items: itemChoices }), signal: controller.signal,
    }).then(setPreview).catch(reason => {
      if (reason instanceof DOMException && reason.name === 'AbortError') return;
      setError(reason instanceof Error ? reason.message : '预览载入失败');
    });
    return () => controller.abort();
  }, [configuring, characters, promptVariants, weapons, itemChoices, catalog, weaponCatalog, itemCatalog]);

  useEffect(() => {
    if (!playing || !replay) return;
    let animation = 0, previous = performance.now();
    function advance(now: number) {
      const delta = Math.min((now - previous) / 1000, 0.1); previous = now;
      const next = Math.min(duration, timeRef.current + delta * speed);
      timeRef.current = next; setTime(next);
      if (next >= duration && !liveRef.current) setPlaying(false);
      else {
        if (next >= duration && nextPending.current && socketRef.current?.readyState === WebSocket.OPEN) {
          nextPending.current = false;
          socketRef.current.send(JSON.stringify({ type: 'next' }));
        }
        animation = requestAnimationFrame(advance);
      }
    }
    animation = requestAnimationFrame(advance);
    return () => cancelAnimationFrame(animation);
  }, [playing, speed, replay, duration]);

  async function createMatch() {
    if (Object.values(strategies).some(strategy =>
      strategy === 'deepseek' || strategy === 'counterfactual_deepseek' || strategy === 'counterfactual')) {
      startLiveMatch(); return;
    }
    setLoading('正在生成对局'); setPlaying(false); setError('');
    try {
      display(await request<Replay>('/api/matches', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...strategies, characters, prompt_variants: promptVariants, weapons, items: itemChoices }) }), true);
      await refreshHistory();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '创建失败'); }
    finally { setLoading(''); }
  }
  function startLiveMatch(resumeId?: string) {
    setLoading('正在连接对局'); setPlaying(false); setError(''); setSessionNote('');
    nextPending.current = false;
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/live`);
    socketRef.current = socket;
    let ended = false;
    const stop = () => {
      liveRef.current = false; nextPending.current = false;
      setLive(false); setWaiting([]); setLoading('');
    };
    socket.onopen = () => socket.send(JSON.stringify(resumeId
      ? { resume_id: resumeId }
      : { ...strategies, characters, prompt_variants: promptVariants, weapons, items: itemChoices }));
    socket.onmessage = event => {
      const message = JSON.parse(event.data);
      if (message.type === 'started') {
        liveRef.current = true; setLive(true);
        display(message.replay, true); setLoading('');
      } else if (message.type === 'waiting') {
        setWaiting(message.players);
      } else if (message.type === 'cycle') {
        setWaiting([]); nextPending.current = true;
        setReplay(current => current ? { ...current,
          frames: [...current.frames, ...message.frames],
          events: [...current.events, ...message.events],
          decisions: [...current.decisions, message.decision],
        } : current);
      } else if (message.type === 'finished') {
        ended = true; stop(); setReplay(message.replay);
        void refreshHistory().catch(() => setError('对局已保存，但历史列表刷新失败'));
      } else if (message.type === 'error' || message.type === 'cancelled') {
        ended = true; stop(); setPlaying(false);
        if (message.type === 'error') setError(message.message);
        else setSessionNote('对局已取消，可从对局记录继续');
        void refreshHistory().catch(() => setError('未完成对局列表刷新失败'));
      }
    };
    socket.onerror = () => setError('实时对局连接失败');
    socket.onclose = () => {
      if (socketRef.current === socket) socketRef.current = null;
      if (!ended) {
        stop(); setPlaying(false); setError('实时连接已断开，已完成的回合仍可继续');
        void refreshHistory().catch(() => undefined);
      }
    };
  }
  function cancelLiveMatch() {
    setPlaying(false);
    if (socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send(JSON.stringify({ type: 'cancel' }));
    else socketRef.current?.close();
  }
  async function selectReplay(id: string) {
    setLoading('正在载入回放'); setPlaying(false); setError('');
    try { display(await request<Replay>(`/api/replays/${id}`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '载入失败'); }
    finally { setLoading(''); }
  }
  async function selectEvaluationReplay(runId: string, replayId: string) {
    const base = `/api/evaluations/${runId}/replays`;
    const record = await request<Replay>(`${base}/${replayId}`);
    display(record);
    setReplayBase(base); setPage('arena');
    setError(''); setSessionNote('评测回放');
  }
  async function deleteReplay(id: string) {
    setLoading('正在删除对局'); setError('');
    try {
      await request<void>(`/api/replays/${id}`, { method: 'DELETE' });
      setConfirmDeleteId(null);
      const [items, running] = await Promise.all([
        request<ReplayItem[]>('/api/replays'), request<IncompleteMatch[]>('/api/matches/incomplete'),
      ]);
      setHistory(items); setIncomplete(running);
      if (replay?.replay_id === id) {
        setPlaying(false);
        setReplay(null); timeRef.current = 0; setTime(0);
        if (items.length) display(await request<Replay>(`/api/replays/${items[0].replay_id}`));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : '删除对局失败'); }
    finally { setLoading(''); }
  }
  async function importReplay(file: File | undefined) {
    if (!file) return;
    setPlaying(false); setError('');
    if (file.size > 12 * 1024 * 1024) { setError('回放文件不能超过 12 MB'); return; }
    setLoading('正在导入回放');
    try {
      display(await request<Replay>('/api/replays/import', { method: 'POST', headers: { 'Content-Type': 'application/x-ndjson' }, body: file }));
      await refreshHistory();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '导入失败'); }
    finally { setLoading(''); }
  }
  function seek(value: number) {
    const next = Math.min(duration, Math.max(0, value));
    setPlaying(false); timeRef.current = next; setTime(next);
  }
  function togglePlay() {
    if (!live && time >= duration) { timeRef.current = 0; setTime(0); }
    setPlaying(value => !value);
  }
  function step(direction: number) {
    if (!replay) return;
    const interval = replay.config.decision_ms / 1000;
    const next = direction > 0 ? (Math.floor(time / interval + 1e-8) + 1) * interval : (Math.ceil(time / interval - 1e-8) - 1) * interval;
    seek(next);
  }

  const index = replay ? (time === 0 ? 0 : frameAt(replay.frames, time)) : 0;
  const frame = replay?.frames[index];
  const currentEvents = useMemo(() => replay?.events.slice(0, frame?.event_count ?? 0) ?? [], [replay, frame?.event_count]);
  const visibleEvents = useMemo(() => configuring ? [] : currentEvents.filter(event => filter === 'all'
    ? event.status !== 'completed' : ['hit', 'item_used', 'teleported', 'missed', 'fallback', 'rejected', 'match_finished'].includes(event.status)).slice(-80).reverse(), [configuring, currentEvents, filter]);
  const viewConfig = configuring ? preview?.config : replay?.config;
  const viewFrame = configuring ? preview?.frame : frame;
  const viewNextFrame = configuring ? preview?.frame : replay?.frames[Math.min(index + 1, replay.frames.length - 1)];
  const viewEvents = configuring ? [] : currentEvents.filter(event => event.simulation_time >= time - 2);
  const viewTime = configuring ? 0 : time;
  const viewDuration = configuring ? 0 : duration;
  const finished = !configuring && !live && Boolean(replay?.summary.result) && time >= duration && duration > 0;
  const status = loading || (configuring ? '阵容预览' : sessionNote || (live && !playing ? '已暂停' : live && waiting.length ? `等待 ${waiting.map(player => actorName(player, replay?.config)).join('、')} 决策` : finished ? '对局结束' : playing ? live ? '实时对局' : '正在播放' : time > 0 ? '已暂停' : '准备就绪'));
  const controlsDisabled = configuring || !replay || Boolean(loading);
  const seekDisabled = controlsDisabled || live;
  const selectedUnavailable = options.some(option => !option.available && Object.values(strategies).includes(option.id));

  return <div className="app-shell">
    <header className="app-header">
      <div className="brand"><div className="brand-symbol"><Swords size={25} /></div><h1>AGENT <span>ARENA</span></h1><span className="brand-subtitle">樱庭 · 像素对战</span></div>
      <div className="header-status"><span className="status-dot" /> {live ? '实时对局' : '本地对局'} <span className="version">◆ 03</span></div>
    </header>

    <main>
      <nav className="page-navigation" aria-label="主导航">
        <button aria-current={page === 'arena' ? 'page' : undefined} onClick={() => setPage('arena')}>对局观战</button>
        <button aria-current={page === 'evaluations' ? 'page' : undefined} disabled={live || Boolean(loading)} onClick={() => { setPlaying(false); setPage('evaluations'); }}>评测结果</button>
      </nav>
      {page === 'evaluations' && <Evaluations onReplay={selectEvaluationReplay} onDeleted={runId => {
        if (replayBase === `/api/evaluations/${runId}/replays`) {
          setReplay(null); setPlaying(false); timeRef.current = 0; setTime(0);
          setReplayBase('/api/replays'); setSessionNote('');
        }
      }} />}
      <div hidden={page !== 'arena'}>
      <div className="match-toolbar">
        <div className="toolbar-title"><span className="eyebrow">SAKURA COURT / 01</span><h2>月下试炼 <span>对战准备</span></h2><p>选择伙伴与策略，开启一场像素对决。</p></div>
        <div className="match-settings">
          {players.map(player => {
            const character = catalog.find(item => item.character_id === characters[player]);
            const compatibleWeapons = weaponCatalog.filter(weapon => weapon.profession === character?.profession);
            const selectedWeapon = weaponCatalog.find(weapon => weapon.weapon_id === weapons[player]);
            const selectedItems = itemSlots[player].map(itemId => itemCatalog.find(item => item.item_id === itemId));
            return <div key={player} className={`player-settings ${player}`} style={teamStyle(characters[player])}>
            <div className="loadout-art"><img src={assetPath(characters[player], 'portrait.png')} alt="" />
              {selectedWeapon && <img className="weapon-icon" src={weaponAsset(selectedWeapon)} alt="" />}
              <div className="item-icons">{selectedItems.map((item, slot) => item
                ? <img key={item.item_id} src={itemAsset(item)} alt="" title={item.name} />
                : <span key={slot} />)}</div></div>
            <label className="character-select"><span>{player === 'p1' ? 'A 方人物' : 'B 方人物'}</span>
              <select aria-label={`${actorName(player)} 人物`} value={characters[player]} disabled={!configuring || Boolean(loading) || live}
                onChange={event => selectCharacter(player, event.target.value as CharacterId)}>
                {catalog.map(character => <option key={character.character_id} value={character.character_id}>{character.name} · {character.title}</option>)}
              </select></label>
            <label className="weapon-select"><span>武器</span>
              <select aria-label={`${actorName(player)} 武器`} value={weapons[player]} disabled={!configuring || Boolean(loading) || live}
                onChange={event => setWeapons(current => ({ ...current, [player]: event.target.value }))}>
                {compatibleWeapons.map(weapon => <option key={weapon.weapon_id} value={weapon.weapon_id}>{weapon.name}</option>)}
              </select>{selectedWeapon && <small title={selectedWeapon.description}>{modifierSummary(selectedWeapon)}</small>}</label>
            <label className="item-select"><span>主动道具</span><div className="item-slots">
              {([0, 1] as const).map(slot => <select key={slot} aria-label={`${actorName(player)} 道具 ${slot + 1}`}
                value={itemSlots[player][slot]} disabled={!configuring || Boolean(loading) || live}
                onChange={event => selectItem(player, slot, event.target.value)}>
                <option value="">无</option>
                {itemCatalog.map(item => <option key={item.item_id} value={item.item_id}
                  disabled={itemSlots[player][slot === 0 ? 1 : 0] === item.item_id}>{item.name}</option>)}
              </select>)}</div></label>
            <label className={`strategy-select ${player}`}><span>策略</span>
            <select aria-label={`${actorName(player)} 策略`} value={strategies[player]} disabled={!configuring || Boolean(loading) || live} onChange={event => setStrategies({ ...strategies, [player]: event.target.value })}>
              {options.map(option => <option key={option.id} value={option.id} disabled={!option.available} title={option.model}>{option.label}{!option.available ? ` (${option.reason})` : ''}</option>)}
            </select></label>
            <label className="prompt-select"><span>人物提示词</span>
              <select aria-label={`${actorName(player)} 提示词`} value={promptVariants[player]} disabled={!configuring || Boolean(loading) || live}
                title="仅纯 DeepSeek、反事实推演 + DeepSeek 策略使用人物提示词"
                onChange={event => setPromptVariants(current => ({ ...current, [player]: event.target.value as PromptVariant }))}>
                <option value="neutral">中立（默认）</option>
                <option value="aggressive">激进</option>
              </select></label></div>})}
          {live ? <button className="primary-button cancel-button" onClick={cancelLiveMatch}><Square size={14} /> 取消对局</button>
            : configuring ? <div className="setup-actions">
              {replay && <button className="primary-button setup-cancel" onClick={cancelConfiguration} disabled={Boolean(loading)}><X size={15} /> 取消</button>}
              <button className="primary-button start-button" onClick={createMatch} disabled={Boolean(loading) || selectedUnavailable}><Play size={16} fill="currentColor" /> 开始对局</button>
            </div>
            : <button className="primary-button" onClick={beginConfiguration} disabled={Boolean(loading)}><Plus size={16} /> 新对局</button>}
        </div>
      </div>

      {error && <div className="error-banner" role="alert"><CircleAlert size={18} /><span>{error}</span>
        <button className="icon-button" aria-label="关闭错误提示" title="关闭错误提示" onClick={() => setError('')}><X size={16} /></button></div>}

      {viewConfig && viewFrame && viewNextFrame ? <section className="match-layout" aria-label={configuring ? '阵容预览' : '对局观战'}>
        <FighterPanel player="p1" frame={viewFrame} config={viewConfig} agent={configuring ? strategies.p1 : replay!.agents.p1} />
        <div className="arena-section">
          <div className="arena-heading"><span><span className={`status-dot ${playing ? 'pulsing' : ''}`} />{status}</span><span className="match-id">{configuring ? 'LOADOUT PREVIEW' : `#${replay!.replay_id.slice(0, 8)}`}</span></div>
          <div className="stage-wrap">
            <ArenaView frame={viewFrame} nextFrame={viewNextFrame} config={viewConfig} time={viewTime} events={viewEvents} />
            <div className="stage-timer"><Timer size={14} /><strong data-testid="game-time">{viewTime.toFixed(2)}</strong><span>s</span></div>
            <div className="stage-name"><span>樱庭 · 月下试炼</span><span>SAKURA COURT / 01</span></div>
            {finished && <div className="result-overlay"><span>对局结束</span><strong>{viewFrame.result?.winner ? `${actorName(viewFrame.result.winner, viewConfig)} 获胜` : '平局'}</strong><small>{viewFrame.result?.reason === 'time_limit' ? '时间结束' : viewFrame.result?.reason === 'double_knockout' ? '同时击倒' : '击倒'}</small></div>}
            {loading && <div className="loading-overlay" role="status"><Activity size={24} />{loading}</div>}
          </div>
          <div className="arena-readout"><span><Crosshair size={13} /> 距离 <strong>{Math.abs(viewFrame.fighters.p1.position - viewFrame.fighters.p2.position).toFixed(2)}</strong></span>
            <span>回合 <strong>{viewFrame.turn.toString().padStart(3, '0')}</strong></span>
            <span>命中 <strong>{viewEvents.filter(event => event.status === 'hit').length.toString().padStart(2, '0')}</strong></span></div>

          <div className="playback">
            <div className="timeline-label"><span>对局时间轴</span><span>{viewTime.toFixed(2)} / {viewDuration.toFixed(2)} s</span></div>
            <div className="timeline-track">
              <input aria-label="对局时间轴" type="range" min={0} max={viewDuration || 0.01} step={0.01} value={viewTime} disabled={seekDisabled} onChange={event => seek(Number(event.target.value))}
                style={{ '--progress': `${viewDuration ? viewTime / viewDuration * 100 : 0}%` } as React.CSSProperties} />
            </div>
            <div className="transport">
              <div className="transport-buttons">
                <button className="icon-button" aria-label="回到开头" title="回到开头" disabled={seekDisabled || time === 0} onClick={() => seek(0)}><SkipBack size={17} /></button>
                <button className="icon-button" aria-label="上一回合" title="上一回合" disabled={seekDisabled || time === 0} onClick={() => step(-1)}><ChevronLeft size={20} /></button>
                <button className="play-button" aria-label={playing ? '暂停' : '播放'} title={playing ? '暂停' : '播放'} disabled={controlsDisabled} onClick={togglePlay}>{playing ? <Pause size={19} fill="currentColor" /> : <Play size={19} fill="currentColor" />}</button>
                <button className="icon-button" aria-label="下一回合" title="下一回合" disabled={seekDisabled || finished} onClick={() => step(1)}><ChevronRight size={20} /></button>
                <button className="icon-button" aria-label="跳到结尾" title="跳到结尾" disabled={seekDisabled || finished} onClick={() => seek(duration)}><SkipForward size={17} /></button>
                <button className="icon-button restart" aria-label="重新播放" title="重新播放" disabled={seekDisabled} onClick={() => { seek(0); setPlaying(true); }}><RotateCcw size={16} /></button>
              </div>
              <div className="speed-control" role="group" aria-label="播放速度">{[0.5, 1, 2].map(value => <button key={value} aria-pressed={speed === value} onClick={() => setSpeed(value)}>{value}×</button>)}</div>
            </div>
          </div>
          <div className="decision-strip">{players.map(player => {
            const decision = configuring ? undefined : replay!.decisions.filter(item => item.simulation_time <= time && item.actions[player]).at(-1);
            const detail = decision?.details?.[player];
            return <div key={player} className={player} style={teamStyle(characterId(viewConfig, player))}><span className="decision-player">{actorName(player, viewConfig)} <span>{configuring ? '配置预览' : '已提交动作'}</span></span><strong>{viewTime === 0 ? '待命' : actionText(decision?.actions[player] ?? null, viewConfig, player)}</strong>
              {detail && detail.source !== 'script' && <div className={`decision-detail ${detail.source === 'fallback' ? 'fallback-detail' : ''}`}>
                <p>{detail.summary}</p><small title={`请求：${detail.requested_model}；返回：${detail.returned_model || '无'}；工具：${detail.selected_tool || '无'}；人物提示词：${detail.character_prompt_version || '通用'}；调用次数：${detail.attempts}；错误：${detail.errors.join(', ') || '无'}`}>
                  {detail.source === 'counterfactual'
                    ? `风险加权收益 ${detail.risk_weighted_score?.toFixed(2) ?? '无'} · ${detail.selected_tool || '无'}`
                    : `${(detail.latency_ms / 1000).toFixed(2)}s · ${detail.total_tokens} tokens${detail.risk_weighted_score != null ? ` · 推演 ${detail.risk_weighted_score.toFixed(2)}` : ''}${detail.source === 'fallback' ? ' · 保底' : detail.errors.length ? ' · 已修正' : ''}`}</small>
              </div>}
            </div>;
          })}</div>
        </div>
        <FighterPanel player="p2" frame={viewFrame} config={viewConfig} agent={configuring ? strategies.p2 : replay!.agents.p2} />
      </section> : <div className="initial-scene"><img src="/assets/arena.png" alt="樱花与远山环绕的月下庭院擂台" /><span>{loading || (configuring ? '正在准备阵容预览' : '等待对局')}</span></div>}

      <section className="records-section">
        <div className="events-section">
          <div className="section-heading"><h2><Activity size={17} /> 战斗事件 <span>{visibleEvents.length}</span></h2>
            <select aria-label="事件筛选" value={filter} onChange={event => setFilter(event.target.value)}><option value="key">关键事件</option><option value="all">全部动作</option></select></div>
          <div className="event-table" data-testid="event-list">
            <div className="event-columns"><span>时间</span><span>角色</span><span>事件</span><span>生命伤害</span></div>
            <div className="event-rows">{visibleEvents.length ? visibleEvents.map((event, eventIndex) => <div key={`${event.action_id}-${event.status}-${event.simulation_time}-${eventIndex}`} className={`event-row ${event.status === 'hit' ? 'damage-event' : ''}`}>
              <button title="跳转到此事件" aria-label={`跳转到 ${event.simulation_time.toFixed(2)} 秒`} onClick={() => seek(event.simulation_time)} disabled={seekDisabled}>{event.simulation_time.toFixed(2)}<ArrowRight size={11} /></button>
              <span className={event.actor ?? ''}>{actorName(event.actor, replay?.config)}</span><span>{eventText(event, replay?.config)}</span><strong>{event.effects.health_damage ? `-${event.effects.health_damage.toFixed(1)}` : '·'}</strong>
            </div>) : <div className="empty-events"><Activity size={20} /><span>{configuring ? '开始对局后显示战斗事件' : time === 0 ? '等待首个战斗事件' : '当前没有关键事件'}</span></div>}</div>
          </div>
        </div>
        <div className="history-section">
          <div className="section-heading"><h2><History size={17} /> 对局记录</h2><div className="history-tools">
            <input ref={fileInput} type="file" accept=".jsonl,application/x-ndjson" hidden onChange={event => { void importReplay(event.target.files?.[0]); event.target.value = ''; }} />
            <button className="icon-button" aria-label="导入回放" title="导入回放" disabled={Boolean(loading) || live} onClick={() => fileInput.current?.click()}><ArrowUpFromLine size={16} /></button>
            {replay?.summary.result && !live && <a className="icon-button" aria-label="导出回放" title="导出回放" href={`${replayBase}/${replay.replay_id}/export`} download><ArrowDownToLine size={16} /></a>}
          </div></div>
          <div className="history-list">{incomplete.map(item => <div key={item.replay_id} className="history-entry incomplete-entry">
            {confirmDeleteId === item.replay_id ? <div className="history-confirm"><span>删除未完成对局 #{item.replay_id.slice(0, 8)}？</span>
              <button autoFocus onClick={() => setConfirmDeleteId(null)} disabled={Boolean(loading)}>取消</button>
              <button className="confirm-delete" onClick={() => void deleteReplay(item.replay_id)} disabled={Boolean(loading)}>删除</button>
            </div> : <>
            <button className="history-row incomplete-row" onClick={() => startLiveMatch(item.replay_id)} disabled={Boolean(loading) || live} aria-label={`继续对局 ${item.replay_id.slice(0, 8)}`}>
              <div className="history-match"><span>{AGENTS[item.agents.p1] || item.agents.p1}<small>vs</small>{AGENTS[item.agents.p2] || item.agents.p2}</span><strong><Play size={11} fill="currentColor" /> 继续</strong></div>
              <div className="history-meta"><span>#{item.replay_id.slice(0, 8)} · {item.turn_count} 回合</span><span>{new Date(item.updated_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</span></div>
            </button>
            <button className="history-delete icon-button" aria-label={`删除未完成对局 ${item.replay_id.slice(0, 8)}`} title="删除未完成对局" disabled={Boolean(loading) || live}
              onClick={() => setConfirmDeleteId(item.replay_id)}><Trash2 size={15} /></button>
            </>}
          </div>)}
          {history.map(item => <div key={item.replay_id} className="history-entry">
            {confirmDeleteId === item.replay_id ? <div className="history-confirm"><span>删除 #{item.replay_id.slice(0, 8)}？</span>
              <button autoFocus onClick={() => setConfirmDeleteId(null)} disabled={Boolean(loading)}>取消</button>
              <button className="confirm-delete" onClick={() => void deleteReplay(item.replay_id)} disabled={Boolean(loading)}>删除</button>
            </div> : <>
            <button className={`history-row ${item.replay_id === replay?.replay_id ? 'selected' : ''}`} onClick={() => selectReplay(item.replay_id)} disabled={Boolean(loading) || live} aria-label={`载入对局 ${item.replay_id.slice(0, 8)}`}>
              <div className="history-match"><span>{AGENTS[item.agents.p1] || item.agents.p1}<small>vs</small>{AGENTS[item.agents.p2] || item.agents.p2}</span><strong>{item.result.simulation_time.toFixed(2)}s</strong></div>
              <div className="history-meta"><span>#{item.replay_id.slice(0, 8)}</span><span>{new Date(item.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</span></div>
            </button>
            <button className="history-delete icon-button" aria-label={`删除对局 ${item.replay_id.slice(0, 8)}`} title="删除对局" disabled={Boolean(loading) || live}
              onClick={() => setConfirmDeleteId(item.replay_id)}><Trash2 size={15} /></button>
            </>}
          </div>)}
          {!history.length && !incomplete.length && <div className="empty-events"><History size={20} /><span>暂无对局记录</span></div>}</div>
        </div>
      </section>
      </div>
    </main>
    <footer><span><Crystal size={12} /> AGENT ARENA <span className="footer-note">· 每一回合，都有新的可能</span></span><span>{replay ? `规则 ${replay.rules_version} · ${replay.frames.length} 帧` : '樱庭 · 月下试炼'}</span></footer>
  </div>;
}
