-- 0034: the LangGraph checkpointer's tables (Phase 6 chunk A, owner 2026-10-29: the checkpointer is not
-- optional, resume from a checkpoint is an exit criterion).
-- The library creates these itself (`AsyncPostgresSaver.setup()`), at run time, in the connection's default
-- schema. On Supabase that is `public`, which the REST API exposes, and the tables hold a run's state
-- (product, brief, claims, generated copy). So they are created HERE, by a reviewed migration, in a schema
-- the API does not expose, with row level security on and every API role revoked. The backend connects as
-- the owning role (BYPASSRLS), sets its search_path to this schema, and never calls `setup()`:
-- the version rows below make it a no-op, and `assert_checkpoint_schema` (app/orchestrator/checkpoint.py)
-- fails loudly if the installed library expects a newer schema than this migration made.
-- The DDL is langgraph-checkpoint-postgres 3.2.0's MIGRATIONS (versions 0 to 9) in its final form;
-- tests/db/test_graph_checkpoints.py compares it with the library's own, column by column.
-- Indexes are plain CREATE INDEX (the library's are CONCURRENTLY, which cannot run in a migration's
-- transaction); the tables are empty when they are made.
-- Down: drop schema graph_checkpoints cascade;
create schema graph_checkpoints;

create table graph_checkpoints.checkpoint_migrations (
  v integer primary key
);

create table graph_checkpoints.checkpoints (
  thread_id text not null,
  checkpoint_ns text not null default '',
  checkpoint_id text not null,
  parent_checkpoint_id text,
  type text,
  checkpoint jsonb not null,
  metadata jsonb not null default '{}',
  primary key (thread_id, checkpoint_ns, checkpoint_id)
);

create table graph_checkpoints.checkpoint_blobs (
  thread_id text not null,
  checkpoint_ns text not null default '',
  channel text not null,
  version text not null,
  type text not null,
  blob bytea,
  primary key (thread_id, checkpoint_ns, channel, version)
);

create table graph_checkpoints.checkpoint_writes (
  thread_id text not null,
  checkpoint_ns text not null default '',
  checkpoint_id text not null,
  task_id text not null,
  idx integer not null,
  channel text not null,
  type text,
  blob bytea not null,
  task_path text not null default '',
  primary key (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

create index checkpoints_thread_id_idx on graph_checkpoints.checkpoints (thread_id);
create index checkpoint_blobs_thread_id_idx on graph_checkpoints.checkpoint_blobs (thread_id);
create index checkpoint_writes_thread_id_idx on graph_checkpoints.checkpoint_writes (thread_id);

insert into graph_checkpoints.checkpoint_migrations (v) select generate_series(0, 9);

alter table graph_checkpoints.checkpoint_migrations enable row level security;
alter table graph_checkpoints.checkpoints enable row level security;
alter table graph_checkpoints.checkpoint_blobs enable row level security;
alter table graph_checkpoints.checkpoint_writes enable row level security;

revoke all on schema graph_checkpoints from public, anon, authenticated;
revoke all on all tables in schema graph_checkpoints from public, anon, authenticated;
