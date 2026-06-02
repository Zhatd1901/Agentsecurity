import { homedir } from 'node:os';
import { join } from 'node:path';

export const DATA_DIR = process.env.WCD_DATA_DIR || join(homedir(), '.wechat-dify-bridge');
