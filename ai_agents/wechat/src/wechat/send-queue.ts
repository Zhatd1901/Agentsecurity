/**
 * 消息发送队列 —— 串行化 sendMessage 调用，防止并发触发微信频率限制。
 *
 * - 同一时间只发送一条消息
 * - 基础间隔 3 秒，限频时自适应延长（最多 5 分钟）
 * - 限频后自动降温，恢复后逐步缩短间隔
 * - enqueue() 立即返回，不阻塞调用方
 */
import { WeChatApi } from './api.js';
import {
  MessageItemType,
  MessageType,
  MessageState,
  type MessageItem,
  type OutboundMessage,
} from './types.js';
import { logger } from '../logger.js';

interface QueuedMessage {
  toUserId: string;
  contextToken: string;
  text: string;
  clientId: string;
  resolve: () => void;
  reject: (err: Error) => void;
}

const BASE_INTERVAL_MS = 3_000;        // 基础发送间隔 3s
const MAX_COOLDOWN_MS = 5 * 60_000;    // 最长冷却 5 分钟
const COOLDOWN_MULTIPLIER = 3;         // 每次限频间隔乘以 3
const RECOVERY_DECAY = 0.5;            // 成功后间隔减半（不低于基础间隔）

export function createSendQueue(api: WeChatApi, botAccountId: string) {
  const queue: QueuedMessage[] = [];
  let processing = false;
  let clientCounter = 0;
  let currentInterval = BASE_INTERVAL_MS;
  let consecutiveRateLimits = 0;

  function generateClientId(): string {
    return `wcc-${Date.now()}-${++clientCounter}`;
  }

  async function processQueue(): Promise<void> {
    if (processing) return;
    processing = true;

    while (queue.length > 0) {
      const item = queue.shift()!;

      try {
        const items: MessageItem[] = [
          { type: MessageItemType.TEXT, text_item: { text: item.text } },
        ];

        const msg: OutboundMessage = {
          from_user_id: botAccountId,
          to_user_id: item.toUserId,
          client_id: item.clientId,
          message_type: MessageType.BOT,
          message_state: MessageState.FINISH,
          context_token: item.contextToken,
          item_list: items,
        };

        logger.info('Sending queued text message', {
          toUserId: item.toUserId,
          clientId: item.clientId,
          textLength: item.text.length,
          queueRemaining: queue.length,
          currentInterval,
          consecutiveRateLimits,
        });

        await api.sendMessage({ msg });

        // 发送成功 → 逐步恢复间隔
        consecutiveRateLimits = 0;
        currentInterval = Math.max(
          BASE_INTERVAL_MS,
          Math.floor(currentInterval * RECOVERY_DECAY),
        );

        logger.info('Queued text message sent', {
          toUserId: item.toUserId,
          clientId: item.clientId,
        });
        item.resolve();
      } catch (err: any) {
        if (err.message === 'SEND_RATE_LIMITED') {
          // 限频 → 消息放回队首，延长冷却时间
          consecutiveRateLimits++;
          currentInterval = Math.min(
            MAX_COOLDOWN_MS,
            currentInterval * COOLDOWN_MULTIPLIER,
          );

          logger.warn('Rate-limited, requeuing with cooldown', {
            clientId: item.clientId,
            newInterval: currentInterval,
            consecutiveRateLimits,
            queueLength: queue.length,
          });

          queue.unshift(item);
        } else {
          // 其他错误 → 记录并放弃该消息
          logger.error('Failed to send queued message (non-retryable)', {
            toUserId: item.toUserId,
            clientId: item.clientId,
            error: err.message,
          });
          item.reject(err);
        }
      }

      // 发送间隔（含冷却时间）
      if (queue.length > 0) {
        await new Promise((r) => setTimeout(r, currentInterval));
      }
    }

    processing = false;
  }

  /** 将消息加入队列，立即返回。不等待实际发送结果。 */
  function enqueue(
    toUserId: string,
    contextToken: string,
    text: string,
  ): void {
    const clientId = generateClientId();
    queue.push({
      toUserId,
      contextToken,
      text,
      clientId,
      resolve: () => {
        logger.debug('Queued message delivered', { clientId });
      },
      reject: (err) => {
        logger.error('Queued message failed', { clientId, error: err.message });
      },
    });
    logger.debug('Message enqueued', { clientId, queueLength: queue.length });
    processQueue();
  }

  return { enqueue, getQueueLength: () => queue.length };
}
