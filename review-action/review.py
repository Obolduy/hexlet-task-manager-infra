import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_ITERATIONS = 5
DIFF_CHAR_LIMIT = 50_000
MAX_READ_FILE_BYTES = 20_000
LOG_TRUNCATE = 200

SEVERITY_ORDER = ["critical", "warning", "info"]
SEVERITY_TITLES = {"critical": "Критично", "warning": "Важно", "info": "На заметку"}


def log(message: str) -> None:
    print(message, flush=True)


def truncate(text: str, limit: int = LOG_TRUNCATE) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


# --- сбор контекста ---------------------------------------------------

def get_diff(repo_root: Path, base_sha: str, head_sha: str) -> str:
    result = subprocess.run(
        ["git", "diff", f"{base_sha}...{head_sha}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    diff = result.stdout
    if len(diff) > DIFF_CHAR_LIMIT:
        diff = diff[:DIFF_CHAR_LIMIT] + "\n... (диф обрезан по объёму)"
    return diff


def read_agents_md(repo_root: Path) -> str:
    path = repo_root / "AGENTS.md"
    if not path.is_file():
        log("AGENTS.md не найден в корне репозитория, продолжаем без него")
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def read_specs(repo_root: Path) -> str:
    specs_dir = repo_root / "specs"
    if not specs_dir.is_dir():
        log("папка specs/ отсутствует, работаем без неё")
        return ""
    parts = []
    for path in sorted(specs_dir.glob("*.md")):
        parts.append(f"### {path.name}\n\n{path.read_text(encoding='utf-8', errors='replace')}")
    if not parts:
        log("папка specs/ пуста")
        return ""
    return "\n\n".join(parts)


def load_prompt() -> str:
    return (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")


def build_user_message(diff: str, agents_md: str, specs_text: str, tools_enabled: bool) -> str:
    sections = [
        "## Diff (BASE...HEAD)",
        ("```diff\n" + diff + "\n```") if diff else "(пусто)",
        "## AGENTS.md (корень репозитория)",
        agents_md if agents_md else "(файл отсутствует)",
        "## Спецификации из specs/",
        specs_text if specs_text else "(папка specs/ отсутствует или пуста)",
    ]
    if not tools_enabled:
        sections.append(
            "## Режим\n\nИнструменты read_file и search_docs недоступны в этом "
            "запуске. Используй только текст выше и сразу верни финальный JSON."
        )
    return "\n\n".join(sections)


# --- инструменты (только чтение) ---------------------------------------

def safe_read_file(repo_root: Path, raw_path: object) -> str:
    if not raw_path or not isinstance(raw_path, str):
        return "Ошибка: путь не указан"
    if raw_path.startswith("/") or raw_path.startswith("~"):
        return "Ошибка: абсолютные пути запрещены"
    parts = Path(raw_path).parts
    if ".." in parts:
        return "Ошибка: выход за пределы репозитория запрещён"
    if any(part.startswith(".") for part in parts):
        return "Ошибка: скрытые файлы и .env запрещены"

    root = repo_root.resolve()
    candidate = (root / raw_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return "Ошибка: путь вне клона репозитория"

    if not candidate.is_file():
        return f"Ошибка: файл не найден: {raw_path}"

    data = candidate.read_bytes()
    truncated = len(data) > MAX_READ_FILE_BYTES
    text = data[:MAX_READ_FILE_BYTES].decode("utf-8", errors="replace")
    return text + "\n...(обрезано по размеру)" if truncated else text


def safe_search_docs(search_url: str, query: object) -> str:
    if not search_url:
        return "Ошибка: search_docs недоступен, SEARCH_URL не настроен"
    if not query or not isinstance(query, str):
        return "Ошибка: пустой запрос"
    try:
        response = requests.post(
            f"{search_url.rstrip('/')}/search", json={"query": query}, timeout=10
        )
        response.raise_for_status()
        results = response.json()[:3]
    except Exception as exc:  # сеть, таймаут, не-JSON-ответ и т.п.
        return f"Ошибка: search_docs недоступен ({exc})"
    if not results:
        return "Ничего не найдено"
    return json.dumps(results, ensure_ascii=False)


def build_tools(search_enabled: bool) -> list[dict]:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Читает файл из клона репозитория по относительному пути. "
                    "Только чтение, доступ ограничен клоном репозитория."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Путь относительно корня репозитория, например apps/api/src/routes/tasks.py",
                        }
                    },
                    "required": ["path"],
                },
            },
        }
    ]
    if search_enabled:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "search_docs",
                    "description": (
                        "Ищет продуктовые правила, бизнес-логику и договорённости, "
                        "которых нет в коде: жизненный цикл сущностей, политики, "
                        "договорённости между сервисами. Вызывай, когда вопрос про "
                        "требования и правила, а не про то, как что-то реализовано в коде."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Вопрос на естественном языке",
                            }
                        },
                        "required": ["query"],
                    },
                },
            }
        )
    return tools


def run_tool_call(call: dict, ctx: dict) -> str:
    name = call["function"]["name"]
    raw_args = call["function"].get("arguments") or "{}"
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError:
        args = {}

    if name == "read_file":
        result = safe_read_file(ctx["repo_root"], args.get("path"))
    elif name == "search_docs":
        result = safe_search_docs(ctx["search_url"], args.get("query"))
    else:
        result = f"Ошибка: неизвестный инструмент {name}"

    log(f"  -> {name}({args}) => {truncate(result)}")
    return result


