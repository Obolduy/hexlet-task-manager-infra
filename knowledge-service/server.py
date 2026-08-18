import os

import psycopg2
from fastapi import FastAPI
from pgvector.psycopg2 import register_vector
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

DATABASE_URL = os.environ["DATABASE_URL"]
MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")

model = SentenceTransformer(MODEL_NAME)
app = FastAPI()

SQL = """
    select c.content, c.heading, d.title, d.source_url, d.owner, d.updated_at,
           1 - (c.embedding <=> %s) as score
    from chunks c
    join documents d on d.id = c.document_id
    order by c.embedding <=> %s
    limit 5
"""


class SearchRequest(BaseModel):
    query: str


class SearchResult(BaseModel):
    content: str
    heading: str | None
    document_title: str
    source_url: str | None
    owner: str | None
    updated_at: str
    score: float


@app.post("/search", response_model=list[SearchResult])
def search(request: SearchRequest) -> list[SearchResult]:
    embedding = model.encode(f"query: {request.query}", normalize_embeddings=True)

    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        # соединение идёт через PgBouncer (transaction pooling) на пути к
        # Supabase; search_path пришедшего соединения не гарантирован, а
        # расширение vector у Supabase живёт в схеме extensions, не public —
        # без явного SET register_vector() иногда не находит тип
        cur.execute("set search_path to public, extensions")
    register_vector(conn)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL, (embedding, embedding))
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        SearchResult(
            content=content,
            heading=heading,
            document_title=title,
            source_url=source_url,
            owner=owner,
            updated_at=str(updated_at),
            score=float(score),
        )
        for content, heading, title, source_url, owner, updated_at, score in rows
    ]
