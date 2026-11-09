#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zotero Antigravity - Zero-Server Full-Text Translation CLI
Directly invoked by Zotero Subprocess to extract PDF sections and stream translations.
"""

import sys
import os
import re
import json
import argparse
import subprocess
import shutil

# Ensure stdout and stderr use utf-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Add current directory to sys.path so pdf_extractor can be imported
curr_dir = os.path.dirname(os.path.abspath(__file__))
if curr_dir not in sys.path:
    sys.path.insert(0, curr_dir)

try:
    from pdf_extractor import PDFExtractor
except ImportError:
    # Fallback to AppData location if needed
    appdata = os.environ.get("APPDATA", "")
    bridge_dir = os.path.join(appdata, "Zotero", "antigravity-bridge")
    if bridge_dir not in sys.path:
        sys.path.insert(0, bridge_dir)
    from pdf_extractor import PDFExtractor

DEFAULT_PROMPT = """### **Role: 专业学术论文翻译专家**

### Task:
请将给定的英文学术论文内容翻译成中文。译文需满足出版级标准，确保术语准确、行文流畅、逻辑严密，并严格遵守中文学术写作规范。

### **Constraints & Guidelines**:
1. 表格内容处理策略(核心要求):
- 定量结果类表格: 包含大量数字、显著性星号及标准变量名的表格，保持原文不翻译。
- 定性描述类表格: 包含大量文本描述的表格，将表内的文本翻译为通顺中文。
- 混合类表格: 保留变量名原文，但将其后的解释性文本翻译成中文。

2. 引用与参考文献:
- 正文中的英文引用 (如 (Author, Year) 格式) 和数字引用 (如 [1]) 请严格保留原文，不可翻译或删除。
- 文后的 References 参考文献列表无需翻译，也不需要输出在最终结果中。

3. 公式处理:
- 正文中的所有数学公式需使用 Markdown / LaTeX 格式准确还原，上下标、希腊字母及特殊数学符号完整保留。

4. 语言风格:
- 译文应采用正式严谨的学术语体，专业术语符合学术规范。

### Output Format:
直接输出翻译结果，不要输出任何问候语、开场白、解释或多余的说明。"""

def find_agy(custom_path: str = None) -> str:
    if custom_path and os.path.isfile(custom_path):
        return custom_path

    candidates = [
        os.path.expanduser(r"~\.gemini\bin\agy.exe"),
        r"C:\Users\28048\.gemini\bin\agy.exe",
        shutil.which("agy.exe") or "",
        shutil.which("agy") or ""
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return "agy.exe"

def emit(data: dict):
    print(json.dumps(data, ensure_ascii=False), flush=True)

def main():
    parser = argparse.ArgumentParser(description="Antigravity Full-Text Translation CLI")
    parser.add_argument("--pdf", required=True, help="Path to PDF file")
    parser.add_argument("--model", default="gemini-3.8-flash-low", help="Model name")
    parser.add_argument("--agy", default="", help="Path to agy executable")
    parser.add_argument("--prompt", default="", help="Custom prompt")
    parser.add_argument("--effort", default="", help="Effort tier (optional)")

    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        emit({"type": "error", "message": f"PDF 文件不存在: {args.pdf}"})
        sys.exit(1)

    agy_path = find_agy(args.agy)
    if not os.path.isfile(agy_path) and not shutil.which(agy_path):
        emit({"type": "error", "message": f"未找到 Antigravity CLI (agy.exe)，请确认已安装。"})
        sys.exit(1)

    try:
        extractor = PDFExtractor(args.pdf)
        sections = extractor.extract_sections()
    except Exception as e:
        emit({"type": "error", "message": f"PDF 章节解析失败: {str(e)}"})
        sys.exit(1)

    total_sections = len(sections)
    if total_sections == 0:
        emit({"type": "error", "message": "未能从 PDF 中提取到有效的文本章节。"})
        sys.exit(1)

    emit({"type": "start", "total": total_sections})

    user_prompt = args.prompt.strip() if args.prompt and args.prompt.strip() else DEFAULT_PROMPT
    aggregated_sections = []

    for idx, sec in enumerate(sections, 1):
        title = sec.get("title", f"Section {idx}")
        content = sec.get("content", "").strip()
        if not content:
            continue

        emit({"type": "progress", "current": idx, "total": total_sections, "title": title})

        sec_prompt = f"{user_prompt}\n\n待翻译小节 [{title}]：\n```text\n{content}\n```"
        cmd = [
            agy_path,
            "-p", sec_prompt,
            "--model", args.model or "gemini-3.8-flash-low",
            "--disable-slash-commands",
            "--output-format", "stream-json"
        ]
        if args.effort:
            cmd.extend(["--effort", args.effort])

        try:
            startupinfo = None
            creationflags = 0
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                startupinfo=startupinfo,
                creationflags=creationflags
            )

            sec_trans_parts = []
            for line in proc.stdout:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    event_type = data.get("event")
                    if event_type == "step_update":
                        delta = data.get("step_update", {}).get("text_delta")
                        if delta:
                            sec_trans_parts.append(delta)
                    elif event_type == "result":
                        res_text = data.get("result", {}).get("response")
                        if res_text and (not sec_trans_parts or len("".join(sec_trans_parts)) < len(res_text)):
                            sec_trans_parts = [res_text]
                        break
                except json.JSONDecodeError:
                    continue

            proc.wait()
            sec_trans = "".join(sec_trans_parts).strip()
            aggregated_sections.append({
                "title": title,
                "english": content,
                "chinese": sec_trans
            })

            emit({"type": "section_done", "current": idx, "total": total_sections, "title": title, "chinese": sec_trans})
        except Exception as proc_err:
            emit({"type": "section_error", "current": idx, "title": title, "message": str(proc_err)})

    # Build final Markdown
    pdf_name = os.path.splitext(os.path.basename(args.pdf))[0]
    md_lines = [
        f"# {pdf_name} - 全文出版级中文译本与研读笔记",
        f"\n> 本笔记由 **Zotero Antigravity Translator** (模型: `{args.model}`) 驱动 Google Antigravity 自动生成。\n",
        "---\n"
    ]

    for sec in aggregated_sections:
        md_lines.append(f"## {sec['title']}\n\n")
        md_lines.append(f"{sec['chinese']}\n\n---\n\n")

    final_markdown = "\n".join(md_lines)
    emit({"type": "complete", "total": total_sections, "markdown": final_markdown})

if __name__ == "__main__":
    main()

