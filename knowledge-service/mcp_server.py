import os

import httpx
from mcp.server.fastmcp import FastMCP

SEARCH_URL = os.environ["SEARCH_URL"]

mcp = FastMCP("knowledge-service", host="0.0.0.0", port=int(os.environ.get("MCP_PORT", 8001)))


@mcp.tool()
def search_docs(query: str) -> list[dict]:
    """Ищет продуктовые правила, бизнес-логику и договорённости, которых нет
    в коде: жизненный цикл сущностей, политики, договорённости между
    сервисами. Вызывай, когда вопрос про требования и правила, а не про то,
    как что-то реализовано в коде."""
    response = httpx.post(f"{SEARCH_URL}/search", json={"query": query}, timeout=30)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
