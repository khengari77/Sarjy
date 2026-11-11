FROM node:22-slim AS build
WORKDIR /app/apps/web
RUN npm install -g pnpm@10.32.1
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web ./
COPY packages/contracts /app/packages/contracts
RUN pnpm build
FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000
COPY --from=build /app/apps/web/build ./build
COPY --from=build /app/apps/web/node_modules ./node_modules
COPY --from=build /app/apps/web/package.json ./
COPY apps/web/server.js ./server.js
EXPOSE 3000
CMD ["node", "server.js"]
