// Pi owns the agent/tool loop; Python owns provider requests and the arena.
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';
import { runAgentLoop } from '../pi-main/packages/agent/src/agent-loop.ts';
import { createAssistantMessageEventStream } from '../pi-main/packages/ai/dist/index.js';

const sessions = new Map();
const MAX_PAIRS = 4;
const MAX_CONTEXT_BYTES = 65_536;
const instruction = '依据最新观察选择一个动作。动作工具会等待结算，并在下次可行动时返回新观察。';
const send = message => process.stdout.write(`${JSON.stringify(message)}\n`);

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function openaiMessages(messages) {
  return messages.map(message => {
    if (message.role === 'system') return { role: 'system', content: message.content };
    if (message.role === 'assistant') {
      return { role: 'assistant', content: null, tool_calls: message.content.map(call => ({
        id: call.id, type: 'function',
        function: { name: call.name, arguments: JSON.stringify(call.arguments) },
      })) };
    }
    const content = message.content.map(block => block.text).join('\n');
    if (message.role === 'toolResult') {
      return { role: 'tool', tool_call_id: message.toolCallId, content };
    }
    return { role: 'user', content };
  });
}

function toolsFor(session) {
  // A provider failure must still submit the engine's rest fallback, even when
  // the character's recovery skill is cooling down. Never advertise this tool.
  const specifications = [...session.packet.tools.map(tool => tool.function), {
    name: '__arena_fallback', description: 'Engine fallback.',
    parameters: { type: 'object', properties: {}, required: [] },
  }];
  return specifications.map(tool => ({
    name: tool.name, label: tool.name, description: tool.description, parameters: tool.parameters,
    async execute() {
      // Set the waiter before publishing the action. No arena state changes here.
      session.waiter = deferred();
      send({ type: 'action', player: session.player, sequence: session.packet.sequence });
      const next = await session.waiter.promise;
      session.waiter = undefined;
      if (!next) return { content: [], details: {}, terminate: true };
      session.packet = next;
      return { content: [{ type: 'text', text: next.observation }], details: {} };
    },
  }));
}

function compact(messages, session) {
  const pairs = [];
  for (let index = 0; index < messages.length - 1; index++) {
    const call = messages[index];
    const result = messages[index + 1];
    if (call.role === 'assistant' && call.content[0]?.name !== '__arena_fallback' && result.role === 'toolResult'
        && call.content[0]?.id === result.toolCallId) {
      const { decision_summary: _summary, ...arguments_ } = call.content[0].arguments;
      pairs.push([{ ...call, content: [{ ...call.content[0], arguments: arguments_ }] }, result]);
    }
  }
  const retained = pairs.slice(-MAX_PAIRS);
  if (retained.length) {
    const last = retained.length - 1;
    retained[last] = [retained[last][0], { ...retained[last][1],
      content: [{ type: 'text', text: session.packet.observation }],
    }];
  }
  // Old full observations duplicate recent_turns in the current observation.
  for (let index = 0; index < retained.length - 1; index++) {
    const result = retained[index][1];
    const view = JSON.parse(result.content[0].text);
    retained[index] = [retained[index][0], { ...result, content: [{ type: 'text', text: JSON.stringify({
      turn: view.turn, simulation_time: view.simulation_time,
    }) }] }];
  }
  const system = {
    role: 'system', content: session.packet.system, timestamp: 0,
    toolsAdded: toolsFor(session).map(({ name, description, parameters }) => ({ name, description, parameters })),
  };
  const user = { role: 'user', content: [{ type: 'text', text: instruction }], timestamp: 0 };
  let output = [system, user, ...retained.flat()];
  const size = () => Buffer.byteLength(JSON.stringify({
    messages: openaiMessages(output), tools: session.packet.tools,
  }));
  // Leave room for generation parameters and a possible validation retry.
  while (size() > MAX_CONTEXT_BYTES - 4096 && retained.length > 1) {
    retained.shift();
    output = [system, user, ...retained.flat()];
  }
  if (!retained.length || size() > MAX_CONTEXT_BYTES - 4096) {
    output = [system, { ...user, content: [{ type: 'text', text: session.packet.observation }] }];
  }
  return output;
}

async function run(session) {
  const controller = new AbortController();
  session.controller = controller;
  const model = {
    id: session.packet.model, name: session.packet.model, provider: 'deepseek',
    api: 'openai-completions', baseUrl: '', reasoning: false, input: ['text'],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 65536, maxTokens: 2048,
  };
  const streamFn = async (_model, context) => {
    session.response = deferred();
    send({
      type: 'request', player: session.player, sequence: session.packet.sequence,
      messages: openaiMessages(context.messages), history: session.context.messages,
    });
    const response = await session.response.promise;
    const stream = createAssistantMessageEventStream();
    const usage = response?.usage ?? {};
    const message = {
      role: 'assistant', api: model.api, provider: model.provider, model: model.id,
      content: response ? [{ type: 'toolCall', id: `${session.player}-${randomUUID()}`,
        name: response.name, arguments: response.arguments }] : [],
      usage: {
        input: Math.max(0, (usage.prompt_tokens ?? 0) - (usage.prompt_cache_hit_tokens ?? 0)),
        output: usage.completion_tokens ?? 0, cacheRead: usage.prompt_cache_hit_tokens ?? 0,
        cacheWrite: 0, totalTokens: usage.total_tokens ?? 0,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
      },
      stopReason: response ? 'toolUse' : 'aborted', timestamp: Date.now(),
    };
    stream.push(response ? { type: 'done', reason: 'toolUse', message }
      : { type: 'error', reason: 'aborted', error: message });
    return stream;
  };
  session.context = { messages: compact(session.packet.history, session), tools: toolsFor(session) };
  await runAgentLoop([], session.context, {
    model, convertToLlm: messages => messages, toolExecution: 'sequential',
    prepareNextTurn(turn) {
      session.context = { messages: compact(turn.context.messages, session), tools: toolsFor(session) };
      // The host stores replays. Keep Pi's invocation result buffer bounded too.
      turn.newMessages.length = 0;
      return { context: session.context };
    },
    finishTurn: () => controller.signal.aborted ? { action: 'end' } : undefined,
  }, event => {
    if (event.type === 'tool_execution_end' && event.isError && !controller.signal.aborted) {
      controller.abort();
      send({ type: 'failed', player: session.player });
    }
  }, controller.signal, streamFn);
}

function stop() {
  for (const session of sessions.values()) {
    session.controller.abort();
    session.waiter?.resolve(null);
    session.response?.resolve(null);
  }
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', line => {
  const packet = JSON.parse(line);
  if (packet.type === 'stop') {
    stop();
    input.close();
    process.stdin.destroy();
    return;
  }
  const session = sessions.get(packet.player);
  if (packet.type === 'response') {
    if (session?.packet.sequence === packet.sequence) session.response.resolve(packet);
  } else if (packet.type === 'observe') {
    if (session) session.waiter.resolve(packet);
    else {
      const created = { player: packet.player, packet };
      sessions.set(packet.player, created);
      void run(created).catch(() => send({ type: 'failed', player: packet.player }));
    }
  }
});
input.on('close', stop);
send({ type: 'ready' });
