/**
 * Zotero Antigravity Translator - Preferences Logic
 */

(() => {
  const DEFAULT_ACADEMIC_PROMPT = `### **Role: 专业学术论文翻译专家**

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
请确保翻译顺利进行，只输出翻译后的结果即可，不要输出任何多余的前缀或寒暄。`;

  function setupAntigravityPrefs() {
    const root = document.getElementById("zotero-prefpane-antigravity");
    if (!root) return;

    const engineSelect = document.getElementById("antigravity-pref-engine");
    const agyPathInput = document.getElementById("antigravity-pref-agy-path");
    const btnDetectAgy = document.getElementById("antigravity-btn-detect-agy");
    const agyStatusLabel = document.getElementById("antigravity-agy-status");
    const modelSelect = document.getElementById("antigravity-pref-model");
    const portInput = document.getElementById("antigravity-pref-port");
    const autotranslateCheck = document.getElementById("antigravity-pref-autotranslate");
    const dictWordsCheck = document.getElementById("antigravity-pref-dict-words");
    const promptArea = document.getElementById("antigravity-pref-prompt");
    const btnTest = document.getElementById("antigravity-btn-test-conn");
    const btnResetPrompt = document.getElementById("antigravity-btn-reset-prompt");
    const statusLabel = document.getElementById("antigravity-conn-status");
    const serverRow = document.getElementById("antigravity-server-row");

    // 1. Sync values from Zotero.Prefs
    try {
      const savedEngine = Zotero.Prefs.get("extensions.antigravity.engine", true) || "direct";
      const savedAgyPath = Zotero.Prefs.get("extensions.antigravity.agy_path", true) || "";
      const savedModel = Zotero.Prefs.get("extensions.antigravity.model", true) || "gemini-3.8-flash-low";
      const savedPort = Zotero.Prefs.get("extensions.antigravity.port", true) || 28119;
      const savedAutotranslate = Zotero.Prefs.get("extensions.antigravity.autotranslate", true);
      const savedDictWords = Zotero.Prefs.get("extensions.antigravity.dict_words", true);
      const savedPrompt = Zotero.Prefs.get("extensions.antigravity.prompt", true);

      if (engineSelect && (!engineSelect.value || engineSelect.value !== savedEngine)) {
        engineSelect.value = savedEngine;
      }
      if (agyPathInput && !agyPathInput.value && savedAgyPath) {
        agyPathInput.value = savedAgyPath;
      }
      if (modelSelect && (!modelSelect.value || modelSelect.value !== savedModel)) {
        modelSelect.value = savedModel;
      }
      if (portInput && !portInput.value) {
        portInput.value = savedPort;
      }
      if (autotranslateCheck && savedAutotranslate !== undefined) {
        autotranslateCheck.checked = savedAutotranslate !== false;
      }
      if (dictWordsCheck && savedDictWords !== undefined) {
        dictWordsCheck.checked = savedDictWords !== false;
      }
      if (promptArea && !promptArea.value) {
        promptArea.value = savedPrompt || DEFAULT_ACADEMIC_PROMPT;
      }
    } catch (e) {
      Zotero.logError(`[Antigravity] Prefs sync error: ${e.message}`);
    }

    const updateEngineVisibility = () => {
      if (serverRow) {
        serverRow.style.opacity = engineSelect?.value === "server" ? "1" : "0.5";
      }
    };
    updateEngineVisibility();

    const checkAgyPath = () => {
      let path = agyPathInput?.value?.trim();
      if (!path && Zotero.Antigravity?.getAgyPath) {
        path = Zotero.Antigravity.getAgyPath();
      }
      let exists = false;
      if (path) {
        if (Zotero.Antigravity?.fileExists) {
          exists = Zotero.Antigravity.fileExists(path);
        } else {
          try {
            let file = Components.classes["@mozilla.org/file/local;1"].createInstance(Components.interfaces.nsIFile);
            file.initWithPath(path);
            exists = file.exists();
          } catch (e) {
            exists = true;
          }
        }
      }
      if (exists) {
        if (agyStatusLabel) {
          agyStatusLabel.style.color = "#137333";
          agyStatusLabel.textContent = "✓ CLI 已就绪";
        }
      } else {
        if (agyStatusLabel) {
          agyStatusLabel.style.color = "#d93025";
          agyStatusLabel.textContent = "⚠️ 未检测到 agy.exe";
        }
      }
    };
    checkAgyPath();

    // 2. Explicit event listeners to guarantee save
    if (modelSelect && !modelSelect._agBound) {
      modelSelect._agBound = true;
      modelSelect.addEventListener("change", () => {
        const val = modelSelect.value;
        Zotero.Prefs.set("extensions.antigravity.model", val, true);
        Zotero.debug(`[Antigravity] Model preference updated to: ${val}`);
      });
    }

    if (engineSelect && !engineSelect._agBound) {
      engineSelect._agBound = true;
      engineSelect.addEventListener("change", () => {
        Zotero.Prefs.set("extensions.antigravity.engine", engineSelect.value, true);
        updateEngineVisibility();
      });
    }

    if (agyPathInput && !agyPathInput._agBound) {
      agyPathInput._agBound = true;
      agyPathInput.addEventListener("input", () => {
        Zotero.Prefs.set("extensions.antigravity.agy_path", agyPathInput.value.trim(), true);
        checkAgyPath();
      });
    }

    if (portInput && !portInput._agBound) {
      portInput._agBound = true;
      portInput.addEventListener("change", () => {
        Zotero.Prefs.set("extensions.antigravity.port", parseInt(portInput.value, 10) || 28119, true);
      });
    }

    if (autotranslateCheck && !autotranslateCheck._agBound) {
      autotranslateCheck._agBound = true;
      autotranslateCheck.addEventListener("change", () => {
        Zotero.Prefs.set("extensions.antigravity.autotranslate", autotranslateCheck.checked, true);
      });
    }

    if (dictWordsCheck && !dictWordsCheck._agBound) {
      dictWordsCheck._agBound = true;
      dictWordsCheck.addEventListener("change", () => {
        Zotero.Prefs.set("extensions.antigravity.dict_words", dictWordsCheck.checked, true);
      });
    }

    if (promptArea && !promptArea._agBound) {
      promptArea._agBound = true;
      promptArea.addEventListener("input", () => {
        Zotero.Prefs.set("extensions.antigravity.prompt", promptArea.value, true);
      });
    }

    if (btnDetectAgy && !btnDetectAgy._agBound) {
      btnDetectAgy._agBound = true;
      btnDetectAgy.addEventListener("click", () => {
        if (Zotero.Antigravity?.getAgyPath) {
          const detected = Zotero.Antigravity.getAgyPath();
          if (agyPathInput) {
            agyPathInput.value = detected;
            Zotero.Prefs.set("extensions.antigravity.agy_path", detected, true);
            checkAgyPath();
          }
        }
      });
    }

    if (btnResetPrompt && !btnResetPrompt._agBound) {
      btnResetPrompt._agBound = true;
      btnResetPrompt.addEventListener("click", () => {
        if (confirm("确定要恢复默认出版级学术翻译提示词吗？")) {
          const def = Zotero.Antigravity?.DEFAULT_PROMPT || DEFAULT_ACADEMIC_PROMPT;
          if (promptArea) promptArea.value = def;
          Zotero.Prefs.set("extensions.antigravity.prompt", def, true);
        }
      });
    }

    if (btnTest && !btnTest._agBound) {
      btnTest._agBound = true;
      btnTest.addEventListener("click", async () => {
        const port = portInput?.value || 28119;
        if (statusLabel) {
          statusLabel.style.color = "#ea8600";
          statusLabel.textContent = "⏳ 连接检测中...";
        }

        try {
          const res = await fetch(`http://127.0.0.1:${port}/health`, { method: "GET" });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = await res.json();

          if (statusLabel) {
            statusLabel.style.color = "#137333";
            statusLabel.textContent = `🟢 已连接 (v${data.version || "1.0"})`;
          }
        } catch (e) {
          if (statusLabel) {
            statusLabel.style.color = "#d93025";
            statusLabel.textContent = "🔴 未连接 (Bridge 未运行)";
          }
        }
      });
    }

    // 3. Usage & Token Statistics Rendering
    const renderUsageStats = () => {
      let stats;
      try {
        if (Zotero.Antigravity?.getStats) {
          stats = Zotero.Antigravity.getStats();
        } else {
          const raw = Zotero.Prefs.get("extensions.antigravity.stats", true);
          stats = raw ? JSON.parse(raw) : null;
        }
      } catch (e) {}

      if (!stats) {
        stats = {
          totalCalls: 0,
          selectionCalls: 0,
          dictCalls: 0,
          fulltextCalls: 0,
          totalChars: 0,
          totalSections: 0,
          todayCalls: 0,
          todayChars: 0,
          lastDate: new Date().toISOString().slice(0, 10),
          modelUsage: {}
        };
      }

      const today = new Date().toISOString().slice(0, 10);
      const isToday = stats.lastDate === today;
      const todayCalls = isToday ? (stats.todayCalls || 0) : 0;
      const todayChars = isToday ? (stats.todayChars || 0) : 0;
      const todayTokens = Math.round(todayChars * 0.38);

      const totalChars = stats.totalChars || 0;
      const estTokens = Math.round(totalChars * 0.38);

      const elSelection = document.getElementById("stat-selection-calls");
      const elDict = document.getElementById("stat-dict-calls");
      const elFulltext = document.getElementById("stat-fulltext-calls");
      const elSections = document.getElementById("stat-fulltext-sections");
      const elTotalChars = document.getElementById("stat-total-chars");
      const elEstTokens = document.getElementById("stat-estimated-tokens");
      const elTodaySummary = document.getElementById("stat-today-summary");
      const elModelBreakdown = document.getElementById("stat-model-breakdown");

      if (elSelection) elSelection.textContent = (stats.selectionCalls || 0).toLocaleString();
      if (elDict) elDict.textContent = (stats.dictCalls || 0).toLocaleString();
      if (elFulltext) elFulltext.textContent = (stats.fulltextCalls || 0).toLocaleString();
      if (elSections) elSections.textContent = (stats.totalSections || 0).toLocaleString();
      if (elTotalChars) elTotalChars.textContent = totalChars.toLocaleString();
      if (elEstTokens) elEstTokens.textContent = estTokens.toLocaleString();

      if (elTodaySummary) {
        elTodaySummary.textContent = `📅 今日用量: ${todayCalls.toLocaleString()} 次调用 / ${todayChars.toLocaleString()} 字符 (约 ${todayTokens.toLocaleString()} Tokens)`;
      }

      if (elModelBreakdown) {
        const models = stats.modelUsage || {};
        const entries = Object.entries(models).sort((a, b) => b[1] - a[1]);
        if (entries.length > 0) {
          const topModel = entries[0][0].replace("gemini-", "").replace("-preview", "");
          elModelBreakdown.textContent = `主力模型: ${topModel} (${entries[0][1]}次)`;
        } else {
          elModelBreakdown.textContent = "主力模型: Gemini 3.8 Flash";
        }
      }
    };

    const btnRefreshStats = document.getElementById("antigravity-btn-refresh-stats");
    const btnResetStats = document.getElementById("antigravity-btn-reset-stats");

    if (btnRefreshStats && !btnRefreshStats._agBound) {
      btnRefreshStats._agBound = true;
      btnRefreshStats.addEventListener("click", () => {
        renderUsageStats();
      });
    }

    if (btnResetStats && !btnResetStats._agBound) {
      btnResetStats._agBound = true;
      btnResetStats.addEventListener("click", () => {
        if (confirm("确定要清空当前的翻译用量和 Token 统计数据吗？此操作不可恢复。")) {
          if (Zotero.Antigravity?.resetStats) {
            Zotero.Antigravity.resetStats();
          } else {
            const initial = {
              totalCalls: 0,
              selectionCalls: 0,
              dictCalls: 0,
              fulltextCalls: 0,
              totalChars: 0,
              totalSections: 0,
              todayCalls: 0,
              todayChars: 0,
              lastDate: new Date().toISOString().slice(0, 10),
              modelUsage: {}
            };
            Zotero.Prefs.set("extensions.antigravity.stats", JSON.stringify(initial), true);
          }
          renderUsageStats();
        }
      });
    }

    renderUsageStats();
  }

  // Trigger setup on multiple lifecycle hooks to guarantee execution
  setupAntigravityPrefs();
  setTimeout(setupAntigravityPrefs, 60);
  setTimeout(setupAntigravityPrefs, 300);

  const root = document.getElementById("zotero-prefpane-antigravity");
  if (root) {
    root.addEventListener("load", setupAntigravityPrefs);
    root.addEventListener("showing", setupAntigravityPrefs);
  }
})();