# --- вызов модели --------------------------------------------------------

def call_openrouter(messages: list, tools: list | None, api_key: str, model: str) -> dict:
    payload = {"model": model, "messages": messages, "temperature": 0.2}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90)
    response.raise_for_status()
    return response.json()


def parse_findings(text: str) -> list[dict]:
    if not text:
        return []
    text = text.strip()

    candidates = [text]
    fence_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fence_match:
        candidates.insert(0, fence_match.group(1))
    bracket_match = re.search(r"\[.*\]", text, re.DOTALL)
    if bracket_match:
        candidates.append(bracket_match.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict) and item.get("file") and item.get("comment")]

    log("не удалось разобрать JSON в ответе модели, находки не собраны")
    return []


# --- агентный цикл ---------------------------------------------------------

def run_review() -> list[dict]:
    repo_root = Path(os.environ.get("REPO_PATH", os.getcwd())).resolve()
    api_key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ["OPENROUTER_MODEL"]
    search_url = os.environ.get("SEARCH_URL", "").strip()
    base_sha = os.environ["BASE_SHA"]
    head_sha = os.environ["HEAD_SHA"]
    single_shot = os.environ.get("SINGLE_SHOT") == "1"

    log(f"репозиторий: {repo_root}")
    log(f"диапазон диффа: {base_sha}...{head_sha}")
    log(f"single-shot: {single_shot}, search_docs доступен: {bool(search_url) and not single_shot}")

    diff = get_diff(repo_root, base_sha, head_sha)
    agents_md = read_agents_md(repo_root)
    specs_text = read_specs(repo_root)

    tools_enabled = not single_shot
    messages = [
        {"role": "system", "content": load_prompt()},
        {"role": "user", "content": build_user_message(diff, agents_md, specs_text, tools_enabled)},
    ]

    if single_shot:
        log("режим SINGLE_SHOT: один запрос без инструментов")
        response = call_openrouter(messages, None, api_key, model)
        final_content = response["choices"][0]["message"].get("content") or ""
        return parse_findings(final_content)

    tools = build_tools(search_enabled=bool(search_url))
    ctx = {"repo_root": repo_root, "search_url": search_url}
    final_content = ""

    for iteration in range(1, MAX_ITERATIONS + 1):
        log(f"итерация {iteration}/{MAX_ITERATIONS}: запрос к модели")
        response = call_openrouter(messages, tools, api_key, model)
        message = response["choices"][0]["message"]
        messages.append(message)
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            final_content = message.get("content") or ""
            log("модель завершила цикл без вызова инструментов")
            break

        for call in tool_calls:
            result = run_tool_call(call, ctx)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    else:
        log("бюджет итераций исчерпан, форсируем финальный ответ без инструментов")
        messages.append(
            {
                "role": "user",
                "content": (
                    "Бюджет вызовов инструментов исчерпан. Верни финальный JSON "
                    "с тем, что уже удалось собрать, без вызова инструментов."
                ),
            }
        )
        response = call_openrouter(messages, None, api_key, model)
        final_content = response["choices"][0]["message"].get("content") or ""

    return parse_findings(final_content)


# --- вывод и постинг в PR ---------------------------------------------------

def build_comment(findings: list[dict] | None) -> str:
    header = "## Автоматическое ревью (ИИ-агент)"

    if findings is None:
        return (
            f"{header}\n\n"
            "Ревьюер не смог завершить проверку из-за внутренней ошибки. "
            "Подробности — в логе джобы `AI review`."
        )

    if not findings:
        return f"{header}\n\nЗамечаний нет."

    grouped: dict[str, list[dict]] = {}
    for item in findings:
        severity = str(item.get("severity") or "info").lower()
        grouped.setdefault(severity, []).append(item)

    order = SEVERITY_ORDER + sorted(k for k in grouped if k not in SEVERITY_ORDER)
    lines = [header, ""]
    for severity in order:
        items = grouped.get(severity)
        if not items:
            continue
        lines.append(f"### {SEVERITY_TITLES.get(severity, severity.capitalize())}")
        for item in items:
            location = str(item["file"])
            if item.get("line"):
                location += f":{item['line']}"
            comment = str(item["comment"]).strip()
            rule = item.get("rule")
            suffix = f" _(правило: {rule})_" if rule else ""
            lines.append(f"- `{location}` — {comment}{suffix}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def post_comment(body: str) -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]
    token = os.environ["GITHUB_TOKEN"]
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    response = requests.post(url, headers=headers, json={"body": body}, timeout=30)
    response.raise_for_status()
    log(f"комментарий опубликован в PR #{pr_number}")


def main() -> None:
    findings = None
    try:
        findings = run_review()
        log(f"собрано находок: {len(findings)}")
    except Exception:
        log("ОШИБКА в ходе ревью:")
        log(traceback.format_exc())

    comment = build_comment(findings)
    try:
        post_comment(comment)
    except Exception:
        log("ОШИБКА при публикации комментария:")
        log(traceback.format_exc())

    sys.exit(0)


if __name__ == "__main__":
    main()
