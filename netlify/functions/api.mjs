/**
 * Netlify Function wrapper for the Express API server.
 *
 * Fliggy search runs directly via Node.js (no Python needed).
 * Ctrip scraper is unavailable on Netlify (requires Chrome + Python),
 * but the search endpoint falls back gracefully with just Fliggy results.
 */

process.env.NETLIFY = 'true';

import serverless from 'serverless-http';
import app from '../../server.js';

export const handler = serverless(app);
