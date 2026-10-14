import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

export function atomicWrite(file, data, mode = 0o600) {
  const temporary = path.join(path.dirname(file), `.${path.basename(file)}.${randomUUID()}.tmp`);
  try {
    fs.writeFileSync(temporary, data, { mode, flag: 'wx' });
    fs.renameSync(temporary, file);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}
export function validateKeys(keys) {
  for (const name of ['coding', 'writing', 'video']) {
    // Written to a dotenv file: reject quotes, newlines, comments, interpolation and spaces.
    if (typeof keys[name] !== 'string' || !/^[A-Za-z0-9_.-]{8,512}$/.test(keys[name])) {
      throw new Error(`Invalid ${name} API key`);
    }
  }
  return keys;
}
