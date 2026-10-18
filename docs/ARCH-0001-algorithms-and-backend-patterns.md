# Algorithms and Backend Patterns

This document evaluates the algorithm and backend patterns against the current
F0 architecture. The goal is to apply each pattern where it protects a real
product boundary, not to add unrelated interview exercises to production code.

## Implemented in the current scaffold

| Pattern | Current implementation | Complexity |
| --- | --- | --- |
| Hash maps and sets | Python dictionaries for route lookup, payload grouping, event payloads, and SQL uniqueness constraints | Average O(1) lookup/insert; O(n) space |
| Idempotency | PostgreSQL unique keys and `processed_event`; Redis enqueue marks | O(1) average key lookup; O(1) extra state per event |
| LRU cache | `functools.lru_cache(maxsize=1)` for process settings | O(1) average lookup; O(1) space here |
| Sliding window | Redis sorted-set request limiter at the gateway | O(log n + r) per request, where `r` is expired entries removed; O(n) active timestamps per identity |
| Retry with backoff and jitter | Shared async helper for idempotent gateway methods | O(a) operation attempts plus bounded wait; O(1) space |
| Keyset pagination | Opaque `(updated_at, id)` cursors for Core companies and leads | O(log n + page size) per page with the composite ordering index; O(1) cursor space |
| TTL key-value state | Redis `EX`, `PX`, and `SET NX` for enqueue marks and score locks | O(1) average key operation; bounded by TTL and key count |
| Top-k query | Territory opportunity uses SQL `ORDER BY ... LIMIT 200` | O(log n + k) with the `iop` index; bounded output space |

The gateway owns synchronous request coordination. Core owns transactional
state and publishes through the outbox. Redis Streams coordinate asynchronous
consumers, while shared contracts keep event shapes stable. Consumers and the
outbox relay use the same bounded retry helper with exponential backoff and
jitter before leaving work pending for a later pass.

## Pattern mapping by product capability

### Hash maps and sets

These are already the right primitives for routing, deduplication, event
processing, and entity candidate keys. Company and signal uniqueness belongs in
Postgres because it must remain correct across processes; Python sets should be
used only for local batch work.

### Two pointers and sliding windows

Two pointers are useful for sorted time-series comparisons, such as comparing
two snapshots or finding overlapping availability. They are not needed by the
current F0 APIs. The sliding-window pattern is now used where it matters most:
the gateway rate limiter.

### Sorting and binary search

Database indexes and `ORDER BY ... LIMIT` should handle production queries. The
territory opportunity query has an `iop` index so its top-k read can avoid a
full application-side sort. A
Python `bisect` implementation is appropriate for small immutable lookup tables
loaded by a worker. Binary search over an answer is a future option for project
capacity or budget feasibility once those constraints become explicit.

### Stacks and queues

Redis Streams are the production queue abstraction. A stack is not currently a
domain concept. Parenthesis validation is a parser concern and does not belong
in the service layer.

### Heaps

Top-k lead ranking can use `heapq.nlargest` for bounded in-memory batches, but
the current territory ranking is better executed in Postgres. Merging k sorted
lists only becomes relevant if analytics reads multiple partitioned sources.

### Intervals and prefix sums

Project schedules, meeting availability, and source cadence windows can use
interval overlap checks. PostgreSQL range types and exclusion constraints are
preferable once scheduling is implemented. Daily analytics rollups are the
database equivalent of prefix aggregation; application code should not rebuild
those sums row by row.

### Trees, graphs, and topological sorting

n8n already represents external workflows as DAGs. Future project dependency
planning can use adjacency lists plus topological sorting, with cycle detection
before a project is approved. DFS and BFS are not currently required for the
company, lead, or territory data model.

### Dijkstra, backtracking, and dynamic programming

These patterns are not part of the current business domain. Dijkstra would only
be justified by territory routing or travel-time optimization. Backtracking is
appropriate for constrained project configuration, and dynamic programming for
forecast or allocation optimization, but both should wait for a defined
objective function and data contract.

## Backend-specific decisions

### LRU and TTL caches

Process settings already use an LRU cache. Request throttling and score locks
use Redis because the gateway and workers may run in multiple processes. A
local LRU or in-memory TTL cache may be added for read-through acceleration, but
it must never become the source of truth for score freshness, idempotency, or
authorization.

### Rate limiting

The gateway uses a Redis sorted-set sliding-window log. Lua makes cleanup,
counting, and insertion atomic. The default is 60 requests per identity per 60
seconds. The gateway uses the socket peer address unless
`TRUST_PROXY_HEADERS=true` explicitly enables the first trusted
`X-Forwarded-For` address. `RATE_LIMIT_FAIL_OPEN=true` preserves availability
during a Redis outage in local/F0 mode; production can set it to `false` when
abuse protection is more important than fail-open availability.

### Retry policy

Only idempotent gateway methods are retried automatically. POST requests are
sent once because blindly retrying them can duplicate writes. Core writes rely
on idempotency keys and the outbox for safe redelivery. Every future connector
should define its retryable errors, attempt limit, timeout, and idempotency
strategy before using the helper.

### Cursor pagination

Company and lead reads use `(updated_at, id)` keyset ordering and return an
opaque `next_cursor`. This avoids the increasing scan cost of offset pagination
and remains stable when new rows arrive between requests. Clients must treat the
cursor as opaque and request the next page until it is `null`.

### Consistent hashing

It is not implemented because F0 has one Redis deployment and no horizontally
sharded worker pool. If the event volume requires sharding, consistent hashing
can assign company keys to partitions while preserving per-company ordering;
the partition key and rebalance strategy must be added to the event contract.

## Guardrails

`make static` validates migration safety, English-only repository text, module
docstrings, syntax, Ruff, and mypy. Performance-sensitive additions should
include measured bounds, an idempotency decision, and an explicit owner for
state before they are connected to a production path.
