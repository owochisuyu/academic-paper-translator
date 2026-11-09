/**
 * Zotero Antigravity Translator - Core Coordinator
 */

var ZoteroAntigravity = {
  id: "antigravity-translator@google.com",
  rootURI: null,
  initialized: false,

  DEFAULT_PROMPT: `### **Role: 专业学术论文翻译专家**

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
请确保翻译顺利进行，只输出翻译后的结果即可，不要输出任何多余的前缀或寒暄。`,

  SELECTION_PROMPT: `你是一位顶级专业学术文献翻译专家。请将以下选中的英文学术内容直接翻译为学术中文。
要求：
1. 术语严谨准确，符合中文学术语体风格；
2. 原文中的英文引用 (如 (Author, Year) 或 [1])、变量名和数学公式必须完整保留；
3. 直接输出翻译结果，严禁输出任何问候语、开场白、解释或括号补充说明。`,

  async init({ id, version, rootURI }) {
    if (this.initialized) return;
    this.id = id;
    this.rootURI = rootURI;
    this.initialized = true;

    // Initialize submodules
    if (this.Reader) {
      this.Reader.init(this);
    } else if (typeof AntigravityReader !== "undefined") {
      AntigravityReader.init(this);
    }

    if (this.FullText) {
      this.FullText.init(this);
    } else if (typeof AntigravityFullText !== "undefined") {
      AntigravityFullText.init(this);
    }

    // Auto-start server only if explicitly in server mode
    const engine = this.getPref("engine", "direct");
    if (engine === "server" && this.getPref("autostart", true)) {
      this.ensureBridgeRunning();
    }
  },

  getPref(name, defaultVal) {
    try {
      const val = Zotero.Prefs.get(`extensions.antigravity.${name}`, true);
      return val !== undefined ? val : defaultVal;
    } catch (e) {
      return defaultVal;
    }
  },

  setPref(name, val) {
    try {
      Zotero.Prefs.set(`extensions.antigravity.${name}`, val, true);
    } catch (e) {
      Zotero.logError(e);
    }
  },

  getStats() {
    try {
      const raw = Zotero.Prefs.get("extensions.antigravity.stats", true);
      if (raw) {
        return JSON.parse(raw);
      }
    } catch (e) {}
    return {
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
  },

  recordUsage({ type, chars = 0, model = "default", sections = 0 }) {
    try {
      const stats = this.getStats();
      const today = new Date().toISOString().slice(0, 10);
      if (stats.lastDate !== today) {
        stats.lastDate = today;
        stats.todayCalls = 0;
        stats.todayChars = 0;
      }

      stats.totalCalls = (stats.totalCalls || 0) + 1;
      stats.totalChars = (stats.totalChars || 0) + (chars || 0);
      stats.todayCalls = (stats.todayCalls || 0) + 1;
      stats.todayChars = (stats.todayChars || 0) + (chars || 0);

      if (type === "selection") {
        stats.selectionCalls = (stats.selectionCalls || 0) + 1;
      } else if (type === "dict") {
        stats.dictCalls = (stats.dictCalls || 0) + 1;
      } else if (type === "fulltext") {
        stats.fulltextCalls = (stats.fulltextCalls || 0) + 1;
        stats.totalSections = (stats.totalSections || 0) + (sections || 0);
      }

      if (!stats.modelUsage) stats.modelUsage = {};
      stats.modelUsage[model] = (stats.modelUsage[model] || 0) + 1;

      Zotero.Prefs.set("extensions.antigravity.stats", JSON.stringify(stats), true);
      return stats;
    } catch (e) {
      Zotero.logError(`[Antigravity] Record stats error: ${e.message}`);
    }
  },

  resetStats() {
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
    return initial;
  },

  fileExists(path) {
    if (!path) return false;
    try {
      let file = Components.classes["@mozilla.org/file/local;1"].createInstance(
        Components.interfaces.nsIFile
      );
      file.initWithPath(path);
      return file.exists();
    } catch (e) {
      return false;
    }
  },

  async getSubprocess() {
    try {
      if (typeof ChromeUtils !== "undefined" && ChromeUtils.importESModule) {
        return ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs").Subprocess;
      }
    } catch (e) {}
    try {
      if (typeof ChromeUtils !== "undefined" && ChromeUtils.import) {
        return ChromeUtils.import("resource://gre/modules/Subprocess.jsm").Subprocess;
      }
    } catch (e) {}
    try {
      return Components.utils.import("resource://gre/modules/Subprocess.jsm").Subprocess;
    } catch (e) {}
    return null;
  },

  getAgyPath() {
    let custom = this.getPref("agy_path", "");
    if (custom && this.fileExists(custom)) return custom;

    try {
      let home = Services.dirsvc.get("Home", Components.interfaces.nsIFile).path;
      let candidate = `${home}\\.gemini\\bin\\agy.exe`;
      if (this.fileExists(candidate)) return candidate;
    } catch (e) {}

    let fallback = "C:\\Users\\28048\\.gemini\\bin\\agy.exe";
    if (this.fileExists(fallback)) return fallback;

    return "agy.exe";
  },

  getPythonPath() {
    const candidates = [
      "C:\\Python314\\pythonw.exe",
      "C:\\Python313\\pythonw.exe",
      "C:\\Python312\\pythonw.exe",
      "C:\\Python311\\pythonw.exe",
      "C:\\Windows\\pyw.exe",
      "C:\\Python314\\python.exe",
      "C:\\Python313\\python.exe",
      "C:\\Python312\\python.exe",
      "C:\\Python311\\python.exe",
      "C:\\Windows\\py.exe"
    ];
    for (const p of candidates) {
      if (this.fileExists(p)) return p;
    }
    return "pythonw.exe";
  },

  getCliScriptPath() {
    const candidates = [
      "C:\\Users\\28048\\AppData\\Roaming\\Zotero\\antigravity-bridge\\fulltext_cli.py",
      "C:\\Users\\28048\\Documents\\antigravity\\bold-pasteur\\bridge\\fulltext_cli.py"
    ];
    try {
      let appdata = Services.dirsvc.get("AppData", Components.interfaces.nsIFile).path;
      candidates.unshift(`${appdata}\\Zotero\\antigravity-bridge\\fulltext_cli.py`);
    } catch (e) {}
    for (const p of candidates) {
      if (this.fileExists(p)) return p;
    }
    return candidates[0];
  },

  async ensureBridgeRunning() {
    const port = this.getPref("port", 28119);
    const mainWin = (typeof Zotero !== "undefined" && Zotero.getMainWindow && Zotero.getMainWindow()) || (typeof window !== "undefined" ? window : null);
    const safeFetch = (mainWin && mainWin.fetch) ? mainWin.fetch.bind(mainWin) : (typeof fetch !== "undefined" ? fetch : null);
    if (!safeFetch) return;

    try {
      const res = await safeFetch(`http://127.0.0.1:${port}/health`, { method: "GET" });
      if (res.ok) {
        Zotero.debug("[Antigravity] Bridge server is alive and healthy.");
        return;
      }
    } catch (e) {
      // Not running, proceed to launch
    }

    Zotero.debug("[Antigravity] Launching Bridge daemon in background...");
    this.launchBridgeProcess();
  },

  launchBridgeProcess() {
    try {
      let serverPyPath = null;
      try {
        const fileURI = Services.io.newURI(this.rootURI + "bridge/server.py");
        if (fileURI instanceof Components.interfaces.nsIFileURL) {
          serverPyPath = fileURI.file.path;
        }
      } catch (e) {}

      if (!serverPyPath || !this.fileExists(serverPyPath)) {
        const candidates = [
          "C:\\Users\\28048\\AppData\\Roaming\\Zotero\\antigravity-bridge\\server.py",
          "C:\\Users\\28048\\Documents\\antigravity\\bold-pasteur\\bridge\\server.py",
          Zotero.getZoteroDirectory().path + "\\extensions\\bridge\\server.py"
        ];
        for (const p of candidates) {
          if (this.fileExists(p)) {
            serverPyPath = p;
            break;
          }
        }
      }

      if (!serverPyPath) {
        Zotero.debug("[Antigravity] server.py path not found, skipping auto-launch.");
        return;
      }

      const pythonCandidates = [
        "C:\\Python314\\pythonw.exe",
        "C:\\Python313\\pythonw.exe",
        "C:\\Python312\\pythonw.exe",
        "C:\\Windows\\pyw.exe",
        "C:\\Windows\\py.exe"
      ];

      let pythonFile = Components.classes["@mozilla.org/file/local;1"].createInstance(
        Components.interfaces.nsIFile
      );

      let found = false;
      for (const pyPath of pythonCandidates) {
        try {
          pythonFile.initWithPath(pyPath);
          if (pythonFile.exists()) {
            found = true;
            break;
          }
        } catch (e) {}
      }

      if (!found) {
        Zotero.debug("[Antigravity] Could not find pythonw.exe to auto-launch bridge.");
        return;
      }

      const process = Components.classes["@mozilla.org/process/util;1"].createInstance(
        Components.interfaces.nsIProcess
      );
      process.init(pythonFile);
      const args = [serverPyPath];
      process.run(false, args, args.length);
      Zotero.debug(`[Antigravity] Bridge started silently with: ${pythonFile.path} ${serverPyPath}`);
    } catch (err) {
      Zotero.logError(`[Antigravity] Error launching bridge: ${err.message}`);
    }
  },

  async appendBilingualToNote(attachmentOrItem, sourceText, translatedText) {
    const item = attachmentOrItem.isRegularItem()
      ? attachmentOrItem
      : attachmentOrItem.parentItem || attachmentOrItem;

    const notes = await item.getNotes();
    let noteItem = null;
    for (const noteID of notes) {
      const n = Zotero.Items.get(noteID);
      if (n && n.getNote().includes("<!-- antigravity-notes-container -->")) {
        noteItem = n;
        break;
      }
    }

    const blockHTML = `
      <div style="border-left: 3px solid #1a73e8; padding-left: 10px; margin: 12px 0;">
        <p style="color: #202124; margin: 4px 0; line-height: 1.6;">${translatedText.replace(/\n/g, "<br/>")}</p>
      </div>
    `;

    if (!noteItem) {
      noteItem = new Zotero.Item("note");
      noteItem.libraryID = item.libraryID;
      noteItem.parentID = item.id;
      noteItem.setNote(`<!-- antigravity-notes-container -->${blockHTML}`);
      await noteItem.saveTx();
    } else {
      let currentContent = noteItem.getNote()
        .replace(/<h[1-6]>.*?文献划词与研读批注.*?<\/h[1-6]>/gi, "")
        .replace(/##\s*📖\s*文献划词与研读批注.*?(?:\r?\n|$)/gi, "");
      noteItem.setNote(currentContent + blockHTML);
      await noteItem.saveTx();
    }
  },

  onMainWindowLoad(window) {
    const doc = window.document;

    const createXUL = (tag) => {
      if (typeof doc.createXULElement === "function") {
        return doc.createXULElement(tag);
      }
      return doc.createElementNS("http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul", tag);
    };

    // 1. Hook into Library Item Context Menu (#zotero-itemmenu)
    const itemMenu = doc.getElementById("zotero-itemmenu");
    if (itemMenu) {
      const addItemMenuItem = () => {
        if (doc.getElementById("antigravity-menu-translate-fulltext")) return;

        const menuItem = createXUL("menuitem");
        menuItem.id = "antigravity-menu-translate-fulltext";
        menuItem.setAttribute("label", "Antigravity: 出版级全文翻译并生成笔记");
        menuItem.setAttribute("class", "menuitem-iconic");
        menuItem.style.listStyleImage = `url("${this.rootURI}content/icon.png")`;

        menuItem.addEventListener("command", async (e) => {
          e.stopPropagation();
          const selectedItems = window.ZoteroPane ? window.ZoteroPane.getSelectedItems() : [];
          if (selectedItems && selectedItems.length > 0) {
            const ft = this.FullText || (typeof AntigravityFullText !== "undefined" ? AntigravityFullText : null) || Zotero.Antigravity?.FullText;
            if (ft) {
              await ft.translateItem(selectedItems[0]);
            }
          }
        });

        itemMenu.appendChild(menuItem);
      };

      addItemMenuItem();
      itemMenu.addEventListener("popupshowing", () => {
        addItemMenuItem();
        const menuItem = doc.getElementById("antigravity-menu-translate-fulltext");
        if (menuItem) {
          const selectedItems = window.ZoteroPane ? window.ZoteroPane.getSelectedItems() : [];
          menuItem.hidden = !selectedItems || selectedItems.length === 0;
        }
      });
    }

    // 2. Hook into Tools Menu (#menu_ToolsPopup)
    const toolsPopup = doc.getElementById("menu_ToolsPopup");
    if (toolsPopup && !doc.getElementById("antigravity-tools-menu-translate")) {
      const toolsMenuItem = createXUL("menuitem");
      toolsMenuItem.id = "antigravity-tools-menu-translate";
      toolsMenuItem.setAttribute("label", "Antigravity: 出版级全文翻译 (选中文献)");
      toolsMenuItem.setAttribute("class", "menuitem-iconic");
      toolsMenuItem.style.listStyleImage = `url("${this.rootURI}content/icon.png")`;

      toolsMenuItem.addEventListener("command", async (e) => {
        e.stopPropagation();
        const selectedItems = window.ZoteroPane ? window.ZoteroPane.getSelectedItems() : [];
        if (selectedItems && selectedItems.length > 0) {
          const ft = this.FullText || (typeof AntigravityFullText !== "undefined" ? AntigravityFullText : null) || Zotero.Antigravity?.FullText;
          if (ft) {
            await ft.translateItem(selectedItems[0]);
          }
        } else {
          Zotero.alert(window, "Antigravity 翻译", "请先在文献列表中选择一篇文献。");
        }
      });

      toolsPopup.appendChild(toolsMenuItem);
    }
  },

  onMainWindowUnload(window) {
    const doc = window.document;
    const menuItem = doc.getElementById("antigravity-menu-translate-fulltext");
    if (menuItem) {
      menuItem.remove();
    }
    const toolsMenuItem = doc.getElementById("antigravity-tools-menu-translate");
    if (toolsMenuItem) {
      toolsMenuItem.remove();
    }
  }
};
