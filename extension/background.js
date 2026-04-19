// ── TruthLens Background Service Worker ───────────────────────────────────
// Works with local Ollama (llama3.1:8b) + Tavily web search backend.
// Note: Analysis takes ~15-30s with local model — notification appears after completion.

const API = "http://localhost:5000";

// ── Create context menu on install ───────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id:       "truthlens-check",
    title:    'Fact-check "%s" with TruthLens',
    contexts: ["selection"]   // shows when text is selected
  });

  chrome.contextMenus.create({
    id:       "truthlens-check-page",
    title:    "Fact-check this page with TruthLens",
    contexts: ["page"]        // right-click anywhere on page
  });
});

// ── Handle context menu clicks ────────────────────────────────────────────
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const stored = await chrome.storage.local.get(["user_id"]);

  if (!stored.user_id) {
    // Not logged in — open popup
    chrome.action.openPopup().catch(() => {});
    return;
  }

  let inputText = "";
  if (info.menuItemId === "truthlens-check" && info.selectionText) {
    inputText = info.selectionText.trim();
  } else if (info.menuItemId === "truthlens-check-page" && tab && tab.url) {
    inputText = tab.url;
  }

  if (!inputText) return;

  // Show "analyzing" notification — local model takes time
  chrome.notifications.create("tl-loading", {
    type:    "basic",
    iconUrl: "icons/icon48.png",
    title:   "TruthLens",
    message: "Searching web + running local AI… (15-30s)"
  });

  try {
    const res = await fetch(`${API}/api/extension/analyze`, {
      method:  "POST",
      headers: {
        "Content-Type":  "application/json",
        "Authorization": `Bearer ${stored.user_id}`
      },
      body: JSON.stringify({ input: inputText.substring(0, 1000) })
    });

    const data = await res.json();
    chrome.notifications.clear("tl-loading");

    if (!res.ok || data.error) {
      chrome.notifications.create({
        type:    "basic",
        iconUrl: "icons/icon48.png",
        title:   "TruthLens — Error",
        message: data.error || "Analysis failed."
      });
      return;
    }

    const verdictEmoji = {
      TRUE:      "✓ TRUE",
      FALSE:     "✗ FALSE",
      UNCERTAIN: "? UNCERTAIN"
    };

    chrome.notifications.create({
      type:    "basic",
      iconUrl: "icons/icon48.png",
      title:   `TruthLens — ${verdictEmoji[data.verdict] || data.verdict}`,
      message: data.explanation
        ? data.explanation.substring(0, 200)
        : "No explanation available."
    });

  } catch (e) {
    chrome.notifications.clear("tl-loading");
    chrome.notifications.create({
      type:    "basic",
      iconUrl: "icons/icon48.png",
      title:   "TruthLens — Connection Error",
      message: "Could not reach TruthLens server. Make sure python app.py is running on port 5000."
    });
  }
});