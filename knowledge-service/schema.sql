-- Референс для эфира. В docker-compose не подключается — схема уже
-- накатана на Supabase, где реально живут данные.

create extension if not exists vector;

create table documents (
  id           bigserial primary key,
  title        text not null,
  body         text not null,
  source_url   text,
  owner        text,
  updated_at   date not null
);

create table chunks (
  id           bigserial primary key,
  document_id  bigint not null references documents(id) on delete cascade,
  heading      text,
  content      text not null,
  embedding    vector(384)           -- должна совпадать с EMBEDDING_MODEL в .env
);

create index on chunks using hnsw (embedding vector_cosine_ops);
