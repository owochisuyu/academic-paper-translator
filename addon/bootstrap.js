/**
 * Zotero Antigravity Translator - Bootstrap Entry Point
 * Compatible with Zotero 7/8/9 (Gecko 102~140+)
 */

var chromeHandle = null;
var prefPaneHandle = null;

function install(data, reason) {}

async function startup({ id, version, resourceURI, rootURI }, reason) {
  await Zotero.initializationPromise;

  if (!rootURI) {
    rootURI = resourceURI.spec;
  }

  // Register chrome url mapping
  try {
    const aomStartup = Components.classes[
      "@mozilla.org/addons/addon-manager-startup;1"
    ].getService(Components.interfaces.amIAddonManagerStartup);
    const manifestURI = Services.io.newURI(rootURI + "manifest.json");
    chromeHandle = aomStartup.registerChrome(manifestURI, [
      ["content", "antigravitytranslator", rootURI + "content/"]
    ]);
  } catch (e) {
    Zotero.logError(e);
  }

  // Dynamic window and DOM APIs provider
  const getWin = () =>
    (typeof Zotero !== "undefined" && Zotero.getMainWindow && Zotero.getMainWindow()) ||
    (typeof window !== "undefined" ? window : null);

  const ctx = {
    Zotero,
    Services,
    Components,
    rootURI,
    addonID: id,
    get fetch() {
      const w = getWin();
      return w?.fetch ? w.fetch.bind(w) : (typeof fetch !== "undefined" ? fetch : null);
    },
    get AbortController() {
      const w = getWin();
      return w?.AbortController || (typeof AbortController !== "undefined" ? AbortController : null);
    },
    get TextDecoder() {
      const w = getWin();
      return w?.TextDecoder || (typeof TextDecoder !== "undefined" ? TextDecoder : null);
    }
  };

  try {
    Services.scriptloader.loadSubScript(`${rootURI}content/reader.js`, ctx);
    Services.scriptloader.loadSubScript(`${rootURI}content/fulltext.js`, ctx);
    Services.scriptloader.loadSubScript(`${rootURI}content/antigravity.js`, ctx);

    // Expose globally on Zotero
    Zotero.Antigravity = ctx.ZoteroAntigravity;
    Zotero.Antigravity.Reader = ctx.AntigravityReader;
    Zotero.Antigravity.FullText = ctx.AntigravityFullText;

    await ctx.ZoteroAntigravity.init({ id, version, rootURI });

    // Inject into any main windows already open
    try {
      const windows = Zotero.getMainWindows ? Zotero.getMainWindows() : [];
      for (const win of windows) {
        ctx.ZoteroAntigravity.onMainWindowLoad(win);
      }
    } catch (winErr) {
      Zotero.logError(winErr);
    }
  } catch (e) {
    Zotero.logError(`[Antigravity] startup error: ${e.message}`);
  }

  // Register Preferences Pane in Zotero Preferences
  try {
    if (Zotero.PreferencePanes?.register) {
      prefPaneHandle = Zotero.PreferencePanes.register({
        pluginID: id,
        src: `${rootURI}content/preferences.xhtml`,
        label: "Antigravity",
        image: `${rootURI}content/icon.png`,
        scripts: [`${rootURI}content/preferences.js`]
      });
    }
  } catch (e) {
    Zotero.logError(e);
  }
}

async function onMainWindowLoad({ window }, reason) {
  if (Zotero.Antigravity) {
    Zotero.Antigravity.onMainWindowLoad(window);
  }
}

async function onMainWindowUnload({ window }, reason) {
  if (Zotero.Antigravity) {
    Zotero.Antigravity.onMainWindowUnload(window);
  }
}

function shutdown({ id, version, resourceURI, rootURI }, reason) {
  if (reason === APP_SHUTDOWN) {
    return;
  }

  try {
    if (prefPaneHandle && Zotero.PreferencePanes?.unregister) {
      Zotero.PreferencePanes.unregister(prefPaneHandle);
      prefPaneHandle = null;
    }
  } catch (e) {}

  if (chromeHandle) {
    try {
      chromeHandle.destruct();
    } catch (e) {}
    chromeHandle = null;
  }
}

function uninstall(data, reason) {}
