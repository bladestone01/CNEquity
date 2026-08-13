---
name: multi-project-changes
description: Coordinate OpenSpec changes across multiple projects in a call chain (e.g. frontend → middleware → backend), splitting responsibilities per layer with correct dependency ordering.
source: auto-skill
extracted_at: '2026-06-26T13:51:05.618Z'
---

# Multi-Project Coordinated Changes

## When to Use

When a feature or change spans **multiple projects** that form a call chain — e.g. a frontend calling a middleware that calls a backend. Each project needs its own OpenSpec change, but the changes must be coordinated so that:

1. Each layer's responsibilities are correctly scoped
2. API contracts between layers are consistent
3. Execution order follows the dependency chain

**Don't use when:**
- The change is confined to a single project
- Projects are independent (no call chain between them)

## The Pattern

### 1. Map the Call Chain

Before creating any changes, understand the full request flow:

```
┌──────────┐      ┌──────────────────┐      ┌──────────────┐
│ Frontend │ ───▶ │ Middleware/Proxy  │ ───▶ │ Backend/API  │
│ (webui)  │      │ (collector_server)│      │(datacrawler) │
└──────────┘      └──────────────────┘      └──────────────┘
```

For each layer, determine:
- **What it currently does** for the relevant feature area
- **What new endpoints/methods it must expose or call**
- **What data model changes it needs**

### 2. Explore Each Project

Use Explore agents to investigate each project in the chain. For each project, understand:

- Existing API endpoints related to the feature
- Data models / entities / schemas
- Service layer patterns (how it calls the next layer)
- Current gaps or bugs that the change must address

**Key questions per layer:**
- Frontend: What UI components exist? How does it call the middleware? What polling/real-time patterns are already used?
- Middleware: Does it proxy HTTP or use message queues? What entities does it own? What auth/permission model?
- Backend: What business logic lives here? What DB tables? What external services does it call?

### 3. Split Requirements Per Layer

Create a responsibility matrix:

```
┌──────────────────────┬────────────┬──────────────┬──────────────┐
│ Requirement          │ Frontend   │ Middleware    │ Backend      │
├──────────────────────┼────────────┼──────────────┼──────────────┤
│ New feature X        │ UI + API   │ Proxy endpoint│ Core logic   │
│ New field Y          │ Display    │ Entity+mapper │ DB + model   │
│ New status Z         │ Tag/label  │ Pass-through  │ Enum + logic │
│ Delete with cascade  │ Confirm UI │ Proxy delete  │ Cascade SQL  │
└──────────────────────┴────────────┴──────────────┴──────────────┘
```

### 4. Create One OpenSpec Change Per Project

For each project, create a change with `proposal.md`, `design.md`, and `tasks.md`. Each change should:

- **Reference the call chain** — make clear which upstream/downstream changes it depends on
- **Scope to that layer only** — don't include tasks for other projects
- **Define API contracts explicitly** — the backend change defines the API, the middleware change proxies it, the frontend change calls it

**Naming convention:** Use related but distinct names per project:
- Backend: `ncbi-pipeline-a-task-control`
- Middleware: `ncbi-task-control-proxy`
- Frontend: `ncbi-task-control-ui`

### 5. Determine Execution Order

```
Execution order follows the dependency chain:

  1. Backend first    — provides the API capabilities
  2. Middleware next   — proxies to backend APIs
  3. Frontend last     — calls middleware APIs
```

Always start from the layer closest to the data/source and work outward.

### 6. Cross-Project Consistency Checks

Before starting implementation, verify:

- [ ] API endpoint paths match across all 3 changes (backend defines, middleware proxies, frontend calls)
- [ ] Request/response schemas are consistent
- [ ] New status codes / enum values are handled in all layers
- [ ] New DB fields are reflected in middleware entities/mappers
- [ ] Permission/auth patterns are consistent

### 7. Implementation Phase

After creating all 3 changes, implement in dependency order (backend → middleware → frontend). Each layer follows a predictable pattern:

**Backend implementation order:**
1. Enums/status codes
2. Data models + migrations
3. DAO layer (queries, aggregations, cascade operations)
4. Service layer (business logic, cancel/retry/delete)
5. Router/controller (API endpoints)
6. Schemas (request/response models)
7. Tests

**Middleware implementation order:**
1. Entity/domain objects + mapper XML
2. Service interface + implementation (proxy methods)
3. Controller (proxy endpoints)
4. Config (URL templates if needed)
5. Permission annotations
6. Compile verification

**Frontend implementation order:**
1. API layer (new methods)
2. Main page overhaul (progress, controls, polling)
3. New pages (sub-task views, detail views)
4. Detail dialogs
5. Stats cards
6. Styles
7. Build verification

**Key insight:** Each layer's implementation is largely mechanical once the API contracts are defined. The backend does the real work; middleware proxies; frontend displays and controls.

### 8. Common Implementation Patterns

When implementing task control features, these patterns recur:

**Cancel with Redis flag:**
```python
# Set flag
redis.set(f"cancel:task:{task_id}", "1", ex=86400)

# Check in loop
if redis.get(f"cancel:task:{task_id}"):
    break
```

**Retry with state rollback:**
```python
# Reset failed sub-tasks to queued
UPDATE sub_tasks SET status = QUEUED, retry_count += 1
WHERE task_id = :id AND status IN (FAILED, ERROR)

# Reset parent task
task.status = PROCESSING
```

**Progress aggregation:**
```sql
SELECT
    COUNT(*) FILTER (WHERE status = COMPLETED) as completed,
    COUNT(*) FILTER (WHERE status = FAILED) as failed,
    ...
FROM sub_tasks WHERE task_id = :task_id
```

**Frontend polling with cleanup:**
```javascript
let pollInterval = setInterval(fetchProgress, 5000)
onUnmounted(() => clearInterval(pollInterval))
```

See the `async-pipeline-task-control` skill for detailed patterns and pitfalls.

## Anti-Patterns

**❌ Creating one mega-change** that spans all projects — each project has its own build system, tests, and deployment

**❌ Ignoring the middleware layer** — assuming frontend calls backend directly when there's a proxy in between

**❌ Inconsistent API contracts** — backend defines `/tasks/{id}/cancel` but middleware proxies `/task/{id}/cancel`

**❌ Wrong execution order** — starting with frontend when backend doesn't have the endpoints yet

## Real Example

In this project, the call chain is:
- **webui** (Vue 3 + Element Plus) → **collector_server** (Java Spring Boot) → **datacrawler** (Python FastAPI)

The middleware has two patterns:
1. HTTP proxy for crawl/search (forwards to datacrawler via `HttpUtils.sendPostJson`)
2. Celery message queue for analysis (pushes to Redis, datacrawler worker picks up)

Understanding which pattern applies to each new endpoint was critical for correctly scoping each change.
