# syntax=docker/dockerfile:1
# Frontend image: Vite build, then static files served by unprivileged nginx.

FROM node:22-alpine AS build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# The API base URL is public (it is visible in the browser) and baked at build
# time. Never pass secrets as build args.
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_PUBLIC_DEMO=false
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_PUBLIC_DEMO=${VITE_PUBLIC_DEMO}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=5 \
  CMD wget -qO /dev/null http://127.0.0.1:8080/healthz || exit 1
