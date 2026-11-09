# Zotero Antigravity 翻译插件实施总结与使用说明

## 1. 成果概述

我们已成功为 Zotero 开发并部署了专属的学术论文翻译插件 **Zotero Antigravity Translator**，底层调用本机已安装的 **Google Antigravity CLI (`agy.exe`)**。

- **插件安装包**：`dist/antigravity-translator@google.com.xpi`
- **本地部署状态**：已自动部署至检测到的 Zotero 9 激活目录：  
  `C:\Users\28048\AppData\Roaming\Zotero\Zotero\Profiles\9dxhzn23.default\extensions\antigravity-translator@google.com.xpi`

---

## 2. 核心功能实现细节

### 功能一：PDF 阅读器划词流式打字翻译
- **触发**：在 Zotero 内置阅读器中选中文本，立即自动唤出悬浮翻译卡片。
- **流式打字**：通过 SSE 逐字渲染，毫秒级响应，无需等待整段翻译完毕。
- **三大快捷操作**：
  1. `✏️ 添加高亮与批注`：在选中文本上创建高亮标记，并将生成的学术译文直接写入该高亮的批注（Annotation Comment）中。
  2. `📝 追加至文献笔记`：以「原文 + 译文」格式整齐排版追加到当前文献主笔记中。
  3. `📋 复制`：一键复制译文到系统剪贴板。

### 功能二：长篇/全文出版级结构化研读笔记
- **触发**：右键任意论文条目 -> 点击「`Antigravity: 出版级全文翻译并生成笔记`」。
- **处理**：
  - 调用 `bridge/pdf_extractor.py` 智能解析 PDF 章节结构（Abstract、Intro、Methods、Results 等），去除页眉、页脚及行号杂质。
  - 自动识别并切除末尾 References 参考文献，节省大量无意义 Token。
  - 按逻辑章节分段流水线送入 `agy.exe` 逐节翻译，并在 Zotero 右下角弹出带有实时百分比与小节标题的进度指示器。
  - 最终在条目下自动生成层级清晰、支持折叠查看英文原文的完整中文研读笔记。

### 功能三：嵌入专业出版级学术翻译 Prompt
系统已默认配置完整的专家翻译指令：
- **表格处理**：定量结果表严格保持原文；定性描述表深入翻译；混合表格保留变量名并翻译文字说明。
- **文献引用**：正文中的英文引用格式 `(Author, Year)` 与数字引用 `[1]` 原样保留；文末 References 自动剔除。
- **公式解析**：所有数学公式使用 LaTeX/KaTeX Markdown 语法精确还原，严格保留希腊字母与上下标。

### 功能四：Zotero 独立偏好设置面板
- 打开 **Zotero 设置 -> Antigravity**：
  - 支持在下拉菜单中直接选择模型：`gemini-3.8-flash-high`（默认推荐）、`gemini-3.7-flash-high`、`gemini-3.1-pro-high`、`claude-sonnet-4-6`、`claude-opus-4-6-thinking` 等。
  - 自由编辑或一键恢复学术翻译系统提示词。
  - 服务健康状态实时探针与手动刷新。
  - 随 Zotero 自动在后台静默托管 Bridge 进程。

---

## 3. 验证与测试结果

| 测试项 | 验证方式 | 结果 |
| :--- | :--- | :--- |
| **Bridge 存活与 agy 路径检测** | 执行 `python bridge/test_bridge.py` 探针 `/health` | **PASS**（成功定位到 `~/.gemini/bin/agy.exe`） |
| **模型列表动态发现** | 调用 `/api/models` 解析 `agy models` 输出 | **PASS**（成功列出 Gemini 3.8 / 3.7 / 3.1 及 Claude 各模型） |
| **SSE 流式 NDJSON 转接** | 模拟学术英文输入测试 `/api/translate/stream` | **PASS**（实时打字推流完成，返回正确学术译文） |
| **XPI 自动化构建** | 运行 `python scripts/build.py` | **PASS**（生成 22.2 KB 标准 `.xpi` 安装包） |
| **本地 Profile 自动同步** | 运行 `python scripts/deploy_local.py` | **PASS**（成功写入 Zotero `9dxhzn23.default` 扩展目录） |

---

## 4. 立即体验与使用步骤

由于插件已经自动部署到你的 Zotero 用户目录中：

1. **打开或重启你的 Zotero 客户端**（若此前已打开，请关闭后重新打开）。
2. 打开任意一篇英文论文的 PDF：
   - **划选任意英文句子或段落**：观察弹出的悬浮窗，体验流式打字机翻译，并点击「✏️ 高亮与批注」查看效果。
   - **在文献库列表中右键论文**：选择「Antigravity: 出版级全文翻译并生成笔记」，体验自动生成的全文双语研读笔记。
3. 可随时在 **Zotero 设置（Preferences）-> Antigravity** 中切换模型或修改学术提示词。

