const API = "http://localhost:5000";

// ── Wire up all buttons here (no inline handlers in HTML) ─────────────────
document.addEventListener("DOMContentLoaded", () => {
  // Login button
  document.getElementById("ext-login-btn").addEventListener("click", extLogin);

  // Press Enter in password field → login
  document.getElementById("ext-password").addEventListener("keydown", (e) => {
    if (e.key === "Enter") extLogin();
  });

  // Analyze button
  document.getElementById("ext-analyze-btn").addEventListener("click", extAnalyze);

  // Press Ctrl+Enter in textarea → analyze
  document.getElementById("ext-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.ctrlKey) extAnalyze();
  });

  // Logout button
  document.getElementById("ext-logout").addEventListener("click", extLogout);

  // Boot
  init();
});

// ── Init: check if already logged in ─────────────────────────────────────
async function init() {
  const stored = await chrome.storage.local.get(["user_id", "user_name", "user_email"]);
  if (stored.user_id) {
    showMain(stored);
    await prefillInput();
  }
}

// ── Pre-fill textarea with selected text or current URL ──────────────────
async function prefillInput() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) return;

    if (!tab.url || (!tab.url.startsWith("http://") && !tab.url.startsWith("https://"))) {
      document.getElementById("ext-input").value = tab.url || "";
      return;
    }

    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => window.getSelection().toString().trim()
    });

    if (result && result.result && result.result.length > 0) {
      document.getElementById("ext-input").value = result.result.substring(0, 500);
    } else {
      document.getElementById("ext-input").value = tab.url;
    }
  } catch (e) {
    console.warn("prefillInput:", e.message);
  }
}

// ── Show main panel after successful login ────────────────────────────────
function showMain(user) {
  document.getElementById("login-section").style.display  = "none";
  document.getElementById("main-section").style.display   = "block";
  document.getElementById("ext-logout").style.display     = "inline";
  const displayName = user.user_name || user.user_email || "User";
  document.getElementById("ext-user-pill").textContent = displayName;
}

// ── Login ─────────────────────────────────────────────────────────────────
async function extLogin() {
  const btn      = document.getElementById("ext-login-btn");
  const errEl    = document.getElementById("ext-err");
  const email    = document.getElementById("ext-email").value.trim();
  const password = document.getElementById("ext-password").value;

  errEl.classList.remove("visible");

  if (!email || !password) {
    errEl.textContent = "Please enter your email and password.";
    errEl.classList.add("visible");
    return;
  }

  btn.disabled    = true;
  btn.textContent = "SIGNING IN...";

  try {
    const res = await fetch(`${API}/api/login`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ email, password })
    });

    const data = await res.json();

    if (!res.ok) {
      errEl.textContent = data.error || "Login failed. Please try again.";
      errEl.classList.add("visible");
      return;
    }

    await chrome.storage.local.set({
      user_id:    String(data.user_id),
      user_name:  data.name,
      user_email: data.email
    });

    showMain({ user_name: data.name, user_email: data.email });
    await prefillInput();

  } catch (e) {
    errEl.textContent = "Cannot connect to TruthLens server. Make sure it is running on port 5000.";
    errEl.classList.add("visible");
  } finally {
    btn.disabled    = false;
    btn.textContent = "SIGN IN";
  }
}

// ── Logout ────────────────────────────────────────────────────────────────
async function extLogout() {
  await chrome.storage.local.clear();
  document.getElementById("login-section").style.display  = "block";
  document.getElementById("main-section").style.display   = "none";
  document.getElementById("result-section").classList.remove("visible");
  document.getElementById("ext-logout").style.display     = "none";
  document.getElementById("ext-user-pill").textContent    = "Not signed in";
  document.getElementById("ext-email").value    = "";
  document.getElementById("ext-password").value = "";
}

// ── Analyze ───────────────────────────────────────────────────────────────
async function extAnalyze() {
  const input  = document.getElementById("ext-input").value.trim();
  if (!input) return;

  const btn     = document.getElementById("ext-analyze-btn");
  const loading = document.getElementById("ext-loading");
  const stored  = await chrome.storage.local.get(["user_id"]);

  if (!stored.user_id) { extLogout(); return; }

  btn.disabled    = true;
  btn.textContent = "ANALYZING...";
  loading.classList.add("active");
  document.getElementById("result-section").classList.remove("visible");

  // Show a status tip because Ollama + Tavily takes ~15-30s
  showStatus("Searching web + running local AI… (~15-30s)");

  try {
    const res = await fetch(`${API}/api/extension/analyze`, {
      method:  "POST",
      headers: {
        "Content-Type":  "application/json",
        "Authorization": `Bearer ${stored.user_id}`
      },
      body: JSON.stringify({ input })
    });

    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || `Server error ${res.status}`);
    hideStatus();
    renderResult(data);

  } catch (e) {
    hideStatus();
    document.getElementById("ext-icon").textContent = "!";
    const vEl       = document.getElementById("ext-verdict");
    vEl.textContent = "ERROR";
    vEl.className   = "verdict-word-sm false";
    document.getElementById("ext-explanation").textContent       = e.message;
    document.getElementById("corrected-label").style.display     = "none";
    document.getElementById("ext-corrected").style.display       = "none";
    document.getElementById("result-section").classList.add("visible");

  } finally {
    btn.disabled    = false;
    btn.textContent = "ANALYZE";
    loading.classList.remove("active");
  }
}

// ── Status tip (shown while Ollama is processing) ─────────────────────────
function showStatus(msg) {
  let el = document.getElementById("ext-status");
  if (!el) {
    el          = document.createElement("div");
    el.id       = "ext-status";
    el.style.cssText = "font-size:0.65rem;color:#555;text-align:center;padding:4px 0;letter-spacing:0.05em;";
    document.getElementById("ext-loading").insertAdjacentElement("afterend", el);
  }
  el.textContent = msg;
}
function hideStatus() {
  const el = document.getElementById("ext-status");
  if (el) el.textContent = "";
}

// ── Render result ─────────────────────────────────────────────────────────
function renderResult(data) {
  const v     = data.verdict.toLowerCase();
  const icons = { true: "✓", false: "✗", uncertain: "?" };

  document.getElementById("ext-icon").textContent = icons[v] || "?";

  const vEl       = document.getElementById("ext-verdict");
  vEl.textContent = data.verdict;
  vEl.className   = `verdict-word-sm ${v}`;

  document.getElementById("ext-explanation").textContent = data.explanation || "";
  document.getElementById("ext-corrected").textContent   = data.corrected_statement || "";

  // Hide corrected block if verdict is TRUE and statement says accurate
  const hide = v === "true" &&
    (data.corrected_statement || "").toLowerCase().includes("accurate");
  document.getElementById("corrected-label").style.display = hide ? "none" : "block";
  document.getElementById("ext-corrected").style.display   = hide ? "none" : "block";

  document.getElementById("result-section").classList.add("visible");
}