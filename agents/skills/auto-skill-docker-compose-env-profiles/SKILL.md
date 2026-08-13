---
name: docker-compose-env-profiles
description: Configure docker-compose services with environment-specific profiles (dev/testing/prod) using variable substitution, correctly access host services from containers on Linux, and avoid common offline deployment pitfalls.
source: auto-skill
extracted_at: '2026-06-28T05:14:10.596Z'
---

# Docker Compose Environment-Specific Configuration

## When to Use

When adding a new service to `docker-compose.yml` that needs different configuration per environment (dev, testing, prod), especially for third-party images where you control env vars but not the image itself.

## Key Pitfall: `localhost` Inside Containers

**`localhost` inside a Docker container refers to the container itself, NOT the host machine.**

This is the #1 mistake when configuring container-to-host communication:

```
┌─────────────────────────────────────────────────┐
│  Host Machine (192.168.x.x)                     │
│                                                 │
│  ┌──────────────────────────────┐               │
│  │  Docker Container            │               │
│  │                              │               │
│  │  localhost = container self  │  ← NOT host!  │
│  │  127.0.0.1 = container self  │               │
│  │                              │               │
│  │  host.docker.internal = host │  ← Use this!  │
│  └──────────────────────────────┘               │
│                                                 │
│  PostgreSQL :7432  (on host network)            │
└─────────────────────────────────────────────────┘
```

### Solution: `host.docker.internal` + `extra_hosts`

On **macOS/Windows** (Docker Desktop): `host.docker.internal` works out of the box.

On **Linux**: You MUST add `extra_hosts` to the service:

```yaml
services:
  my_service:
    image: some-image
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - DATABASE_URL=postgres://user:pass@host.docker.internal:5432/db
```

Without `extra_hosts`, `host.docker.internal` will not resolve on Linux and the container will fail to connect.

## Environment-Specific Configuration Pattern

### 1. Define variables in per-profile `.env` files

```
# .env.dev
TILESERV_DATABASE_URL=postgres://user:pass@host.docker.internal:7432/mydb
TILESERV_BASE_URL=http://localhost:7800

# .env.prod
TILESERV_DATABASE_URL=postgres://user:pass@10.0.0.5:7432/mydb
TILESERV_BASE_URL=http://10.0.0.21:7800
```

**Note**: For database connections from containers, use `host.docker.internal` (not `localhost`) even in dev/testing profiles. For the public-facing URL (`TS_BASE_URL`), `localhost` is correct because external clients on the same machine access it via localhost.

### 2. Use `${VAR}` substitution in `docker-compose.yml`

```yaml
services:
  my_service:
    image: some-image
    environment:
      - DATABASE_URL=${TILESERV_DATABASE_URL}
      - PUBLIC_URL=${TILESERV_BASE_URL}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "7800:7800"
```

### 3. Run with the correct profile

```bash
# Dev
docker compose --env-file .env.dev up -d my_service

# Prod
docker compose --env-file .env.prod up -d my_service
```

### 4. Validate before deploying

```bash
# Check resolved config
docker compose --env-file .env.dev config | grep -A 10 my_service
docker compose --env-file .env.prod config | grep -A 10 my_service
```

## Two `.env` Files: Know the Difference

This project has TWO separate `.env` files with different purposes:

```
项目根目录 .env                    deploy/config/.env
─────────────────                  ──────────────────
用途: docker-compose 变量替换       用途: 容器内环境变量注入
      ${VAR} 占位符解析                    env_file: ./deploy/config/.env

谁读取: docker compose 命令          谁读取: api / worker 容器进程
      在启动前解析                           运行时作为 ENV 可用
```

**Rule**: `${VAR}` substitution in `docker-compose.yml` reads from the **root `.env`** (or shell `export`). The `env_file:` directive reads from `deploy/config/.env`. They are NOT interchangeable.

### Offline Deployment Consideration

In offline deployments, the root `.env` may not exist (dev profile files aren't bundled). The deploy script must **create** it:

```bash
# In deploy_offline.sh — write TILESERV_* to root .env for docker-compose
python3 -c "
root_env = '.env'
# Create or update root .env with TILESERV variables
# These are resolved by docker compose BEFORE container creation
"
```

If you write `${VAR}` values to `deploy/config/.env` instead, docker-compose will resolve them as empty strings.

## Offline Deployment Pitfalls

### `pull_policy: build` Breaks Offline Deploy

If a service has `pull_policy: build`, docker-compose will **always** try to build from Dockerfile, even if the image is already loaded locally. In offline deployments where the Dockerfile isn't present, this fails with:

```
target worker: failed to solve: failed to read dockerfile: open Dockerfile: no such file or directory
```

**Fix**: Use `pull_policy: missing` instead. This uses the pre-loaded image if it exists, and only builds if the image is absent.

```yaml
services:
  api:
    image: datacrawler:latest
    build:
      context: .
      dockerfile: Dockerfile
    pull_policy: missing  # NOT 'build' — that breaks offline deploy
```

### `npx` Without Version Pinning Downloads Latest

When using `npx <package>` without a version specifier, npx downloads `@latest` from npm — even if the Docker image bundles a specific version of the tool's binaries.

```
mcr.microsoft.com/playwright:v1.58.0-noble
  → Contains browser binaries v1.58.0
  → Does NOT contain the playwright npm package

npx playwright run-server
  → Downloads playwright@1.61.1 (latest!) from npm
  → Version mismatch: binaries 1.58.0 + npm 1.61.1
```

**Fix**: Pin the version in the command:

```yaml
command: npx playwright@1.58.0 run-server --port 3010 --host 0.0.0.0
```

### Build Script: Check Local Images Before Pull/Build

For offline bundle builds, check if images already exist locally before pulling/building to avoid unnecessary network access and time:

```bash
if "$CONTAINER_ENGINE" image inspect "pramsey/pg_tileserv:${TILESERV_VER}" >/dev/null 2>&1; then
    echo "[SKIP] Local image exists, skipping pull"
else
    "$CONTAINER_ENGINE" pull "pramsey/pg_tileserv:${TILESERV_VER}"
fi
```

The main application image (`datacrawler:latest`) should always rebuild since it contains latest code. Third-party images (pg_tileserv, playwright) can safely skip if the version-tagged image exists locally.

## Debugging Connection Failures

When a container fails to connect to a host service:

1. **Check TCP reachability from host**: `nc -zv <db_host> <port>`
2. **Check from inside container**: `docker exec <container> cat /etc/hosts` — verify `host.docker.internal` resolves
3. **Check logs**: `docker logs <container> --tail 20` — look for connection errors
4. **Common errors**:
   - `connection refused` on `[::1]` → using `localhost` instead of `host.docker.internal`
   - `unexpected EOF` → TCP connects but protocol fails (SSL mismatch, pg_hba.conf, or proxy interference)
   - `no such host` → missing `extra_hosts` on Linux
   - `database "X" does not exist` → database name in URL doesn't match actual DB on server
