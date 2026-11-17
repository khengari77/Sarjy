import { createServer } from 'node:http';
// Runtime-generated adapter output is verified by the HTTP boundary test.
const handlerPath = './build/handler.js';
const { handler } = await import(handlerPath);

// The request gate lives in the SvelteKit `handle` hook (signed reviewer
// session cookie). This wrapper only binds the adapter-node handler.
createServer(handler).listen(Number(process.env.PORT || 3000), process.env.HOST || '0.0.0.0');
