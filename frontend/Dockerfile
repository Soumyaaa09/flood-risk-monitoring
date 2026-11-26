# ==============================================================================
# FloodWatch Frontend Production Dockerfile (Multi-Stage Build)
# ==============================================================================
# Stage 1: Build static assets using official Node 20 LTS
# Stage 2: Serve optimized assets via high-performance Nginx Alpine
# Security & Operational Standards:
# - Multi-stage separation prevents developer dependencies in production image
# - No backend service-role credentials or database strings copied
# - Built-in Nginx SPA routing and reverse-proxy to backend API
# - Built-in container healthcheck via /health
# ==============================================================================

# --- Stage 1: Build ---
FROM node:20-alpine AS build

WORKDIR /app

# Install dependencies first for efficient layer caching
COPY package.json package-lock.json ./
RUN npm ci --no-audit --prefer-offline

# Copy source code and build config
COPY index.html ./
COPY postcss.config.js tailwind.config.js vite.config.js ./
COPY src/ ./src/

# Compile production bundle
RUN npm run build

# --- Stage 2: Runtime Web Server ---
FROM nginx:1.27-alpine AS runtime

# Install curl for container healthcheck
RUN apk add --no-cache curl

# Remove default Nginx welcome page
RUN rm -rf /usr/share/nginx/html/*

# Copy compiled assets from build stage
COPY --from=build /app/dist /usr/share/nginx/html

# Copy production Nginx reverse proxy & security configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose HTTP port
EXPOSE 80

# Container healthcheck probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:80/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
