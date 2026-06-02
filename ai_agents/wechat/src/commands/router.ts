import type { Session } from '../session.js';
import { logger } from '../logger.js';
import { handleHelp, handleClear, handleCwd, handleStatus, handleHistory, handleReset, handleCompact, handleUndo, handleVersion, handlePrompt, handleUnknown, handleMyId } from './handlers.js';

export interface CommandContext {
  accountId: string;
  session: Session;
  updateSession: (partial: Partial<Session>) => void;
  clearSession: () => Session;
  getChatHistoryText?: (limit?: number) => string;
  text: string;
  /** 发送消息的用户微信 ID */
  fromUserId?: string;
}

export interface CommandResult {
  reply?: string;
  handled: boolean;
  difyPrompt?: string; // If set, this text should be sent to Dify
}

/**
 * Parse and dispatch a slash command.
 *
 * Supported commands:
 *   /help     - Show help text
 *   /clear    - Clear current session
 *   /status   - Show session info
 *   /prompt   - View/set system prompt
 */
export function routeCommand(ctx: CommandContext): CommandResult {
  const text = ctx.text.trim();

  if (!text.startsWith('/')) {
    return { handled: false };
  }

  const spaceIdx = text.indexOf(' ');
  const cmd = (spaceIdx === -1 ? text.slice(1) : text.slice(1, spaceIdx)).toLowerCase();
  const args = spaceIdx === -1 ? '' : text.slice(spaceIdx + 1).trim();

  logger.info(`Slash command: /${cmd} ${args}`.trimEnd());

  switch (cmd) {
    case 'help':
      return handleHelp(args);
    case 'clear':
      return handleClear(ctx);
    case 'reset':
      return handleReset(ctx);
    case 'cwd':
      return handleCwd(ctx, args);
    case 'prompt':
      return handlePrompt(ctx, args);
    case 'status':
      return handleStatus(ctx);
    case 'history':
      return handleHistory(ctx, args);
    case 'undo':
      return handleUndo(ctx, args);
    case 'compact':
      return handleCompact(ctx);
    case 'version':
    case 'v':
      return handleVersion();
    case 'myid':
      return handleMyId(ctx);
    default:
      return handleUnknown(cmd, args);
  }
}
