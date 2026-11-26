FROM node:22-alpine AS base
WORKDIR /app
RUN npm install -g pnpm@10.26.2
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY apps/api/package.json apps/api/
FROM base AS api-build
RUN pnpm install --frozen-lockfile --filter @polyquant/api...
COPY apps/api apps/api
RUN pnpm --filter @polyquant/api build
RUN CI=true pnpm install --offline --prod --frozen-lockfile --filter @polyquant/api...
FROM node:22-alpine AS api
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx
WORKDIR /app
COPY --from=api-build /app/node_modules ./node_modules
COPY --from=api-build /app/apps/api ./apps/api
WORKDIR /app/apps/api
USER node
CMD ["node","dist/server.js"]
FROM base AS web-build
RUN pnpm install --frozen-lockfile --filter @polyquant/web...
COPY apps/web apps/web
RUN pnpm --filter @polyquant/web build
FROM alpine:3.24 AS web
RUN apk upgrade --no-cache && apk add --no-cache "nginx>=1.30.7"
COPY infrastructure/nginx/web.conf /etc/nginx/nginx.conf
COPY --from=web-build /app/apps/web/dist /usr/share/nginx/html
USER nginx
ENTRYPOINT ["nginx"]
CMD ["-g","daemon off;"]
