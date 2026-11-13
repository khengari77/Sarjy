import { createServer } from 'node:http';
import { timingSafeEqual, createHash } from 'node:crypto';
// Runtime-generated adapter output is verified by the HTTP boundary test.
const handlerPath = './build/handler.js';
const { handler } = await import(handlerPath);

const protectedMode = process.env.DEVELOPMENT_PROTECTED === 'true';
const { DEVELOPMENT_USER: user, DEVELOPMENT_PASSWORD: password, WEB_SERVICE_SECRET: secret } = process.env;
if (protectedMode && (!user || !password || !secret)) throw new Error('DEVELOPMENT_ACCESS_SECRETS_REQUIRED');
/** @param {string} value */
const digest = (value) => createHash('sha256').update(value).digest();
const expected = digest('Basic ' + Buffer.from(`${user}:${password}`).toString('base64'));
createServer((request, response) => {
  if (protectedMode && !timingSafeEqual(digest(request.headers.authorization || ''), expected)) {
    response.writeHead(401, {
      'WWW-Authenticate': 'Basic realm="Sarjy development", charset="UTF-8"',
      'Cache-Control': 'no-store',
    });
    response.end('Authentication required');
    return;
  }
  handler(request, response);
}).listen(Number(process.env.PORT || 3000), process.env.HOST || '0.0.0.0');
