/**
 * Tongcheng (同程旅行) flight search client.
 *
 * Strategy (tried in order):
 *   1. Open platform API (via 北京艺龙) — requires business cooperation
 *   2. CDP scraper subprocess — local only, requires Chrome on port 9223
 *
 * Tongcheng's open platform (via eLong subsidiary) requires direct business
 * cooperation — no self-service developer registration available.
 * Contact: fxswb@ly.com
 *
 * On Netlify (no Chrome), this source returns empty results.
 */

import { spawn } from 'child_process';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __dirname = (() => {
  try { return dirname(fileURLToPath(import.meta.url)).replace('/sources', ''); }
  catch { return process.cwd(); }
})();

const CDP_PORT = 9223;

export const meta = {
  id: 'tongcheng',
  name: '同程旅行',
  requiresChrome: true,
  provides: ['price'],
};

async function isChromeAvailable() {
  try {
    const resp = await fetch(`http://localhost:${CDP_PORT}/json/version`);
    return resp.ok;
  } catch {
    return false;
  }
}

function searchViaCdp(origin, destination, date) {
  return new Promise((resolve) => {
    const proc = spawn('python3', [
      '-u', join(__dirname, 'cdp_scraper.py'),
      origin, destination, date,
      '--platform', 'tongcheng',
      '--json',
    ], {
      cwd: __dirname,
      env: {
        ...process.env,
        NO_PROXY: 'localhost,127.0.0.1,::1',
        PYTHONIOENCODING: 'utf-8',
        PYTHONUNBUFFERED: '1',
      },
      timeout: 45000,
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data) => { stdout += data.toString(); });
    proc.stderr.on('data', (data) => { stderr += data.toString(); });

    proc.on('close', (code) => {
      if (stderr) {
        const lines = stderr.split('\n').filter(l => l.trim());
        const relevant = lines.filter(l =>
          l.includes('[Tongcheng]') || l.includes('Error') || l.includes('Traceback')
        );
        if (relevant.length > 0) console.log('Tongcheng CDP:', relevant.join('\n'));
      }
      try {
        const startMarker = '__JSON_START__';
        const endMarker = '__JSON_END__';
        const startIdx = stdout.indexOf(startMarker);
        const endIdx = stdout.indexOf(endMarker);
        if (startIdx >= 0 && endIdx > startIdx) {
          const jsonStr = stdout.slice(startIdx + startMarker.length, endIdx).trim();
          const parsed = JSON.parse(jsonStr);
          resolve(parsed.tongcheng || []);
        } else {
          resolve([]);
        }
      } catch (err) {
        console.error('Tongcheng CDP parse error:', err.message);
        resolve([]);
      }
    });

    proc.on('error', (err) => {
      console.error('Tongcheng CDP start error:', err.message);
      resolve([]);
    });
  });
}

export async function search(params) {
  const chromeOk = await isChromeAvailable();
  if (chromeOk) {
    console.log('Tongcheng: using CDP scraper (Chrome available)');
    return searchViaCdp(params.origin, params.destination, params.date);
  }

  console.log('Tongcheng: Chrome not available, returning 0 results');
  return [];
}

export async function isAvailable() {
  if (process.env.NETLIFY) return false;
  return isChromeAvailable();
}
