/**
 * Zotero Antigravity Translator - PDF Reader Integration
 */

var AntigravityReader = {
  addon: null,

  init(addon) {
    this.addon = addon;

    try {
      if (Zotero.Reader?.registerEventListener) {
        Zotero.Reader.registerEventListener(
          "renderTextSelectionPopup",
          (event) => this.onRenderSelectionPopup(event),
          addon.id
        );
        Zotero.Reader.registerEventListener(
          "renderToolbar",
          (event) => this.onRenderToolbar(event),
          addon.id
        );
        Zotero.Reader.registerEventListener(
          "createViewContextMenu",
          (event) => this.onCreateViewContextMenu(event),
          addon.id
        );
        Zotero.debug("[Antigravity] Successfully registered reader listeners (popup, toolbar, contextmenu).");
      }

      // Inject toolbar button into any readers already open
      if (Zotero.Reader?._readers) {
        for (let r of Zotero.Reader._readers) {
          if (r._iframeWindow?.document) {
            this.injectToolbarButtonFallback(r, r._iframeWindow.document);
          }
        }
      }
    } catch (e) {
      Zotero.logError(`[Antigravity] Failed to register reader listeners: ${e.message}`);
    }
  },

  injectStyles(doc) {
    if (doc.getElementById("antigravity-reader-style")) return;

    const style = doc.createElement("style");
    style.id = "antigravity-reader-style";
    style.textContent = `
      .selection-popup {
        max-width: 460px !important;
        width: 440px !important;
        box-sizing: border-box !important;
        border-radius: 10px !important;
      }
      .selection-popup .colors {
        width: 100% !important;
        justify-content: space-evenly !important;
      }
      .antigravity-popup-box {
        display: flex !important;
        flex-direction: column !important;
        box-sizing: border-box !important;
        width: 100% !important;
        background: var(--material-background, #ffffff);
        color: var(--material-color, #202124);
        border-radius: 8px;
        border: 1px solid rgba(26, 115, 232, 0.22);
        box-shadow: 0 4px 18px rgba(0, 0, 0, 0.06);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
        font-size: 13px;
        line-height: 1.5;
        margin-top: 8px;
        padding: 10px 12px;
      }
      @media (prefers-color-scheme: dark) {
        .antigravity-popup-box {
          background: #252627;
          color: #e8eaed;
          border-color: rgba(138, 180, 248, 0.22);
          box-shadow: 0 4px 18px rgba(0, 0, 0, 0.25);
        }
      }
      .antigravity-header {
        display: flex !important;
        flex-direction: row !important;
        align-items: center !important;
        justify-content: space-between !important;
        width: 100% !important;
        margin-bottom: 6px;
        padding-bottom: 6px;
        border-bottom: 1px solid rgba(128, 128, 128, 0.12);
      }
      .antigravity-title {
        display: flex !important;
        align-items: center !important;
        gap: 6px;
        font-weight: 700;
        font-size: 12.5px;
        color: #1a73e8;
        letter-spacing: 0.2px;
      }
      @media (prefers-color-scheme: dark) {
        .antigravity-title {
          color: #8ab4f8;
        }
      }
      .antigravity-badge {
        font-size: 11px;
        font-weight: 500;
        padding: 2px 7px;
        border-radius: 4px;
        background: rgba(26, 115, 232, 0.1);
        color: #1a73e8;
      }
      .antigravity-model-select {
        font-size: 11px;
        font-weight: 500;
        padding: 2px 6px;
        border-radius: 4px;
        background: rgba(26, 115, 232, 0.08);
        border: 1px solid rgba(26, 115, 232, 0.3);
        color: #1a73e8;
        cursor: pointer;
        outline: none;
        max-width: 200px;
        transition: all 0.15s ease;
      }
      .antigravity-model-select:hover {
        background: rgba(26, 115, 232, 0.16);
        border-color: #1a73e8;
      }
      @media (prefers-color-scheme: dark) {
        .antigravity-badge {
          background: rgba(138, 180, 248, 0.15);
          color: #8ab4f8;
        }
        .antigravity-model-select {
          background: #202124;
          border-color: rgba(138, 180, 248, 0.4);
          color: #8ab4f8;
        }
        .antigravity-model-select:hover {
          background: #2d2e30;
          border-color: #8ab4f8;
        }
      }
      .antigravity-content {
        min-height: 52px;
        max-height: 240px;
        overflow-y: auto;
        word-break: break-word;
        white-space: pre-wrap;
        user-select: text;
        padding: 4px 2px;
        font-size: 13.5px;
        line-height: 1.65;
        color: #1f2937;
      }
      @media (prefers-color-scheme: dark) {
        .antigravity-content {
          color: #e5e7eb;
        }
      }
      .antigravity-content.is-streaming::after {
        content: " ▋";
        display: inline-block;
        animation: antigravity-blink 0.8s infinite;
        color: #1a73e8;
      }
      @keyframes antigravity-blink {
        0%, 100% { opacity: 1; }
        50% { opacity: 0; }
      }
      .antigravity-toolbar {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: wrap !important;
        align-items: center !important;
        justify-content: flex-end !important;
        gap: 6px;
        margin-top: 8px;
        padding-top: 8px;
        border-top: 1px solid rgba(128, 128, 128, 0.12);
      }
      .antigravity-btn {
        display: inline-flex !important;
        align-items: center !important;
        gap: 4px;
        padding: 4px 10px;
        border-radius: 5px;
        border: 1px solid rgba(128, 128, 128, 0.25);
        background: transparent;
        color: inherit;
        font-size: 11.5px;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.15s ease;
      }
      .antigravity-btn:hover {
        background: rgba(26, 115, 232, 0.08);
        border-color: #1a73e8;
        color: #1a73e8;
      }
      .antigravity-btn-primary {
        background: #1a73e8 !important;
        color: #ffffff !important;
        border-color: #1a73e8 !important;
        box-shadow: 0 1px 3px rgba(26, 115, 232, 0.3) !important;
      }
      .antigravity-btn-primary:hover {
        background: #1557b0 !important;
        border-color: #1557b0 !important;
      }
      .antigravity-error {
        color: #d93025;
        font-size: 12px;
        padding: 4px 0;
      }
      @media (prefers-color-scheme: dark) {
        .antigravity-error {
          color: #f28b82;
        }
      }
      /* Dictionary Card Styles */
      .antigravity-dict-container {
        display: flex !important;
        flex-direction: column !important;
        gap: 5px !important;
        user-select: text !important;
        padding: 2px 0 !important;
      }
      .antigravity-dict-word-header {
        display: flex !important;
        align-items: baseline !important;
        flex-wrap: wrap !important;
        gap: 6px !important;
        margin-bottom: 2px !important;
      }
      .antigravity-dict-word {
        font-size: 15px !important;
        font-weight: 700 !important;
        color: #1a73e8 !important;
        letter-spacing: -0.2px !important;
      }
      @media (prefers-color-scheme: dark) {
        .antigravity-dict-word {
          color: #8ab4f8 !important;
        }
      }
      .antigravity-dict-phone {
        font-family: Consolas, "Segoe UI", sans-serif !important;
        font-size: 11.5px !important;
        color: #5f6368 !important;
      }
      @media (prefers-color-scheme: dark) {
        .antigravity-dict-phone {
          color: #9aa0a6 !important;
        }
      }
      .antigravity-dict-tag {
        font-size: 10px !important;
        font-weight: 500 !important;
        padding: 1px 5px !important;
        border-radius: 3px !important;
        background: rgba(26, 115, 232, 0.08) !important;
        color: #1a73e8 !important;
        border: 1px solid rgba(26, 115, 232, 0.2) !important;
      }
      @media (prefers-color-scheme: dark) {
        .antigravity-dict-tag {
          background: rgba(138, 180, 248, 0.12) !important;
          color: #8ab4f8 !important;
          border-color: rgba(138, 180, 248, 0.25) !important;
        }
      }
      .antigravity-dict-meanings {
        display: flex !important;
        flex-direction: column !important;
        gap: 3px !important;
      }
      .antigravity-dict-meaning-item {
        font-size: 12.5px !important;
        line-height: 1.5 !important;
        color: #202124 !important;
      }
      @media (prefers-color-scheme: dark) {
        .antigravity-dict-meaning-item {
          color: #e8eaed !important;
        }
      }
      .antigravity-dict-collins {
        margin-top: 4px !important;
        padding: 6px 8px !important;
        background: rgba(26, 115, 232, 0.04) !important;
        border-left: 3px solid #1a73e8 !important;
        border-radius: 0 4px 4px 0 !important;
        font-size: 11.5px !important;
        line-height: 1.45 !important;
        color: #374151 !important;
      }
      @media (prefers-color-scheme: dark) {
        .antigravity-dict-collins {
          background: rgba(138, 180, 248, 0.06) !important;
          border-left-color: #8ab4f8 !important;
          color: #d1d5db !important;
        }
      }
      .antigravity-dict-collins-def {
        font-weight: 500 !important;
        margin-bottom: 2px !important;
      }
      .antigravity-dict-example {
        font-size: 11px !important;
        color: #555 !important;
        font-style: italic !important;
        margin-top: 2px !important;
      }
      @media (prefers-color-scheme: dark) {
        .antigravity-dict-example {
          color: #9ca3af !important;
        }
      }
    `;
    doc.head.appendChild(style);
  },

  createToolbarButton(reader, doc) {
    const btn = doc.createElement("button");
    btn.id = "antigravity-toolbar-fulltext-btn";
    btn.className = "toolbar-button";
    btn.title = "Antigravity: 出版级全文翻译并生成笔记";
    btn.setAttribute("tabindex", "-1");
    btn.style.setProperty("width", "auto", "important");
    btn.style.setProperty("height", "28px", "important");
    btn.style.setProperty("display", "inline-flex", "important");
    btn.style.setProperty("align-items", "center", "important");
    btn.style.setProperty("gap", "5px", "important");
    btn.style.setProperty("padding", "0 10px", "important");
    btn.style.setProperty("margin", "0 4px", "important");
    btn.style.setProperty("cursor", "pointer", "important");
    btn.style.setProperty("border", "1px solid rgba(26, 115, 232, 0.5)", "important");
    btn.style.setProperty("border-radius", "4px", "important");
    btn.style.setProperty("background", "rgba(26, 115, 232, 0.12)", "important");
    btn.style.setProperty("box-sizing", "border-box", "important");
    btn.style.setProperty("flex-shrink", "0", "important");
    btn.style.setProperty("vertical-align", "middle", "important");

    btn.innerHTML = `
      <svg width="15" height="15" viewBox="0 0 48 48" fill="none" style="vertical-align: middle; flex-shrink: 0;">
        <rect width="48" height="48" rx="8" fill="#1a73e8"/>
        <path d="M24 10 L35 30 L29 30 L24 20 L19 30 L13 30 Z" fill="#ffffff"/>
        <circle cx="24" cy="35" r="3" fill="#ffffff"/>
      </svg>
      <span style="font-size: 12px; font-weight: 600; color: #1a73e8; white-space: nowrap; line-height: 1;">全文翻译</span>
    `;

    btn.onmouseenter = () => {
      btn.style.background = "rgba(26, 115, 232, 0.22)";
      btn.style.borderColor = "#1a73e8";
    };
    btn.onmouseleave = () => {
      btn.style.background = "rgba(26, 115, 232, 0.12)";
      btn.style.borderColor = "rgba(26, 115, 232, 0.5)";
    };

    btn.onclick = async (e) => {
      e.stopPropagation();
      e.preventDefault();
      const item = reader._item || (reader.itemID ? Zotero.Items.get(reader.itemID) : null);
      if (item) {
        const ft = this.addon?.FullText || (typeof AntigravityFullText !== "undefined" ? AntigravityFullText : null) || Zotero.Antigravity?.FullText;
        if (ft) {
          await ft.translateItem(item);
        } else {
          Zotero.alert(null, "Antigravity 翻译", "未能初始化全文翻译模块。");
        }
      } else {
        Zotero.alert(null, "Antigravity 翻译", "未找到当前阅读的文献条目。");
      }
    };

    return btn;
  },

  onRenderToolbar(event) {
    try {
      const { reader, doc, append } = event;
      const btn = this.createToolbarButton(reader, doc);

      if (typeof append === "function") {
        append(btn);
      } else {
        this.injectToolbarButtonFallback(reader, doc, btn);
      }
    } catch (err) {
      Zotero.logError(`[Antigravity] onRenderToolbar error: ${err.message}`);
    }
  },

  injectToolbarButtonFallback(reader, doc, existingBtn) {
    try {
      if (doc.getElementById("antigravity-toolbar-fulltext-btn")) return;
      const btn = existingBtn || this.createToolbarButton(reader, doc);

      const customSections = doc.querySelector(".toolbar .custom-sections");
      if (customSections) {
        const sec = doc.createElement("div");
        sec.className = "section";
        sec.appendChild(btn);
        customSections.appendChild(sec);
        return;
      }

      const endContainer = doc.querySelector(".toolbar .end") || doc.querySelector(".end");
      if (endContainer) {
        endContainer.prepend(btn);
      }
    } catch (e) {
      Zotero.logError(`[Antigravity] injectToolbarButtonFallback error: ${e.message}`);
    }
  },

  onCreateViewContextMenu(event) {
    try {
      const { reader, params, append } = event;
      append({
        label: "Antigravity: 出版级全文翻译并生成笔记",
        icon: `${this.addon.rootURI}content/icon.png`,
        onCommand: async () => {
          const item = reader._item || (reader.itemID ? Zotero.Items.get(reader.itemID) : null);
          if (item) {
            const ft = this.addon?.FullText || (typeof AntigravityFullText !== "undefined" ? AntigravityFullText : null) || Zotero.Antigravity?.FullText;
            if (ft) {
              await ft.translateItem(item);
            }
          }
        }
      });
    } catch (err) {
      Zotero.logError(`[Antigravity] onCreateViewContextMenu error: ${err.message}`);
    }
  },

  onRenderSelectionPopup(event) {
    try {
      const { reader, doc, params, append } = event;
      const selectedText = (params.annotation?.text || "").trim();
      if (!selectedText) return;

      // Safe window and DOM APIs resolution
      const mainWin = (typeof Zotero !== "undefined" && Zotero.getMainWindow && Zotero.getMainWindow()) || null;
      const readerWin = doc.defaultView || null;
      const globalWin = (typeof window !== "undefined" ? window : null);
      const safeFetch = (mainWin && mainWin.fetch)
        ? mainWin.fetch.bind(mainWin)
        : ((readerWin && readerWin.fetch) ? readerWin.fetch.bind(readerWin) : (typeof fetch !== "undefined" ? fetch : null));
      const SafeAbortController = (mainWin && mainWin.AbortController)
        || (readerWin && readerWin.AbortController)
        || (typeof AbortController !== "undefined" ? AbortController : null);
      const SafeTextDecoder = (mainWin && mainWin.TextDecoder)
        || (readerWin && readerWin.TextDecoder)
        || (typeof TextDecoder !== "undefined" ? TextDecoder : null);

      // Inject CSS styles directly into document
      this.injectStyles(doc);

      // Expand parent popup container
      const popup = doc.querySelector(".selection-popup");
      if (popup) {
        popup.style.maxWidth = "440px";
        popup.style.width = "420px";
        popup.style.boxSizing = "border-box";
        const colors = popup.querySelector(".colors");
        if (colors) {
          colors.style.width = "100%";
          colors.style.justifyContent = "space-evenly";
        }
      }

      const isSingleWordOrTerm = (text) => {
        if (!text) return false;
        const cleaned = text.trim().replace(/^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$/g, "");
        if (!cleaned) return false;
        if (/[.!?;\n\r\t]/.test(cleaned)) return false;
        const tokens = cleaned.split(/\s+/);
        return tokens.length <= 2 && /^[a-zA-Z0-9\-\s]+$/.test(cleaned) && cleaned.length <= 35;
      };

      const escapeHtml = (str) => {
        if (!str) return "";
        return String(str)
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#039;");
      };

      const fetchDict = async (word) => {
        const url = `https://dict.youdao.com/jsonapi?q=${encodeURIComponent(word)}`;
        let timeoutId = null;
        try {
          const ctrl = SafeAbortController ? new SafeAbortController() : null;
          if (ctrl) {
            timeoutId = setTimeout(() => {
              try { ctrl.abort(); } catch (e) {}
            }, 3500);
          }
          const fetchOpts = ctrl ? { signal: ctrl.signal } : {};
          const res = await safeFetch(url, fetchOpts);
          if (timeoutId) clearTimeout(timeoutId);
          if (!res || !res.ok) return null;
          const data = await res.json();
          if (!data) return null;

          const ec = data.ec?.word?.[0] || data.simple?.word?.[0];
          const usphone = ec?.usphone || "";
          const ukphone = ec?.ukphone || "";
          const phone = usphone ? `美 /${usphone}/` : (ukphone ? `英 /${ukphone}/` : "");
          const tags = data.ec?.exam_type || [];

          const meanings = [];
          const trs = data.ec?.word?.[0]?.trs || [];
          for (const t of trs) {
            const def = t?.tr?.[0]?.l?.i?.[0];
            if (def) meanings.push(def);
          }
          if (meanings.length === 0 && data.web_trans?.["web-translation"]) {
            const wt = data.web_trans["web-translation"]?.[0]?.trans || [];
            for (const item of wt) {
              if (item.value) meanings.push(item.value);
            }
          }
          if (meanings.length === 0 && data.fanyi?.tran) {
            meanings.push(data.fanyi.tran);
          }
          if (meanings.length === 0) return null;

          let collinsDef = "";
          let collinsEngSent = "";
          let collinsChnSent = "";
          const colEntry = data.collins?.collins_entries?.[0]?.entries?.entry?.[0]?.tran_entry?.[0];
          if (colEntry) {
            collinsDef = (colEntry.tran || "").replace(/<\/?b>/g, "").trim();
            const sent = colEntry.exam_sents?.sent?.[0];
            if (sent) {
              collinsEngSent = sent.eng_sent || "";
              collinsChnSent = sent.chn_sent || "";
            }
          }

          return {
            word,
            phone,
            tags,
            meanings,
            collinsDef,
            collinsEngSent,
            collinsChnSent
          };
        } catch (err) {
          if (timeoutId) clearTimeout(timeoutId);
          return null;
        }
      };

      const cleanedWord = selectedText.trim().replace(/^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$/g, "");
      const dictWordsPref = this.addon.getPref("dict_words", true);
      const isWord = dictWordsPref && isSingleWordOrTerm(selectedText);

      let selectedModel = this.addon.getPref("model", "gemini-3.8-flash-low");
      const port = this.addon.getPref("port", 28119);
      const customPrompt = this.addon.getPref("prompt", "");
      const engine = this.addon.getPref("engine", "direct"); // 'direct' or 'server'

      // Create popup container
      const box = doc.createElement("div");
      box.className = "antigravity-popup-box";

      // Header
      const header = doc.createElement("div");
      header.className = "antigravity-header";

      const titleDiv = doc.createElement("div");
      titleDiv.className = "antigravity-title";

      const updateTitle = (type) => {
        if (type === "dict") {
          titleDiv.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#1a73e8" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;">
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
            </svg>
            <span>牛津词典</span>
          `;
        } else if (type === "ai_word") {
          titleDiv.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 48 48" fill="none" style="vertical-align: middle;">
              <rect width="48" height="48" rx="8" fill="#1a73e8"/>
              <path d="M24 10 L35 30 L29 30 L24 20 L19 30 L13 30 Z" fill="#ffffff"/>
              <circle cx="24" cy="35" r="3" fill="#ffffff"/>
            </svg>
            <span>学术解析</span>
          `;
        } else {
          titleDiv.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 48 48" fill="none" style="vertical-align: middle;">
              <rect width="48" height="48" rx="8" fill="#1a73e8"/>
              <path d="M24 10 L35 30 L29 30 L24 20 L19 30 L13 30 Z" fill="#ffffff"/>
              <circle cx="24" cy="35" r="3" fill="#ffffff"/>
            </svg>
            <span>译文</span>
          `;
        }
      };

      updateTitle(isWord ? "dict" : "translate");

      const modelSelect = doc.createElement("select");
      modelSelect.className = "antigravity-model-select";
      modelSelect.title = "点击快速切换翻译/查词模式";

      const geminiModelList = [
        { id: "gemini-3.8-flash-low", name: "⚡ 3.8 Flash (Low) 极速" },
        { id: "gemini-3.8-flash-medium", name: "3.8 Flash (Medium) 均衡" },
        { id: "gemini-3.8-flash-high", name: "3.8 Flash (High) 思考" },
        { id: "gemini-3.1-pro-high", name: "💎 3.1 Pro (High) 推理" },
        { id: "gemini-3.1-pro-low", name: "3.1 Pro (Low) 专业" }
      ];

      if (isWord) {
        const dictOpt = doc.createElement("option");
        dictOpt.value = "dict";
        dictOpt.textContent = "⚡ 牛津词典 (秒查)";
        dictOpt.selected = true;
        modelSelect.appendChild(dictOpt);
      }

      for (const m of geminiModelList) {
        const opt = doc.createElement("option");
        opt.value = m.id;
        opt.textContent = m.name;
        if (!isWord && m.id === selectedModel) {
          opt.selected = true;
        }
        modelSelect.appendChild(opt);
      }

      modelSelect.addEventListener("change", (e) => {
        e.stopPropagation();
        const curVal = modelSelect.value;
        if (curVal === "dict") {
          updateTitle("dict");
        } else {
          selectedModel = curVal;
          this.addon.setPref("model", selectedModel);
          updateTitle(isWord ? "ai_word" : "translate");
        }
        if (currentProcess) {
          try { currentProcess.kill(); } catch (kErr) {}
          currentProcess = null;
        }
        if (abortController) {
          try { abortController.abort(); } catch (aErr) {}
          abortController = SafeAbortController ? new SafeAbortController() : null;
        }
        startTranslate();
      });

      header.appendChild(titleDiv);
      header.appendChild(modelSelect);
      box.appendChild(header);

      // Content area
      const content = doc.createElement("div");
      content.className = "antigravity-content is-streaming";
      content.textContent = isWord ? "⚡ 正在秒查牛津词典释义..." : "正在调用 Antigravity CLI 出版级翻译...";
      box.appendChild(content);

      // Toolbar
      const toolbar = doc.createElement("div");
      toolbar.className = "antigravity-toolbar";

      if (isWord) {
        const btnAi = doc.createElement("button");
        btnAi.className = "antigravity-btn";
        btnAi.textContent = "🤖 AI 解析";
        btnAi.title = "使用 Gemini 深度解析学术词义与语境搭配";
        btnAi.onclick = (e) => {
          e.stopPropagation();
          e.preventDefault();
          modelSelect.value = selectedModel || "gemini-3.8-flash-low";
          modelSelect.dispatchEvent(new doc.defaultView.Event("change"));
        };
        toolbar.appendChild(btnAi);
      }

      const btnHighlight = doc.createElement("button");
      btnHighlight.className = "antigravity-btn antigravity-btn-primary";
      btnHighlight.textContent = "✏️ 高亮与批注";
      btnHighlight.title = "在选中文本处添加高亮，并将译文写入批注";

      const btnNote = doc.createElement("button");
      btnNote.className = "antigravity-btn";
      btnNote.textContent = "📝 追加至笔记";
      btnNote.title = "将双语对照内容添加到文献主笔记";

      const btnCopy = doc.createElement("button");
      btnCopy.className = "antigravity-btn";
      btnCopy.textContent = "📋 复制";
      btnCopy.title = "复制译文到剪贴板";

      const btnRetry = doc.createElement("button");
      btnRetry.className = "antigravity-btn";
      btnRetry.textContent = "🔄 重试";

      toolbar.appendChild(btnHighlight);
      toolbar.appendChild(btnNote);
      toolbar.appendChild(btnCopy);
      toolbar.appendChild(btnRetry);
      box.appendChild(toolbar);

      // Append to popup
      append(box);

      let translatedText = "";
      let currentProcess = null;
      let abortController = SafeAbortController ? new SafeAbortController() : null;

      const decodeChunk = (value) => {
        try {
          if (SafeTextDecoder) {
            const dec = new SafeTextDecoder("utf-8");
            return dec.decode(value, { stream: true });
          } else if (typeof TextDecoder !== "undefined") {
            return new TextDecoder("utf-8").decode(value, { stream: true });
          }
        } catch (e) {}
        try {
          const binary = Array.from(value).map(b => String.fromCharCode(b)).join("");
          return decodeURIComponent(escape(binary));
        } catch (e) {
          return "";
        }
      };

      let hasReceivedFirstChunk = false;

      const processSseLine = (line) => {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data: ")) return;
        try {
          const data = JSON.parse(trimmed.slice(6));
          if (data.type === "chunk") {
            if (!hasReceivedFirstChunk) {
              hasReceivedFirstChunk = true;
              content.textContent = "";
            }
            translatedText += data.text;
            content.textContent = translatedText;
            content.scrollTop = content.scrollHeight;
          } else if (data.type === "done") {
            if (data.full_text) {
              translatedText = data.full_text;
              content.textContent = translatedText;
            }
          } else if (data.type === "error") {
            throw new Error(data.message);
          }
        } catch (e) {
          if (e.message && line.includes('"type":"error"')) throw e;
        }
      };

      const startTranslate = async () => {
        translatedText = "";
        hasReceivedFirstChunk = false;

        // === 0. DICTIONARY MODE (Instant Oxford/Collins Lookup for single words) ===
        if (modelSelect.value === "dict" && isWord) {
          content.className = "antigravity-content is-streaming";
          content.textContent = "⚡ 正在秒查牛津词典释义...";
          try {
            const dictInfo = await fetchDict(cleanedWord);
            if (dictInfo && dictInfo.meanings && dictInfo.meanings.length > 0) {
              content.classList.remove("is-streaming");
              content.innerHTML = `
                <div class="antigravity-dict-container">
                  <div class="antigravity-dict-word-header">
                    <span class="antigravity-dict-word">${escapeHtml(dictInfo.word)}</span>
                    ${dictInfo.phone ? `<span class="antigravity-dict-phone">${escapeHtml(dictInfo.phone)}</span>` : ""}
                    ${dictInfo.tags.slice(0, 4).map(t => `<span class="antigravity-dict-tag">${escapeHtml(t)}</span>`).join("")}
                  </div>
                  <div class="antigravity-dict-meanings">
                    ${dictInfo.meanings.map(m => `<div class="antigravity-dict-meaning-item">${escapeHtml(m)}</div>`).join("")}
                  </div>
                  ${dictInfo.collinsDef ? `
                    <div class="antigravity-dict-collins">
                      <div class="antigravity-dict-collins-def">📖 ${escapeHtml(dictInfo.collinsDef)}</div>
                      ${dictInfo.collinsEngSent ? `<div class="antigravity-dict-example">例: ${escapeHtml(dictInfo.collinsEngSent)} <span style="opacity: 0.85;">(${escapeHtml(dictInfo.collinsChnSent)})</span></div>` : ""}
                    </div>
                  ` : ""}
                </div>
              `;
              let plain = `${dictInfo.word}`;
              if (dictInfo.phone) plain += ` [${dictInfo.phone}]`;
              plain += `\n${dictInfo.meanings.join("\n")}`;
              if (dictInfo.collinsEngSent) {
                plain += `\n例: ${dictInfo.collinsEngSent} (${dictInfo.collinsChnSent})`;
              }
              translatedText = plain;
              if (this.addon?.recordUsage) {
                this.addon.recordUsage({
                  type: "dict",
                  chars: cleanedWord.length,
                  model: "oxford-dict"
                });
              }
              return;
            }
          } catch (dictErr) {
            Zotero.debug(`[Antigravity] Dictionary lookup failed, falling back to LLM: ${dictErr.message}`);
          }
          // If dictionary lookup failed or empty, fallback seamlessly to LLM
        }

        content.className = "antigravity-content is-streaming";
        content.textContent = "正在调用 Antigravity CLI 出版级翻译...";

        const currentEngine = this.addon.getPref("engine", "direct");

        // === 1. DIRECT SUBPROCESS MODE (Zero-Server Architecture, Default) ===
        if (currentEngine === "direct") {
          try {
            const Subprocess = await this.addon.getSubprocess();
            if (!Subprocess) {
              throw new Error("当前环境未找到 Subprocess 模块。");
            }

            let agyPath = this.addon.getAgyPath();
            if (!agyPath || agyPath === "agy.exe") {
              try {
                let resolved = await Subprocess.pathSearch("agy.exe");
                if (resolved) agyPath = resolved;
              } catch (e) {}
            }
            const userPrompt = (customPrompt && customPrompt.trim())
              ? customPrompt.trim()
              : (this.addon.SELECTION_PROMPT || this.addon.DEFAULT_PROMPT);
            const combinedPrompt = `${userPrompt}\n\n待翻译学术内容如下：\n\`\`\`text\n${selectedText}\n\`\`\``;

            const activeModel = selectedModel || this.addon.getPref("model", "gemini-3.8-flash-low");
            const args = [
              "-p", combinedPrompt,
              "--model", activeModel,
              "--disable-slash-commands",
              "--output-format", "stream-json"
            ];

            currentProcess = await Subprocess.call({
              command: agyPath,
              arguments: args,
              stdout: "pipe",
              stderr: "pipe"
            });

            let buffer = "";
            while (true) {
              let chunk = await currentProcess.stdout.readString();
              if (!chunk) break; // EOF

              buffer += chunk;
              let lines = buffer.split("\n");
              buffer = lines.pop() || "";

              for (let line of lines) {
                let trimmed = line.trim();
                if (!trimmed) continue;
                try {
                  let data = JSON.parse(trimmed);
                  if (data.event === "step_update") {
                    let delta = data.step_update?.text_delta;
                    if (delta) {
                      if (!hasReceivedFirstChunk) {
                        hasReceivedFirstChunk = true;
                        content.textContent = "";
                      }
                      translatedText += delta;
                      content.textContent = translatedText;
                      content.scrollTop = content.scrollHeight;
                    }
                  } else if (data.event === "result") {
                    let res = data.result?.response;
                    if (res && (!translatedText || translatedText.length < res.length)) {
                      translatedText = res;
                      content.textContent = translatedText;
                    }
                  }
                } catch (e) {}
              }
            }

            if (buffer.trim()) {
              try {
                let data = JSON.parse(buffer.trim());
                if (data.event === "result" && data.result?.response) {
                  translatedText = data.result.response;
                  content.textContent = translatedText;
                }
              } catch (e) {}
            }

            try {
              await currentProcess.wait();
            } catch (e) {}
            currentProcess = null;

            if (!translatedText) {
              content.textContent = "未能获取到翻译结果，请重试。";
            } else if (this.addon?.recordUsage) {
              this.addon.recordUsage({
                type: "selection",
                chars: selectedText.length,
                model: activeModel
              });
            }
            return;
          } catch (directErr) {
            Zotero.logError(`[Antigravity] Direct Subprocess translation error: ${directErr.message}`);
            content.className = "antigravity-content antigravity-error";
            content.innerHTML = `⚠️ <b>直连翻译失败</b><br/><span style="font-size:11px; color:#c5221f;">${directErr.message}</span><br/><span style="font-size:11px; color:#666;">请确认 Antigravity CLI 路径已在设置中正确配置。</span>`;
            return;
          } finally {
            content.classList.remove("is-streaming");
          }
        }

        // === 2. SERVER MODE (HTTP Bridge Fallback) ===
        try {
          if (!safeFetch) {
            throw new Error("当前环境未找到可用的 fetch API。");
          }

          const activeModel = selectedModel || this.addon.getPref("model", "gemini-3.8-flash-low");
          const fetchOptions = {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              text: selectedText,
              model: activeModel,
              prompt: customPrompt || undefined
            })
          };
          if (abortController) {
            fetchOptions.signal = abortController.signal;
          }

          let response;
          try {
            response = await safeFetch(`http://127.0.0.1:${port}/api/translate/stream`, fetchOptions);
          } catch (fetchErr) {
            // Auto-heal: Attempt to launch bridge daemon if down
            this.addon.ensureBridgeRunning();
            throw new Error(`无法连接 Bridge 守护服务 (127.0.0.1:${port})。\n系统已尝试自动在后台拉起 Bridge，请稍后点击「重试」，或切换为【免 Server 直连模式】。`);
          }

          if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
          }

          const readerStream = response.body?.getReader ? response.body.getReader() : null;
          let buffer = "";

          if (readerStream) {
            while (true) {
              const { done, value } = await readerStream.read();
              if (done) break;

              buffer += decodeChunk(value);
              const lines = buffer.split("\n\n");
              buffer = lines.pop() || "";

              for (const line of lines) {
                processSseLine(line);
              }
            }
            if (buffer.trim()) {
              processSseLine(buffer);
            }
          } else {
            const fullText = await response.text();
            const lines = fullText.split("\n\n");
            for (const line of lines) {
              processSseLine(line);
            }
          }

          if (!translatedText) {
            content.textContent = "未能获取到翻译结果，请重试。";
          } else if (this.addon?.recordUsage) {
            this.addon.recordUsage({
              type: "selection",
              chars: selectedText.length,
              model: activeModel
            });
          }
        } catch (err) {
          Zotero.logError(err);
          if (err.name !== "AbortError") {
            content.className = "antigravity-content antigravity-error";
            content.innerHTML = `⚠️ <b>翻译连接失败</b><br/><span style="font-size:11px; color:#c5221f;">${err.message || err}</span><br/><span style="font-size:11px; color:#666;">建议在设置中切换为【免 Server 直连模式】，无需常驻后台服务！</span>`;
          }
        } finally {
          content.classList.remove("is-streaming");
        }
      };

      // Button event listeners
      btnHighlight.onclick = async (e) => {
        e.stopPropagation();
        e.preventDefault();
        if (!translatedText) return;

        try {
          const readerObj = reader._internalReader || (doc.defaultView?.wrappedJSObject || doc.defaultView)?._reader;
          const annoManager = readerObj?._annotationManager;

          // 1. If user clicked on an existing annotation, simply update its comment
          if (params.annotation?.id && annoManager?.updateAnnotations) {
            annoManager.updateAnnotations([{
              id: params.annotation.id,
              comment: translatedText
            }]);
            btnHighlight.textContent = "✓ 已更新批注";
            btnHighlight.disabled = true;
            return;
          }

          // 2. Prepare new highlight annotation data
          const baseAnno = Object.assign({}, params.annotation, {
            type: "highlight",
            color: "#ffd400",
            comment: translatedText,
            sortIndex:
              params.annotation?.sortIndex ||
              `${String(params.annotation?.position?.pageIndex || 0).padStart(5, "0")}_${Date.now()}`
          });

          let win = doc.defaultView;
          let clonedAnno = baseAnno;
          try {
            if (typeof Components !== "undefined" && Components.utils?.cloneInto && win) {
              clonedAnno = Components.utils.cloneInto(baseAnno, win);
            }
          } catch (cloneErr) {}

          let created = null;
          if (readerObj?._onAddAnnotation) {
            created = readerObj._onAddAnnotation(clonedAnno, true);
          } else if (annoManager?.addAnnotation) {
            created = annoManager.addAnnotation(clonedAnno);
          }

          // 3. Fallback direct to DB if internal manager not found
          if (!created) {
            let item = Zotero.Items.get(reader.itemID || reader._item?.id);
            if (item) {
              await Zotero.Annotations.saveFromJSON(item, baseAnno);
            }
          }

          btnHighlight.textContent = "✓ 已添加批注";
          btnHighlight.disabled = true;
        } catch (err) {
          Zotero.logError(`[Antigravity] Highlight error: ${err.message || err}`);
          btnHighlight.textContent = "⚠️ 添加失败";
        }
      };

      btnNote.onclick = async (e) => {
        e.stopPropagation();
        e.preventDefault();
        if (!translatedText) return;

        try {
          const item = reader._item || (reader.itemID ? Zotero.Items.get(reader.itemID) : null);
          if (!item) {
            throw new Error("未找到当前文献主条目。");
          }
          await this.addon.appendBilingualToNote(item, selectedText, translatedText);
          btnNote.textContent = "✓ 已写入笔记";
          btnNote.disabled = true;
        } catch (err) {
          Zotero.logError(`[Antigravity] Append note error: ${err.message || err}`);
          btnNote.textContent = "⚠️ 写入失败";
        }
      };

      btnCopy.onclick = (e) => {
        e.stopPropagation();
        e.preventDefault();
        if (!translatedText) return;

        try {
          const clipboard = Components.classes["@mozilla.org/widget/clipboardhelper;1"].getService(
            Components.interfaces.nsIClipboardHelper
          );
          clipboard.copyString(translatedText);
          btnCopy.textContent = "✓ 已复制";
          setTimeout(() => {
            btnCopy.textContent = "📋 复制";
          }, 2000);
        } catch (err) {
          Zotero.logError(err);
        }
      };

      btnRetry.onclick = (e) => {
        e.stopPropagation();
        e.preventDefault();
        if (currentProcess) {
          try { currentProcess.kill(); } catch (e) {}
          currentProcess = null;
        }
        if (abortController) {
          try { abortController.abort(); } catch (e) {}
          abortController = SafeAbortController ? new SafeAbortController() : null;
        }
        startTranslate();
      };

      // Trigger translation automatically
      startTranslate();
    } catch (outerErr) {
      Zotero.logError(`[Antigravity] onRenderSelectionPopup error: ${outerErr.message}`);
    }
  }
};
