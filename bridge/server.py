import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
os.chdir(SCRIPT_DIR)

import json
import shutil
import asyncio
import subprocess
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pdf_extractor import PDFExtractor

DEFAULT_PROMPT = """### **Role: 专业学术论文翻译专家**

### Task:
请将给定的英文学术论文内容翻译成中文。译文需满足出版级标准，确保术语准确、行文流畅、逻辑严密，并严格遵守中文学术写作规范。

### **Constraints & Guidelines**:
1. 表格内容处理策略(核心要求):
请依据表格的具体内容属性采用不同的处理方式:
- 定量结果类表格: 对于展示回归分析结果、描述性统计、相关性矩阵或模型参数的表格，由于包含大量数字、显著性星号及标准变量名，请保持原文不进行翻译，以确保数据的原始精确度。
- 定性描述类表格: 对于包含大量文本描述的表格，例如变量定义表、文献综述表、机制解释表或假设阐述表，必须将表内的文本内容翻译为通顺的中文。
- 混合类表格: 若表格中同时包含变量名与较长的文字说明，请保留变量名原文，但将其后的解释性文本翻译成中文。

2. 引用与参考文献:
- 正文中的英文引用(如 (Author Year) 格式)和数字应用（如[1]）请严格保留原文，不可翻译或删除。
- 文后的 References 参考文献列表无需翻译，也不需要输出在最终结果中。

3. 公式处理:
正文及附录中的所有数学公式需使用Markdown格式准确还原。请特别注意上下标、希腊字母及特殊数学符号的完整性，禁止省略或简化任何数学表达式。

4. 语言风格:
译文应采用正式的学术语体。避免使用口语化表达。专业术语请参考该领域的通用译法。

### Output Format:
请确保翻译顺利进行，只输出翻译后的结果即可，不要输出任何多余的前缀或寒暄。"""

