# Public frontend image (SvelteKit + adapter-node), built from the frontend/ context:
#   docker build -t datacommune-frontend:<sha> \
#     --build-arg VITE_API_URL=https://api.datacommune.fr \
#     --build-arg PUBLIC_DC_API_KEY=dc_anon_... \
#     --build-arg VITE_DEPLOYMENT_SHA=<sha> frontend
#
# VITE_API_URL and PUBLIC_DC_API_KEY are inlined into the JS bundle at build time
# (src/lib/config.ts): rotating the public key means rebuilding the image. The anon
# key is public by design (dc_anon_ prefix, quota-capped server-side), so a build
# ARG is acceptable; a dc_live_ key is refused by the validator below.

# node:22-slim, digest read on 2026-10-12. Re-pin deliberately on upgrade.
ARG NODE_IMAGE=node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392

# --- Stage 1: build ---
FROM ${NODE_IMAGE} AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG VITE_API_URL=https://api.datacommune.fr
ARG PUBLIC_DC_API_KEY
ARG VITE_DEPLOYMENT_ENV=production
ARG VITE_DEPLOYMENT_SHA=unknown
# Accounts are off on the public instance until they are taken over (phase 5):
# login, register, billing and admin routes answer 404 (src/hooks.ts reroute).
ARG VITE_ACCOUNTS_ENABLED=false
# Paid plans (/billing): off; accounts can be on without them (D-133 amended 2026-11-12).
ARG VITE_BILLING_ENABLED=false
# Audience measurement (04.7-14b): on only where Caddy routes /count to GoatCounter.
ARG VITE_MESURE_AUDIENCE=false
# Error report (page /signaler): off unless the public compose file opens it.
ARG VITE_SIGNALEMENT=false
ENV VITE_API_URL=${VITE_API_URL} \
    PUBLIC_DC_API_KEY=${PUBLIC_DC_API_KEY} \
    VITE_DEPLOYMENT_ENV=${VITE_DEPLOYMENT_ENV} \
    VITE_DEPLOYMENT_SHA=${VITE_DEPLOYMENT_SHA} \
    VITE_ACCOUNTS_ENABLED=${VITE_ACCOUNTS_ENABLED} \
    VITE_BILLING_ENABLED=${VITE_BILLING_ENABLED} \
    VITE_MESURE_AUDIENCE=${VITE_MESURE_AUDIENCE} \
    VITE_SIGNALEMENT=${VITE_SIGNALEMENT}

# Fail closed on a missing or non-dc_anon_ key, build, then scan the bundle.
RUN node scripts/validate-public-api-key.mjs \
 && npm run build \
 && node scripts/scan-public-bundle.mjs build

# --- Stage 2: runtime ---
FROM ${NODE_IMAGE}
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3000
WORKDIR /app

# adapter-node output is self-contained: the app has no runtime `dependencies`.
COPY --from=build --chown=node:node /app/build ./build
COPY --from=build --chown=node:node /app/package.json ./package.json
# server.js wraps the adapter-node handler so static and prerendered answers get
# the S4 headers too (KI-P3); it reads the canonical list from its source file.
COPY --from=build --chown=node:node /app/server.js ./server.js
COPY --from=build --chown=node:node /app/src/lib/utils/security-headers.js ./src/lib/utils/security-headers.js

USER node
EXPOSE 3000

# /__version is a server route (no API call, no auth): it proves the Node process serves.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/__version').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["node", "server.js"]
