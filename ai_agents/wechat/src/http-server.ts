/**
 * HTTP API 服务 — Dify 可通过此接口向微信主动推送消息
 *
 * 端点:
 *   POST /send  { toUserId: string, text: string }
 *   GET  /health
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { logger } from './logger.js';

export interface HttpApiDeps {
  sendText: (toUserId: string, contextToken: string, text: string) => void;
  /** 管理员微信 ID —— 所有推送只发给这个人 */
  adminUserId: string;
}

export function startHttpServer(port: number, deps: HttpApiDeps) {
  const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
    // CORS
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    // Health check
    if (req.method === 'GET' && req.url === '/health') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'ok', service: 'wechat-dify-bridge' }));
      return;
    }

    // Send message — 仅允许发给管理员，防止数据泄露
    if (req.method === 'POST' && req.url === '/send') {
      try {
        const body = await readBody(req);
        const { text } = JSON.parse(body);

        if (!text) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ success: false, error: 'text is required' }));
          return;
        }

        if (!deps.adminUserId) {
          res.writeHead(500, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ success: false, error: 'adminUserId not set' }));
          return;
        }

        deps.sendText(deps.adminUserId, '', text);

        logger.info('HTTP push enqueued for admin', { adminId: deps.adminUserId, textLen: text.length });
        res.writeHead(202, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ success: true, queued: true }));
      } catch (err: any) {
        logger.error('HTTP /send error', { error: err.message });
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ success: false, error: err.message }));
      }
      return;
    }

    // 404
    res.writeHead(404);
    res.end('Not found');
  });

  server.listen(port, () => {
    logger.info(`HTTP API listening on port ${port}`);
    console.log(`🌐 HTTP API: http://localhost:${port}/send`);
  });

  return server;
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}