app = FastAPI(title="Zotero Antigravity Translation Bridge", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=".*",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def find_agy_path() -> str:
    """Locate agy.exe on the system."""
    env_path = os.environ.get("ANTIGRAVITY_CLI_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".gemini", "bin", "agy.exe"),
        os.path.join(home, ".gemini", "bin", "agy"),
        shutil.which("agy.exe"),
        shutil.which("agy")
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.abspath(c)

    raise FileNotFoundError("Could not find agy.exe in ~/.gemini/bin or system PATH.")

class TranslateRequest(BaseModel):
    text: str
    model: Optional[str] = "gemini-3.8-flash-high"
    prompt: Optional[str] = None
    effort: Optional[str] = "high"

class FullTextRequest(BaseModel):
    pdf_path: str
    model: Optional[str] = "gemini-3.8-flash-high"
    prompt: Optional[str] = None
    effort: Optional[str] = "high"

@app.get("/health")
def health_check():
    try:
        agy_path = find_agy_path()
        return {"status": "ok", "agy_path": agy_path, "version": "1.0.0"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

CACHED_MODELS = [
    {"id": "gemini-3.8-flash-low", "name": "Gemini 3.8 Flash (Low)"},
    {"id": "gemini-3.8-flash-medium", "name": "Gemini 3.8 Flash (Medium)"},
    {"id": "gemini-3.8-flash-high", "name": "Gemini 3.8 Flash (High)"},
    {"id": "gemini-3.1-pro-high", "name": "Gemini 3.1 Pro (High)"},
    {"id": "gemini-3.1-pro-low", "name": "Gemini 3.1 Pro (Low)"}
]

@app.get("/api/models")
async def get_models():
    """Retrieve available models with fast fallback."""
    global CACHED_MODELS
    try:
        agy = find_agy_path()
        extra_kwargs = {"creationflags": 0x08000000} if os.name == "nt" else {}
        proc = await asyncio.create_subprocess_exec(
            agy, "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **extra_kwargs
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        lines = stdout.decode("utf-8", errors="ignore").splitlines()
        parsed = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("Fetching"):
                continue
            parts = line.split("\t")
            mid = parts[0].strip()
            mname = parts[1].strip() if len(parts) >= 2 else mid
            lowered = mid.lower()
            if "claude" in lowered or "gpt" in lowered or "3.7" in lowered or "oss" in lowered:
                continue
            parsed.append({"id": mid, "name": mname})
        if parsed:
            CACHED_MODELS = parsed
        return {"models": CACHED_MODELS}
    except Exception:
        return {"models": CACHED_MODELS}

async def stream_agy_translation(user_prompt: str, text: str, model: str, effort: str):
    """
    Spawns agy.exe with stream-json output format,
    parses stdout NDJSON lines, and yields SSE chunks.
    """
    agy_path = find_agy_path()
    combined_prompt = f"{user_prompt}\n\n待翻译学术内容如下：\n```text\n{text}\n```"

    cmd = [
        agy_path,
        "-p", combined_prompt,
        "--model", model or "gemini-3.8-flash-high",
        "--effort", effort or "high",
        "--output-format", "stream-json"
    ]

    proc = None
    try:
        extra_kwargs = {"creationflags": 0x08000000} if os.name == "nt" else {}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **extra_kwargs
        )

        full_translation = []

        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="ignore").strip()
            if not line_str:
                continue

            try:
                data = json.loads(line_str)
                event_type = data.get("event")
                if event_type == "step_update":
                    step = data.get("step_update", {})
                    delta = step.get("text_delta")
                    if delta:
                        full_translation.append(delta)
                        yield f"data: {json.dumps({'type': 'chunk', 'text': delta}, ensure_ascii=False)}\n\n"
                elif event_type == "result":
                    res = data.get("result", {})
                    full_res = res.get("response", "".join(full_translation))
                    yield f"data: {json.dumps({'type': 'done', 'full_text': full_res}, ensure_ascii=False)}\n\n"
                    break
            except json.JSONDecodeError:
                continue

        await proc.wait()
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
    finally:
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

@app.post("/api/translate/stream")
async def translate_stream(req: TranslateRequest):
    """Stream translation for selection."""
    user_prompt = req.prompt.strip() if req.prompt and req.prompt.strip() else DEFAULT_PROMPT
    return StreamingResponse(
        stream_agy_translation(user_prompt, req.text, req.model, req.effort),
        media_type="text/event-stream"
    )

async def run_fulltext_pipeline(pdf_path: str, model: str, prompt: str, effort: str):
    """
    Extracts sections from PDF, translates them sequentially with agy,
    and yields SSE progress and final aggregated markdown.
    """
    try:
        extractor = PDFExtractor(pdf_path)
        sections = extractor.extract_sections()
        total_sections = len(sections)

        yield f"data: {json.dumps({'type': 'start', 'total': total_sections}, ensure_ascii=False)}\n\n"

        aggregated_sections = []
        user_prompt = prompt.strip() if prompt and prompt.strip() else DEFAULT_PROMPT
        agy_path = find_agy_path()

        for idx, sec in enumerate(sections, 1):
            title = sec.get("title", f"Section {idx}")
            content = sec.get("content", "")

            yield f"data: {json.dumps({'type': 'progress', 'current': idx, 'total': total_sections, 'title': title}, ensure_ascii=False)}\n\n"

            sec_prompt = f"{user_prompt}\n\n待翻译小节 [{title}]：\n```text\n{content}\n```"
            cmd = [
                agy_path,
                "-p", sec_prompt,
                "--model", model or "gemini-3.8-flash-high",
                "--effort", effort or "high",
                "--output-format", "stream-json"
            ]

            extra_kwargs = {"creationflags": 0x08000000} if os.name == "nt" else {}
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **extra_kwargs
            )

            sec_trans_parts = []
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf-8", errors="ignore").strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    if data.get("event") == "step_update":
                        delta = data.get("step_update", {}).get("text_delta")
                        if delta:
                            sec_trans_parts.append(delta)
                            yield f"data: {json.dumps({'type': 'section_chunk', 'current': idx, 'text': delta}, ensure_ascii=False)}\n\n"
                    elif data.get("event") == "result":
                        break
                except json.JSONDecodeError:
                    continue

            await proc.wait()
            sec_trans = "".join(sec_trans_parts).strip()
            aggregated_sections.append({
                "title": title,
                "english": content,
                "chinese": sec_trans
            })

            yield f"data: {json.dumps({'type': 'section_done', 'current': idx, 'title': title, 'chinese': sec_trans}, ensure_ascii=False)}\n\n"

        # Generate full publication-grade Markdown
        pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
        md_lines = [
            f"# {pdf_name} - 全文出版级中文译本与研读笔记",
            "\n> 本笔记由 **Zotero Antigravity Translator** 驱动 Google Antigravity 自动生成。\n",
            "---\n"
        ]

        for sec in aggregated_sections:
            md_lines.append(f"## {sec['title']}\n")
            md_lines.append(f"{sec['chinese']}\n\n---\n\n")

        final_md = "\n".join(md_lines)
        yield f"data: {json.dumps({'type': 'full_done', 'markdown': final_md}, ensure_ascii=False)}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

@app.post("/api/translate/fulltext")
async def translate_fulltext(req: FullTextRequest):
    """Full-text translation endpoint with section progress."""
    if not os.path.exists(req.pdf_path):
        raise HTTPException(status_code=404, detail=f"PDF not found: {req.pdf_path}")

    return StreamingResponse(
        run_fulltext_pipeline(req.pdf_path, req.model, req.prompt, req.effort),
        media_type="text/event-stream"
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("BRIDGE_PORT", 28119))
    print(f"Starting Zotero Antigravity Bridge on port {port}...")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

