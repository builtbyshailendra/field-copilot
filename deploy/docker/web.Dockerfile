# Web image: Next.js standalone output. API_URL is baked into the rewrite at build time.
FROM node:26-slim AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
ARG API_URL=http://field-copilot-api:8020
ENV API_URL=$API_URL BUILD_STANDALONE=1 NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:26-slim
WORKDIR /app
ENV NODE_ENV=production PORT=3100 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
COPY --from=build /web/.next/standalone ./
COPY --from=build /web/.next/static ./.next/static
# Numeric UID (the node user), so Kubernetes can check runAsNonRoot.
USER 1000:1000
EXPOSE 3100
CMD ["node", "server.js"]
