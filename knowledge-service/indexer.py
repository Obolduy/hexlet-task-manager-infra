"""Файлы в sources/: frontmatter (title, source_url, owner, updated_at)
между строками "---", затем markdown с разделами по ## — каждый раздел
становится чанком."""
import glob
import os
import re

import psycopg2
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

DATABASE_URL = os.environ["DATABASE_URL"]
MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")

model = SentenceTransformer(MODEL_NAME)


def parse_document(path):
    text = open(path, encoding="utf-8").read()
    _, frontmatter, body = text.split("---", 2)
    meta = dict(line.split(":", 1) for line in frontmatter.strip().splitlines())
    meta = {key.strip(): value.strip() for key, value in meta.items()}

    sections = re.split(r"^##\s+(.+)$", body, flags=re.MULTILINE)
    chunks = list(zip(sections[1::2], sections[2::2]))

    return meta, body.strip(), chunks


def reindex():
    conn = psycopg2.connect(DATABASE_URL)
    register_vector(conn)
    with conn, conn.cursor() as cur:
        cur.execute("truncate chunks, documents restart identity cascade")

        for path in sorted(glob.glob("sources/*.md")):
            meta, body, chunks = parse_document(path)

            cur.execute(
                """
                insert into documents (title, body, source_url, owner, updated_at)
                values (%s, %s, %s, %s, %s) returning id
                """,
                (meta["title"], body, meta.get("source_url"), meta.get("owner"), meta["updated_at"]),
            )
            document_id = cur.fetchone()[0]

            for heading, content in chunks:
                heading, content = heading.strip(), content.strip()
                embedding = model.encode(f"passage: {heading}\n{content}", normalize_embeddings=True)
                cur.execute(
                    """
                    insert into chunks (document_id, heading, content, embedding)
                    values (%s, %s, %s, %s)
                    """,
                    (document_id, heading, content, embedding),
                )
            print(f"{path}: {len(chunks)} chunks")

    conn.close()


if __name__ == "__main__":
    reindex()
