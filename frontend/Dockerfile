# Node 24 matches CI and the toolchain floor in AGENTS.md (vite 8 / vitest 4
# / TS 6); building on an older major is drift waiting to bite.
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci
COPY . .
RUN npm run build

# nginx-unprivileged runs as uid 101 and binds 8080 — the stock nginx image
# needs root (or CAP_NET_BIND_SERVICE) for port 80. The Service keeps port
# 80 and re-targets 8080, so nothing upstream changes.
FROM nginxinc/nginx-unprivileged:1.31-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
