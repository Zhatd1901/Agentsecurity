import { WeChatApi } from './api.js';
import { createSendQueue } from './send-queue.js';
import { logger } from '../logger.js';

export function createSender(api: WeChatApi, botAccountId: string) {
  const queue = createSendQueue(api, botAccountId);

  /** 将消息加入发送队列（串行化，避免触发微信频率限制）。立即返回，不阻塞。 */
  function sendText(toUserId: string, contextToken: string, text: string): void {
    queue.enqueue(toUserId, contextToken, text);
  }

  return { sendText, getQueueLength: queue.getQueueLength };
}
