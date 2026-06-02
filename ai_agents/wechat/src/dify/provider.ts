/**
 * Dify Chat API 客户端 — 流式 SSE 模式
 */
import { logger } from '../logger.js';

// ── 类型定义 ──

export interface DifyConfig {
  apiKey: string;
  baseUrl: string;
}

export interface QueryOptions {
  prompt: string;
  user: string;
  conversationId?: string;
  inputs?: Record<string, string>;
  /** 流式文本回调 (delta) */
  onText?: (delta: string) => Promise<void> | void;
  /** 可选 AbortController */
  abortController?: AbortController;
}

export interface QueryResult {
  answer: string;
  conversationId: string;
  messageId?: string;
  error?: string;
}

// ── SSE 解析 ──

interface SSEChunk {
  event: string;
  conversation_id?: string;
  message_id?: string;
  answer?: string;
  metadata?: unknown;
  status?: number;
  code?: string;
  message?: string;
  data?: {
    id?: string;
    workflow_id?: string;
    status?: string;
    outputs?: Record<string, unknown>;
    error?: string;
  };
}

function parseSSELine(line: string): SSEChunk | null {
  if (!line.startsWith('data: ')) return null;
  const jsonStr = line.slice(6).trim();
  if (!jsonStr) return null;
  try {
    return JSON.parse(jsonStr);
  } catch {
    return null;
  }
}

// ── Dify Chat 请求 ──

export async function difyQuery(
  config: DifyConfig,
  options: QueryOptions,
): Promise<QueryResult> {
  const {
    prompt,
    user,
    conversationId,
    inputs = {},
    onText,
    abortController,
  } = options;

  const baseUrl = config.baseUrl.replace(/\/+$/, '');
  const url = `${baseUrl}/v1/chat-messages`;

  const body = {
    query: prompt,
    user,
    conversation_id: conversationId || '',
    response_mode: 'streaming',
    inputs,
  };

  logger.info('Dify query', { user, hasConversationId: !!conversationId, promptLen: prompt.length });

  const controller = abortController || new AbortController();
  const timeoutMs = 5 * 60 * 1000; // 5 分钟超时

  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${config.apiKey}`,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(`Dify API HTTP ${res.status}: ${errorText}`);
    }

    // 流式读取 SSE
    const reader = res.body?.getReader();
    if (!reader) throw new Error('No response body stream');

    const decoder = new TextDecoder();
    let buffer = '';
    let resultConversationId = conversationId || '';
    let resultMessageId = '';
    let fullAnswer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      // 保留最后一个可能不完整的行
      buffer = lines.pop() || '';

      for (const line of lines) {
        const chunk = parseSSELine(line);
        if (!chunk) continue;

        switch (chunk.event) {
          case 'message':
            if (chunk.conversation_id) resultConversationId = chunk.conversation_id;
            if (chunk.message_id) resultMessageId = chunk.message_id;
            if (chunk.answer) {
              fullAnswer += chunk.answer;
              await onText?.(chunk.answer);
            }
            break;

          case 'message_end':
            if (chunk.conversation_id) resultConversationId = chunk.conversation_id;
            if (chunk.message_id) resultMessageId = chunk.message_id;
            break;

          case 'error':
            logger.error('Dify stream error', chunk);
            return {
              answer: fullAnswer,
              conversationId: resultConversationId,
              messageId: resultMessageId,
              error: chunk.message || `Dify error: ${chunk.status}`,
            };

          default:
            logger.debug('Dify unknown event', { event: chunk.event });
        }
      }
    }

    logger.info('Dify query complete', {
      conversationId: resultConversationId,
      answerLen: fullAnswer.length,
    });

    return {
      answer: fullAnswer,
      conversationId: resultConversationId,
      messageId: resultMessageId,
    };
  } catch (err: any) {
    if (err.name === 'AbortError') {
      logger.info('Dify query aborted');
      throw err;
    }
    logger.error('Dify query failed', { error: err.message });
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

// ── Dify Workflow 请求 ──

export interface WorkflowOptions {
  user: string;
  inputs?: Record<string, string>;
  onText?: (delta: string) => Promise<void> | void;
  abortController?: AbortController;
}

export interface WorkflowResult {
  answer: string;
  error?: string;
}

export async function difyWorkflowQuery(
  config: DifyConfig,
  options: WorkflowOptions,
): Promise<WorkflowResult> {
  const { user, inputs = {}, onText, abortController } = options;

  const baseUrl = config.baseUrl.replace(/\/+$/, '');
  const url = `${baseUrl}/v1/workflows/run`;

  const body = {
    inputs,
    response_mode: 'streaming',
    user,
  };

  logger.info('Dify workflow query', { user, inputs });

  const controller = abortController || new AbortController();
  const timeoutMs = 5 * 60 * 1000;
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${config.apiKey}`,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(`Dify API HTTP ${res.status}: ${errorText}`);
    }

    const reader = res.body?.getReader();
    if (!reader) throw new Error('No response body stream');

    const decoder = new TextDecoder();
    let buffer = '';
    let fullOutput = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const chunk = parseSSELine(line);
        if (!chunk) continue;

        switch (chunk.event) {
          case 'workflow_finished':
            if (chunk.data?.outputs) {
              const outputs = chunk.data.outputs;
              const text = String(outputs.text || outputs.output || JSON.stringify(outputs));
              fullOutput = text;
              await onText?.(text);
            }
            break;

          case 'error':
            logger.error('Dify workflow error', chunk);
            return { answer: fullOutput, error: chunk.message || 'Workflow error' };

          default:
            // node_started, node_finished 等忽略
            break;
        }
      }
    }

    return { answer: fullOutput };
  } catch (err: any) {
    if (err.name === 'AbortError') {
      logger.info('Dify workflow aborted');
      throw err;
    }
    logger.error('Dify workflow failed', { error: err.message });
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}
