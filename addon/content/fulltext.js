/**
 * Zotero Antigravity Translator - Full-Text Translation Pipeline
 */

var AntigravityFullText = {
  addon: null,

  init(addon) {
    this.addon = addon;
  },

  async translateItem(item) {
    if (!item) return;

    // Resolve PDF attachment
    let pdfAttachment = null;
    if (item.isRegularItem()) {
      const attachments = await item.getAttachments();
      for (const attID of attachments) {
        const att = Zotero.Items.get(attID);
        if (att.isPDFAttachment()) {
          pdfAttachment = att;
          break;
        }
      }
    } else if (item.isPDFAttachment()) {
      pdfAttachment = item;
      item = item.parentItem || item;
    }

    if (!pdfAttachment) {
      Zotero.alert(null, "Antigravity 翻译", "未在选中的文献中找到可翻译的 PDF 附件。");
      return;
    }

    const filePath = await pdfAttachment.getFilePathAsync();
    if (!filePath) {
      Zotero.alert(null, "Antigravity 翻译", "无法获取 PDF 本地文件路径，请确认文件已下载到本地。");
      return;
    }

    const port = this.addon.getPref("port", 28119);
    const model = this.addon.getPref("model", "gemini-3.8-flash-high");
    const shortModel = (model || "").replace("gemini-", "").replace("claude-", "");
    const customPrompt = this.addon.getPref("prompt", "");

    // Create Zotero Progress Window
    const pw = new Zotero.ProgressWindow({ closeOnClick: false });
    pw.changeHeadline(`🚀 全文出版级翻译 [${shortModel}]`);
    pw.show();

    // Ensure clean progress window layout without any white bounding frames
    const applyCleanProgressWindowStyle = () => {
      try {
        const pwWin = pw._progressWindow;
        if (pwWin && pwWin.document) {
          const doc = pwWin.document;
          if (!doc.getElementById("antigravity-pw-custom-style")) {
            const style = doc.createElementNS("http://www.w3.org/1999/xhtml", "style");
            style.id = "antigravity-pw-custom-style";
            style.textContent = `
              .zotero-progress-item-label,
              description.zotero-progress-item-label,
              label.zotero-progress-item-label {
                min-width: 260px !important;
                max-width: 360px !important;
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
              }
            `;
            (doc.head || doc.documentElement).appendChild(style);
          }
        }
      } catch (e) {}
    };

    const relayoutProgressWindow = () => {
      try {
        applyCleanProgressWindowStyle();
        const pwWin = pw._progressWindow;
        if (pwWin) {
          pwWin.sizeToContent();
          if (Zotero.ProgressWindowSet && Zotero.ProgressWindowSet.tile) {
            Zotero.ProgressWindowSet.tile(pwWin);
          }
        }
      } catch (e) {}
    };

    applyCleanProgressWindowStyle();
    if (pw._progressWindow) {
      pw._progressWindow.addEventListener("load", () => {
        applyCleanProgressWindowStyle();
        relayoutProgressWindow();
      }, false);
    }

    const itemProgress = new pw.ItemProgress(
      "chrome://zotero/skin/treeitem-pdf.png",
      "正在提取 PDF 章节结构..."
    );
    itemProgress.setProgress(5);

    const mainWin = (typeof Zotero !== "undefined" && Zotero.getMainWindow && Zotero.getMainWindow()) || (typeof window !== "undefined" ? window : null);
    const safeFetch = (mainWin && mainWin.fetch) ? mainWin.fetch.bind(mainWin) : (typeof fetch !== "undefined" ? fetch : null);

    const engine = this.addon.getPref("engine", "direct");

    try {
      const Subprocess = await this.addon.getSubprocess();
      if (engine === "direct" && Subprocess) {
        // === DIRECT SUBPROCESS FULL-TEXT PIPELINE (ZERO-SERVER) ===
        const pythonPath = this.addon.getPythonPath();
        const cliScriptPath = this.addon.getCliScriptPath();
        const agyPath = this.addon.getAgyPath();

        itemProgress.setText("正在分析 PDF 章节与文本内容...");
        itemProgress.setProgress(10);

        const args = [
          cliScriptPath,
          "--pdf", filePath,
          "--model", model || "gemini-3.8-flash-high",
          "--agy", agyPath
        ];
        if (customPrompt && customPrompt.trim()) {
          args.push("--prompt", customPrompt.trim());
        }

        const proc = await Subprocess.call({
          command: pythonPath,
          arguments: args,
          stdout: "pipe",
          stderr: "pipe"
        });

        let buffer = "";
        let finalMarkdown = "";

        while (true) {
          let chunk = await proc.stdout.readString();
          if (!chunk) break;

          buffer += chunk;
          let lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (let line of lines) {
            let trimmed = line.trim();
            if (!trimmed) continue;
            try {
              let data = JSON.parse(trimmed);
              if (data.type === "start") {
                pw.changeHeadline(`🚀 全文出版级翻译 (共 ${data.total} 小节) [${shortModel}]`);
                itemProgress.setText(`已完成结构解析，开始翻译章节 (1/${data.total})...`);
                itemProgress.setProgress(12);
                relayoutProgressWindow();
              } else if (data.type === "progress") {
                let pct = Math.round(12 + ((data.current - 1) / (data.total || 1)) * 82);
                itemProgress.setProgress(pct);
                pw.changeHeadline(`🚀 全文翻译进度 (${data.current}/${data.total}) [${shortModel}]`);
                itemProgress.setText(`正在翻译: ${data.title}`);
                relayoutProgressWindow();
              } else if (data.type === "section_done") {
                let pct = Math.round(12 + (data.current / (data.total || 1)) * 82);
                itemProgress.setProgress(pct);
                itemProgress.setText(`✓ 已完成: ${data.title}`);
                relayoutProgressWindow();
              } else if (data.type === "complete") {
                finalMarkdown = data.markdown;
              } else if (data.type === "error") {
                throw new Error(data.message);
              }
            } catch (parseErr) {
              if (line.includes('"type": "error"') || line.includes('"type":"error"')) {
                throw parseErr;
              }
            }
          }
        }

        try { await proc.wait(); } catch (e) {}

        if (finalMarkdown) {
          pw.changeHeadline("📝 正在生成文献研读笔记");
          itemProgress.setText("所有章节翻译完毕，正在写入文献研读笔记...");
          itemProgress.setProgress(96);
          relayoutProgressWindow();

          const htmlNote = this.convertMarkdownToNoteHTML(finalMarkdown, item.getField("title"));
          const note = new Zotero.Item("note");
          note.libraryID = item.libraryID;
          note.parentID = item.id;
          note.setNote(htmlNote);
          await note.saveTx();

          if (this.addon?.recordUsage) {
            this.addon.recordUsage({
              type: "fulltext",
              chars: finalMarkdown.length,
              model: model || "gemini-3.8-flash-high",
              sections: totalSections || 1
            });
          }

          pw.changeHeadline("✅ 全文出版级翻译完成");
          itemProgress.setIcon("chrome://zotero/skin/tick.png");
          itemProgress.setText("✓ 出版级中文研读笔记已附加至文献！");
          itemProgress.setProgress(100);
          relayoutProgressWindow();
          pw.startCloseTimer(4500);
          return;
        } else {
          throw new Error("未能获取到全文翻译内容。");
        }
      }

      // === SERVER MODE FALLBACK (HTTP BRIDGE) ===
      if (!safeFetch) {
        throw new Error("当前环境未找到可用的 fetch API。");
      }

      if (this.addon?.ensureBridgeRunning) {
        await this.addon.ensureBridgeRunning();
        for (let i = 0; i < 6; i++) {
          try {
            const check = await safeFetch(`http://127.0.0.1:${port}/health`, { method: "GET" });
            if (check.ok) break;
          } catch (e) {}
          await new Promise(r => setTimeout(r, 500));
        }
      }

      const response = await safeFetch(`http://127.0.0.1:${port}/api/translate/fulltext`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pdf_path: filePath,
          model: model,
          prompt: customPrompt || undefined
        })
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const reader = response.body?.getReader ? response.body.getReader() : null;
      let buffer = "";
      let totalSections = 1;
      let finalMarkdown = "";

      const decodeChunk = (value) => {
        try {
          if (mainWin && mainWin.TextDecoder) {
            return new mainWin.TextDecoder("utf-8").decode(value, { stream: true });
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

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decodeChunk(value);
          const lines = buffer.split("\n\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith("data: ")) continue;

            try {
              const data = JSON.parse(trimmed.slice(6));
              if (data.type === "start") {
                totalSections = data.total || 1;
                itemProgress.setText(`发现 ${totalSections} 个章节，开始翻译...`);
                itemProgress.setProgress(10);
                relayoutProgressWindow();
              } else if (data.type === "progress") {
                const current = data.current;
                const title = data.title;
                const percent = Math.round(10 + (current / totalSections) * 80);
                itemProgress.setText(`正在翻译 (${current}/${totalSections}): ${title}`);
                itemProgress.setProgress(percent);
                relayoutProgressWindow();
              } else if (data.type === "full_done") {
                finalMarkdown = data.markdown;
              } else if (data.type === "error") {
                throw new Error(data.message);
              }
            } catch (e) {}
          }
        }
      }

      if (finalMarkdown) {
        itemProgress.setText("翻译完成！正在写入文献研读笔记...");
        itemProgress.setProgress(95);
        relayoutProgressWindow();

        const htmlNote = this.convertMarkdownToNoteHTML(finalMarkdown, item.getField("title"));
        const note = new Zotero.Item("note");
        note.libraryID = item.libraryID;
        note.parentID = item.id;
        note.setNote(htmlNote);
        await note.saveTx();

        if (this.addon?.recordUsage) {
          this.addon.recordUsage({
            type: "fulltext",
            chars: finalMarkdown.length,
            model: model || "gemini-3.8-flash-high",
            sections: totalSections || 1
          });
        }

        itemProgress.setText("✓ 出版级中文研读笔记生成成功！");
        itemProgress.setProgress(100);
        relayoutProgressWindow();
        pw.startCloseTimer(4000);
      } else {
        throw new Error("未能生成最终翻译内容。");
      }
    } catch (err) {
      Zotero.logError(err);
      itemProgress.setError();
      itemProgress.setText(`翻译失败: ${err.message}`);
      relayoutProgressWindow();
      pw.startCloseTimer(6000);
    }
  },

  convertMarkdownToNoteHTML(md, title) {
    let html = md
      .replace(/^# (.*$)/gim, "<h1>$1</h1>")
      .replace(/^## (.*$)/gim, "<h2>$1</h2>")
      .replace(/^### (.*$)/gim, "<h3>$1</h3>")
      .replace(/^\> (.*$)/gim, "<blockquote>$1</blockquote>")
      .replace(/\*\*(.*?)\*\*/g, "<b>$1</b>")
      .replace(/\*(.*?)\*/g, "<i>$1</i>")
      .replace(/\n\n/g, "<br/><br/>");

    return `
      <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6;">
        <h1 style="color: #1a73e8; border-bottom: 2px solid #1a73e8; padding-bottom: 4px;">📖 全文出版级中文译本与研读笔记</h1>
        <p style="color: #666; font-size: 12px;">文献标题: <b>${title || "学术论文"}</b><br/>生成工具: Google Antigravity CLI</p>
        <hr/>
        ${html}
      </div>
    `;
  }
};
