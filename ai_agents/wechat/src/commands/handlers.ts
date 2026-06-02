import type { CommandContext, CommandResult } from './router.js';
import { loadConfig, saveConfig } from '../config.js';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HELP_TEXT = `可用命令：

会话管理：
  /help             显示帮助
  /clear            清除当前会话（开始新对话）
  /reset            完全重置（包括工作目录等设置）
  /status           查看当前会话状态
  /compact          压缩上下文（开始新 Dify 会话，保留历史）
  /history [数量]   查看对话记录（默认最近20条）
  /undo [数量]      撤销最近对话（默认1条）

配置：
  /cwd [路径]       查看或切换工作目录
  /prompt [内容]    查看或设置系统提示词（全局生效）
  /myid             查看你的微信 ID（用于配置推送通知）

其他：
  /version          查看版本信息

直接输入文字即可与 Dify AI 对话`;

export function handleHelp(_args: string): CommandResult {
  return { reply: HELP_TEXT, handled: true };
}

export function handleClear(ctx: CommandContext): CommandResult {
  const newSession = ctx.clearSession();
  Object.assign(ctx.session, newSession);
  return { reply: '✅ 会话已清除，下次消息将开始新会话。', handled: true };
}

export function handleCwd(ctx: CommandContext, args: string): CommandResult {
  if (!args) {
    return { reply: `当前工作目录: ${ctx.session.workingDirectory}\n用法: /cwd <路径>`, handled: true };
  }
  ctx.updateSession({ workingDirectory: args });
  return { reply: `✅ 工作目录已切换为: ${args}`, handled: true };
}

export function handleStatus(ctx: CommandContext): CommandResult {
  const s = ctx.session;
  const lines = [
    '📊 会话状态',
    '',
    `工作目录: ${s.workingDirectory}`,
    `系统提示词: ${s.systemPrompt ?? '无'}`,
    `Dify 会话ID: ${s.difyConversationId ?? '无'}`,
    `状态: ${s.state}`,
    `聊天记录: ${s.chatHistory?.length ?? 0} 条`,
  ];
  return { reply: lines.join('\n'), handled: true };
}

const MAX_HISTORY_LIMIT = 100;

export function handleHistory(ctx: CommandContext, args: string): CommandResult {
  const limit = args ? parseInt(args, 10) : 20;
  if (isNaN(limit) || limit <= 0) {
    return { reply: '用法: /history [数量]\n例: /history 50（显示最近50条对话）', handled: true };
  }
  const effectiveLimit = Math.min(limit, MAX_HISTORY_LIMIT);
  const historyText = ctx.getChatHistoryText?.(effectiveLimit) || '暂无对话记录';
  return { reply: `📝 对话记录（最近${effectiveLimit}条）:\n\n${historyText}`, handled: true };
}

export function handleReset(ctx: CommandContext): CommandResult {
  const newSession = ctx.clearSession();
  newSession.workingDirectory = process.cwd();
  newSession.difyConversationId = undefined;
  Object.assign(ctx.session, newSession);
  return { reply: '✅ 会话已完全重置，所有设置恢复默认。', handled: true };
}

export function handleCompact(ctx: CommandContext): CommandResult {
  const currentConvId = ctx.session.difyConversationId;
  if (!currentConvId) {
    return { reply: 'ℹ️ 当前没有活动的 Dify 会话，无需压缩。', handled: true };
  }
  ctx.updateSession({ difyConversationId: undefined });
  return {
    reply: '✅ 上下文已压缩\n\n下次消息将开始新的 Dify 会话\n聊天历史已保留，可用 /history 查看',
    handled: true,
  };
}

export function handleUndo(ctx: CommandContext, args: string): CommandResult {
  const count = args ? parseInt(args, 10) : 1;
  if (isNaN(count) || count <= 0) {
    return { reply: '用法: /undo [数量]\n例: /undo 2（撤销最近2条对话）', handled: true };
  }
  const history = ctx.session.chatHistory || [];
  if (history.length === 0) {
    return { reply: '⚠️ 没有对话记录可撤销', handled: true };
  }
  const actualCount = Math.min(count, history.length);
  ctx.session.chatHistory = history.slice(0, -actualCount);
  ctx.updateSession({ chatHistory: ctx.session.chatHistory });
  return { reply: `✅ 已撤销最近 ${actualCount} 条对话`, handled: true };
}

export function handleVersion(): CommandResult {
  try {
    const __dirname = fileURLToPath(new URL('.', import.meta.url));
    const pkg = JSON.parse(readFileSync(join(__dirname, '..', '..', 'package.json'), 'utf-8'));
    const version = pkg.version || 'unknown';
    return { reply: `wechat-dify-bridge v${version}`, handled: true };
  } catch {
    return { reply: 'wechat-dify-bridge (version unknown)', handled: true };
  }
}

export function handlePrompt(_ctx: CommandContext, args: string): CommandResult {
  const config = loadConfig();
  if (!args) {
    const current = config.systemPrompt;
    if (current) {
      return { reply: `📝 当前系统提示词:\n${current}\n\n用法:\n/prompt <提示词>  — 设置\n/prompt clear   — 清除`, handled: true };
    }
    return { reply: '📝 暂无系统提示词\n\n用法: /prompt <提示词>\n例: /prompt 用中文回答我', handled: true };
  }
  if (args.trim().toLowerCase() === 'clear') {
    config.systemPrompt = undefined;
    saveConfig(config);
    return { reply: '✅ 系统提示词已清除', handled: true };
  }
  config.systemPrompt = args.trim();
  saveConfig(config);
  return { reply: `✅ 系统提示词已设置:\n${config.systemPrompt}`, handled: true };
}

export function handleMyId(ctx: CommandContext): CommandResult {
  const myId = ctx.fromUserId || '未知（请在聊天中发送 /myid 获取）';
  return {
    reply: `你的微信 ID: ${myId}\n\n将此 ID 加入配置文件 ~/.wechat-dify-bridge/config.env:\nadminUserId=${myId}`,
    handled: true,
  };
}

export function handleUnknown(cmd: string, _args: string): CommandResult {
  return {
    handled: true,
    reply: `未知命令: /${cmd}\n输入 /help 查看可用命令`,
  };
}
