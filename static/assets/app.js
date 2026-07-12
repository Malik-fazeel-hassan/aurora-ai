/* Aurora — Claude-inspired frontend */
(() => {
  "use strict";

  const STORAGE_KEY = "aurora.chats.v1";
  const THEME_KEY = "aurora.theme";
  const MODEL_KEY = "aurora.model";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const state = {
    chats: [],
    activeId: null,
    providers: null,
    settings: null,
    selectedModel: localStorage.getItem(MODEL_KEY) || "auto",
    streaming: false,
    abort: null,
    artifact: null,
    renameId: null,
    agentMode: localStorage.getItem("aurora.agent") === "1",
    debateMode: localStorage.getItem("aurora.debate") === "1",
    orchMode: localStorage.getItem("aurora.orch") === "1",
    pipeline: localStorage.getItem("aurora.pipeline") || "auto",
    activity: [],
  };

  // ---------- Utils ----------
  const uid = () => crypto.randomUUID?.() || `id-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  const now = () => Date.now();

  function toast(msg, ms = 2200) {
    const el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, ms);
  }

  function saveChats() {
    const slim = state.chats.map((c) => ({
      id: c.id,
      title: c.title,
      createdAt: c.createdAt,
      updatedAt: c.updatedAt,
      model: c.model,
      messages: c.messages,
    }));
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ activeId: state.activeId, chats: slim }));
  }

  function loadChats() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      state.chats = Array.isArray(data.chats) ? data.chats : [];
      state.activeId = data.activeId || (state.chats[0] && state.chats[0].id) || null;
    } catch {
      state.chats = [];
    }
  }

  function activeChat() {
    return state.chats.find((c) => c.id === state.activeId) || null;
  }

  function createChat(title = "New chat") {
    const chat = {
      id: uid(),
      title,
      createdAt: now(),
      updatedAt: now(),
      model: state.selectedModel,
      messages: [],
    };
    state.chats.unshift(chat);
    state.activeId = chat.id;
    saveChats();
    return chat;
  }

  function deleteChat(id) {
    state.chats = state.chats.filter((c) => c.id !== id);
    if (state.activeId === id) {
      state.activeId = state.chats[0]?.id || null;
    }
    saveChats();
    render();
  }

  function renameChat(id, title) {
    const c = state.chats.find((x) => x.id === id);
    if (!c) return;
    c.title = title.trim() || "Untitled";
    c.updatedAt = now();
    saveChats();
    renderChatList();
  }

  function autoTitle(chat, firstUserText) {
    if (chat.title && chat.title !== "New chat") return;
    const t = firstUserText.replace(/\s+/g, " ").trim().slice(0, 48);
    chat.title = t || "New chat";
  }

  // ---------- Markdown (uses marked if present, else fallback) ----------
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function simpleMarkdown(src) {
    // Lightweight GFM-ish renderer used if marked fails to load
    let text = String(src || "").replace(/\r\n/g, "\n");
    const blocks = [];
    text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
      const i = blocks.length;
      blocks.push({ lang: lang || "", code });
      return `\n@@BLOCK${i}@@\n`;
    });

    const lines = text.split("\n");
    let html = "";
    let inUl = false, inOl = false, inP = false, inBq = false;

    const closeLists = () => {
      if (inUl) { html += "</ul>"; inUl = false; }
      if (inOl) { html += "</ol>"; inOl = false; }
    };
    const closeP = () => { if (inP) { html += "</p>"; inP = false; } };
    const closeBq = () => { if (inBq) { html += "</blockquote>"; inBq = false; } };

    const inline = (s) => {
      s = escapeHtml(s);
      s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
      s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
      s = s.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
      return s;
    };

    for (const line of lines) {
      const blockMatch = line.match(/^@@BLOCK(\d+)@@$/);
      if (blockMatch) {
        closeP(); closeLists(); closeBq();
        const b = blocks[+blockMatch[1]];
        html += renderCodeBlock(b.lang, b.code);
        continue;
      }
      if (/^\s*$/.test(line)) {
        closeP(); closeLists(); closeBq();
        continue;
      }
      const h = line.match(/^(#{1,3})\s+(.+)$/);
      if (h) {
        closeP(); closeLists(); closeBq();
        const n = h[1].length;
        html += `<h${n}>${inline(h[2])}</h${n}>`;
        continue;
      }
      if (/^>\s?/.test(line)) {
        closeP(); closeLists();
        if (!inBq) { html += "<blockquote>"; inBq = true; }
        html += `<p>${inline(line.replace(/^>\s?/, ""))}</p>`;
        continue;
      }
      closeBq();
      const ul = line.match(/^\s*[-*]\s+(.+)$/);
      if (ul) {
        closeP();
        if (inOl) { html += "</ol>"; inOl = false; }
        if (!inUl) { html += "<ul>"; inUl = true; }
        html += `<li>${inline(ul[1])}</li>`;
        continue;
      }
      const ol = line.match(/^\s*\d+\.\s+(.+)$/);
      if (ol) {
        closeP();
        if (inUl) { html += "</ul>"; inUl = false; }
        if (!inOl) { html += "<ol>"; inOl = true; }
        html += `<li>${inline(ol[1])}</li>`;
        continue;
      }
      closeLists();
      if (!inP) { html += "<p>"; inP = true; }
      else html += "<br>";
      html += inline(line);
    }
    closeP(); closeLists(); closeBq();
    return html;
  }

  function renderCodeBlock(lang, code) {
    const id = uid();
    const safeLang = escapeHtml(lang || "text");
    const safeCode = escapeHtml(code.replace(/\n$/, ""));
    return (
      `<pre data-lang="${safeLang}" data-code-id="${id}">` +
      `<div class="code-header"><span>${safeLang}</span>` +
      `<div class="actions">` +
      (isArtifactLang(lang) ? `<button type="button" data-action="artifact" data-code-id="${id}">Open artifact</button>` : "") +
      `<button type="button" data-action="copy" data-code-id="${id}">Copy</button>` +
      `</div></div>` +
      `<code id="code-${id}">${safeCode}</code></pre>`
    );
  }

  function isArtifactLang(lang) {
    const l = (lang || "").toLowerCase();
    return ["html", "htm", "svg", "markdown", "md", "javascript", "js", "css", "python", "py", "json", "tsx", "jsx", "typescript", "ts", "xml", "mermaid"].includes(l);
  }

  function md(src) {
    if (window.marked) {
      try {
        window.marked.setOptions({
          gfm: true,
          breaks: false,
        });
        // Custom renderer for code blocks with headers (marked v9+ uses token object)
        const renderer = new window.marked.Renderer();
        renderer.code = (codeOrToken, infostring) => {
          let lang = "";
          let code = "";
          if (codeOrToken && typeof codeOrToken === "object") {
            code = codeOrToken.text || codeOrToken.raw || "";
            lang = (codeOrToken.lang || "").trim().split(/\s+/)[0] || "";
          } else {
            code = String(codeOrToken ?? "");
            lang = String(infostring || "").trim().split(/\s+/)[0] || "";
          }
          return renderCodeBlock(lang, code);
        };
        return window.marked.parse(src || "", { renderer });
      } catch {
        return simpleMarkdown(src);
      }
    }
    return simpleMarkdown(src);
  }

  // ---------- Render ----------
  function render() {
    renderChatList();
    renderMessages();
    updateStatus();
    updateModelLabel();
  }

  function renderChatList(filter = "") {
    const list = $("#chat-list");
    const q = filter.trim().toLowerCase();
    const chats = state.chats.filter((c) => !q || c.title.toLowerCase().includes(q));
    if (!chats.length) {
      list.innerHTML = `<div style="padding:12px 10px;color:var(--text-3);font-size:0.88rem;">No chats yet</div>`;
      return;
    }
    list.innerHTML = chats
      .map(
        (c) => `
      <div class="chat-item ${c.id === state.activeId ? "active" : ""}" data-id="${c.id}" role="listitem">
        <button type="button" class="title" data-open="${c.id}">${escapeHtml(c.title)}</button>
        <button type="button" class="menu-btn" data-menu="${c.id}" aria-label="Chat menu">⋯</button>
      </div>`
      )
      .join("");
  }

  function renderMessages() {
    const chat = activeChat();
    const box = $("#messages");
    const empty = $("#empty-state");

    if (!chat || !chat.messages.length) {
      box.innerHTML = "";
      empty.hidden = false;
      return;
    }
    empty.hidden = true;

    box.innerHTML = chat.messages
      .map((m, idx) => {
        if (m.role === "user") {
          return `
          <div class="msg user" data-idx="${idx}">
            <div class="avatar">You</div>
            <div class="body">
              <div class="meta">You</div>
              <div class="content">${escapeHtml(m.content)}</div>
            </div>
          </div>`;
        }
        const streaming = m.streaming ? "streaming" : "";
        const cursor = m.streaming ? " streaming-cursor" : "";
        return `
        <div class="msg assistant ${streaming}" data-idx="${idx}">
          <div class="avatar">A</div>
          <div class="body">
            <div class="meta">Aurora</div>
            <div class="content${cursor}">${md(m.content || "")}</div>
            <div class="msg-actions">
              <button type="button" data-copy-msg="${idx}">Copy</button>
              ${!m.streaming ? `<button type="button" data-retry="${idx}">Retry</button>` : ""}
            </div>
          </div>
        </div>`;
      })
      .join("");

    // Scroll to bottom
    requestAnimationFrame(() => {
      box.scrollTop = box.scrollHeight;
    });
  }

  function patchLastAssistant(content, streaming = true) {
    const chat = activeChat();
    if (!chat) return;
    const last = chat.messages[chat.messages.length - 1];
    if (!last || last.role !== "assistant") return;
    last.content = content;
    last.streaming = streaming;

    // Efficient update of last message content
    const nodes = $$(".msg.assistant");
    const node = nodes[nodes.length - 1];
    if (node) {
      const contentEl = $(".content", node);
      if (contentEl) {
        contentEl.classList.toggle("streaming-cursor", streaming);
        contentEl.innerHTML = md(content);
      }
      node.classList.toggle("streaming", streaming);
      const box = $("#messages");
      box.scrollTop = box.scrollHeight;
    } else {
      renderMessages();
    }
  }

  function updateModelLabel() {
    if (state.selectedModel === "auto") {
      $("#model-label").textContent = "⚡ Auto";
      return;
    }
    const models = allModels();
    const found = models.find((m) => m.id === state.selectedModel);
    $("#model-label").textContent = found ? found.label : state.selectedModel;
  }

  function allModels() {
    if (!state.providers) {
      return [
        { id: "anthropic/claude-3.5-sonnet", label: "Claude 3.5 Sonnet" },
        { id: "openai/gpt-4o", label: "GPT-4o" },
        { id: "llama-3.3-70b-versatile", label: "Llama 3.3 70B" },
      ];
    }
    const out = [];
    for (const [key, p] of Object.entries(state.providers)) {
      for (const m of p.models || []) {
        out.push({ ...m, provider: key, providerName: p.name });
      }
    }
    return out;
  }

  async function updateStatus() {
    const dot = $("#status-dot");
    const text = $("#status-text");
    try {
      const s = state.settings || (await apiGet("/api/settings"));
      state.settings = s;
      if (s.api_key_set) {
        dot.className = "status-dot online";
        const prof = s.active_profile ? ` · ${s.active_profile}` : "";
        const mode = state.orchMode ? ` · orch:${state.pipeline || "auto"}` : (state.debateMode ? " · debate" : (state.agentMode ? " · agent" : ""));
        const routes = s.routes_available != null ? ` · ${s.routes_available} routes` : "";
        const mcp = (s.mcp && s.mcp.connected) ? ` · mcp:${s.mcp.connected}` : "";
        text.textContent = `Connected${prof}${routes}${mcp} · ${state.selectedModel}${mode}`;
      } else {
        dot.className = "status-dot offline";
        text.textContent = "Demo mode — add API key in Settings";
      }
    } catch {
      dot.className = "status-dot error";
      text.textContent = "Backend offline — open via server";
    }
  }

  // ---------- API ----------
  async function apiGet(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async function apiPost(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      let detail = await r.text();
      try { detail = JSON.parse(detail); } catch {}
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return r.json();
  }

  // ---------- Activity / agent UI ----------
  function _clearModeButtons() {
    ["btn-agent", "btn-debate", "btn-orch"].forEach((id) => {
      const b = document.getElementById(id);
      if (b) b.setAttribute("aria-pressed", "false");
    });
    const wrap = $("#orch-pipeline-wrap");
    if (wrap) wrap.hidden = true;
  }

  function setAgentMode(on) {
    state.agentMode = !!on;
    if (on) { state.debateMode = false; state.orchMode = false; }
    localStorage.setItem("aurora.agent", on ? "1" : "0");
    if (on) {
      localStorage.setItem("aurora.debate", "0");
      localStorage.setItem("aurora.orch", "0");
      _clearModeButtons();
    }
    const btn = $("#btn-agent");
    if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");
    updateStatus();
  }

  function setDebateMode(on) {
    state.debateMode = !!on;
    if (on) { state.agentMode = false; state.orchMode = false; }
    localStorage.setItem("aurora.debate", on ? "1" : "0");
    if (on) {
      localStorage.setItem("aurora.agent", "0");
      localStorage.setItem("aurora.orch", "0");
      _clearModeButtons();
    }
    const btn = $("#btn-debate");
    if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");
    updateStatus();
  }

  function setOrchMode(on) {
    state.orchMode = !!on;
    if (on) { state.agentMode = false; state.debateMode = false; }
    localStorage.setItem("aurora.orch", on ? "1" : "0");
    if (on) {
      localStorage.setItem("aurora.agent", "0");
      localStorage.setItem("aurora.debate", "0");
      _clearModeButtons();
    }
    const btn = $("#btn-orch");
    if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");
    const wrap = $("#orch-pipeline-wrap");
    if (wrap) wrap.hidden = !on;
    const sel = $("#orch-pipeline");
    if (sel) sel.value = state.pipeline || "auto";
    updateStatus();
  }

  function clearActivity() {
    state.activity = [];
    const el = $("#activity");
    if (el) { el.innerHTML = ""; el.hidden = true; }
  }

  function pushActivity(kind, text) {
    const el = $("#activity");
    if (!el) return;
    el.hidden = false;
    const line = document.createElement("div");
    line.className = `activity-line ${kind || ""}`;
    const tag = kind === "tool" ? "tool" : kind === "fail" ? "fail" : kind === "ok" ? "route" : "info";
    line.innerHTML = `<span class="tag">${tag}</span><span>${escapeHtml(text)}</span>`;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
    state.activity.push({ kind, text });
  }

  // ---------- Chat send / stream ----------
  async function sendMessage(text) {
    text = (text || "").trim();
    if (!text || state.streaming) return;

    let chat = activeChat();
    if (!chat) chat = createChat();

    // Attach any pending file context
    const pending = sendMessage._attach;
    if (pending) {
      text = `${text}\n\n---\nAttached file \`${pending.name}\`:\n\`\`\`\n${pending.content.slice(0, 40000)}\n\`\`\``;
      sendMessage._attach = null;
    }

    chat.messages.push({ role: "user", content: text });
    autoTitle(chat, text);
    chat.model = state.selectedModel;
    chat.updatedAt = now();

    chat.messages.push({ role: "assistant", content: "", streaming: true });
    saveChats();
    render();
    setStreaming(true);

    const payloadMessages = chat.messages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .slice(0, -1) // exclude empty streaming assistant
      .map((m) => ({ role: m.role, content: m.content }));

    const controller = new AbortController();
    state.abort = controller;

    clearActivity();
    if (state.orchMode) pushActivity("orch", `Orchestration pipeline: ${state.pipeline || "auto"}`);
    else if (state.debateMode) pushActivity("debate", "Debate mode — 3 models in parallel, then merge");
    else if (state.agentMode) pushActivity("ok", "Agent mode enabled — tools + multi-step loop");
    if (state.selectedModel === "auto") pushActivity("ok", "Auto-routing models (failover across keys)");

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: payloadMessages,
          model: state.selectedModel,
          stream: true,
          agent_mode: !!state.agentMode && !state.debateMode && !state.orchMode,
          debate_mode: !!state.debateMode && !state.orchMode,
          debate_panel: 3,
          orchestrate: !!state.orchMode,
          pipeline: state.pipeline || "auto",
          auto_route: state.selectedModel === "auto" || state.selectedModel === "⚡ Auto (best + failover)",
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(errText || `HTTP ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let full = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n");
        buffer = parts.pop() || "";

        for (const line of parts) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data:")) continue;
          const data = trimmed.slice(5).trim();
          if (data === "[DONE]") continue;
          try {
            const json = JSON.parse(data);
            if (json.error) throw new Error(typeof json.error === "string" ? json.error : JSON.stringify(json.error));

            // Aurora control events (routing / agent tools)
            if (json.aurora_event) {
              const ev = json.aurora_event;
              if (ev === "route_plan") {
                const names = (json.candidates || []).map((c) => c.label || c.model).slice(0, 4).join(" → ");
                pushActivity("ok", `Task: ${json.task || "chat"} · candidates: ${names || "…"}`);
              } else if (ev === "trying_route") {
                pushActivity("info", `Trying ${json.label || json.model} (${json.tier || ""})`);
              } else if (ev === "route_selected") {
                pushActivity("ok", `Using ${json.label || json.model}`);
                // reflect actual model in status
                if (json.model) {
                  // don't overwrite user selection permanently; show in status only
                  $("#status-text").textContent = `Live · ${json.model}${state.agentMode ? " · agent" : ""}`;
                }
              } else if (ev === "route_failed") {
                pushActivity("fail", json.error || "Route failed");
              } else if (ev === "agent_start") {
                pushActivity("ok", `Agent start · tools: ${(json.tools || []).slice(0,6).join(", ")}`);
              } else if (ev === "agent_step") {
                pushActivity("info", `Agent step ${json.step}/${json.max_steps}`);
              } else if (ev === "tool_call") {
                let args = json.arguments || "";
                if (args.length > 120) args = args.slice(0, 120) + "…";
                pushActivity("tool", `${json.name}(${args})`);
              } else if (ev === "tool_result") {
                pushActivity(json.ok ? "ok" : "fail", `${json.name} → ${json.preview || ""}`);
              } else if (ev === "agent_done") {
                pushActivity("ok", `Agent done in ${json.steps} step(s) · ${json.model || ""}`);
              } else if (ev === "debate_start") {
                const names = (json.panel || []).map((p) => p.label || p.model).join(", ");
                pushActivity("debate", `Panel: ${names || "…"}`);
              } else if (ev === "debate_panelist") {
                if (json.ok) pushActivity("ok", `${json.label}: ${json.preview || (json.chars + " chars")}`);
                else pushActivity("fail", `${json.label} failed: ${json.error || ""}`);
              } else if (ev === "debate_synthesize") {
                pushActivity("debate", `Synthesizing ${json.successes}/${json.panel_size} answers…`);
              } else if (ev === "debate_done") {
                pushActivity("ok", `Debate done · synthesizer: ${json.synthesizer || ""}`);
              } else if (ev === "orch_boot") {
                pushActivity("orch", `Boot pipeline ${json.pipeline || "auto"}`);
              } else if (ev === "orch_start") {
                pushActivity("orch", `${json.name || json.pipeline}: ${(json.steps_planned || []).map(s=>s.module).join(" → ")}`);
              } else if (ev === "orch_expanded") {
                pushActivity("orch", `Expanded steps: ${(json.steps || []).map(s=>s.module).join(" → ")}`);
              } else if (ev === "orch_parallel") {
                pushActivity("orch", `Parallel group ${json.group}: ${(json.steps || []).join(", ")}`);
              } else if (ev === "orch_step_start") {
                pushActivity("info", `▶ ${json.title || json.module}${json.use_tools ? " (tools)" : ""}`);
              } else if (ev === "orch_step_done") {
                if (json.ok) pushActivity("ok", `✓ ${json.title || json.module} · ${json.label || json.model || ""}`);
                else pushActivity("fail", `✗ ${json.title || json.module}: ${json.error || "failed"}`);
              } else if (ev === "orch_final") {
                pushActivity("orch", `Final from pipeline · ${json.modules_run || 0} modules`);
              } else if (ev === "orch_done" || ev === "orch_stream_done") {
                pushActivity("ok", `Orchestration complete (${json.pipeline || ""})`);
              }
              continue;
            }

            const delta =
              json.choices?.[0]?.delta?.content ??
              json.choices?.[0]?.message?.content ??
              json.content ??
              "";
            if (delta) {
              full += delta;
              patchLastAssistant(full, true);
            }
          } catch (e) {
            if (e.message && !e.message.includes("JSON")) throw e;
            // ignore partial JSON parse errors
          }
        }
      }

      if (!full) full = "_(No content returned. Check model id and API key.)_";
      patchLastAssistant(full, false);
      const last = chat.messages[chat.messages.length - 1];
      last.content = full;
      last.streaming = false;
      chat.updatedAt = now();
      saveChats();
      renderMessages();
      maybeAutoOpenArtifact(full);
    } catch (err) {
      if (err.name === "AbortError") {
        const last = chat.messages[chat.messages.length - 1];
        if (last && last.role === "assistant") {
          last.streaming = false;
          if (!last.content) last.content = "_(Generation stopped.)_";
        }
        saveChats();
        renderMessages();
      } else {
        const last = chat.messages[chat.messages.length - 1];
        if (last && last.role === "assistant") {
          last.streaming = false;
          last.content = `**Error:** ${err.message || String(err)}\n\nCheck Settings (API key, base URL, model).`;
        }
        saveChats();
        renderMessages();
        toast("Request failed");
      }
    } finally {
      setStreaming(false);
      state.abort = null;
      renderChatList($("#chat-search").value);
    }
  }

  function setStreaming(on) {
    state.streaming = !!on;
    const stopRow = $("#stop-row");
    if (stopRow) stopRow.hidden = !on;
    const send = $("#btn-send");
    const input = $("#input");
    if (send) send.disabled = on || !(input && input.value.trim());
    // Keep input usable so UI never feels fully frozen; only block double-send via state.streaming
    if (input) input.disabled = false;
    if (!on) {
      state.abort = null;
      clearTimeout(setStreaming._safetyTimer);
    } else {
      // Safety unlock after 3 minutes if something hangs
      clearTimeout(setStreaming._safetyTimer);
      setStreaming._safetyTimer = setTimeout(() => {
        if (state.streaming) {
          try { state.abort?.abort(); } catch {}
          setStreaming(false);
          pushActivity("fail", "Stopped: response timed out / stuck");
          toast("Unlocked — previous reply got stuck");
          const chat = activeChat();
          const last = chat?.messages?.[chat.messages.length - 1];
          if (last && last.role === "assistant" && last.streaming) {
            last.streaming = false;
            if (!last.content) last.content = "_(Stopped — connection stuck. Click Retry or send again.)_";
            saveChats();
            renderMessages();
          }
        }
      }, 180000);
    }
  }

  function forceUnlockUI() {
    try { state.abort?.abort(); } catch {}
    state.abort = null;
    setStreaming(false);
    // clear any assistant streaming flags
    for (const c of state.chats) {
      for (const m of c.messages || []) {
        if (m.streaming) m.streaming = false;
      }
    }
    saveChats();
    renderMessages();
    const backdrop = $("#sidebar-backdrop");
    if (backdrop) backdrop.hidden = true;
    const sidebar = $("#sidebar");
    if (sidebar) sidebar.classList.remove("open");
    // close blocking modals
    const sm = $("#settings-modal");
    if (sm) sm.hidden = true;
    const rm = $("#rename-modal");
    if (rm) rm.hidden = true;
    toast("UI unlocked");
  }

  function maybeAutoOpenArtifact(text) {
    // Auto-open first substantial html/svg/md block
    const re = /```(html|htm|svg|markdown|md)\n([\s\S]*?)```/i;
    const m = text.match(re);
    if (m && m[2].length > 80) {
      openArtifact(m[1].toLowerCase(), m[2]);
    }
  }

  // ---------- Artifacts ----------
  function openArtifact(lang, code) {
    state.artifact = { lang, code };
    const panel = $("#artifacts");
    panel.hidden = false;
    $("#artifact-name").textContent = artifactTitle(lang);
    showArtifactView($(".seg-btn.active")?.dataset.view || "preview");
  }

  function artifactTitle(lang) {
    const map = {
      html: "HTML preview",
      htm: "HTML preview",
      svg: "SVG",
      md: "Document",
      markdown: "Document",
      js: "JavaScript",
      javascript: "JavaScript",
      css: "CSS",
      py: "Python",
      python: "Python",
      json: "JSON",
    };
    return map[(lang || "").toLowerCase()] || "Artifact";
  }

  function showArtifactView(view) {
    $$(".seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    const preview = $("#artifact-preview");
    const codeEl = $("#artifact-code");
    if (!state.artifact) return;
    const { lang, code } = state.artifact;

    if (view === "code") {
      preview.hidden = true;
      codeEl.hidden = false;
      codeEl.querySelector("code").textContent = code;
      return;
    }

    codeEl.hidden = true;
    preview.hidden = false;
    preview.innerHTML = "";
    const l = (lang || "").toLowerCase();

    if (l === "html" || l === "htm" || l === "svg") {
      const iframe = document.createElement("iframe");
      iframe.sandbox = "allow-scripts allow-forms allow-modals";
      iframe.srcdoc = l === "svg" && !code.trim().startsWith("<svg") ? code : code;
      if (l === "svg" && code.includes("<svg")) {
        iframe.srcdoc = `<!DOCTYPE html><html><body style="margin:0;display:grid;place-items:center;min-height:100vh;background:#fff">${code}</body></html>`;
      }
      preview.appendChild(iframe);
    } else if (l === "md" || l === "markdown") {
      const div = document.createElement("div");
      div.className = "md-preview";
      div.innerHTML = md(code);
      preview.appendChild(div);
    } else {
      const pre = document.createElement("pre");
      pre.className = "artifact-code";
      pre.style.display = "block";
      pre.style.height = "100%";
      pre.textContent = code;
      preview.appendChild(pre);
    }
  }

  function closeArtifact() {
    $("#artifacts").hidden = true;
    state.artifact = null;
  }

  // ---------- Settings UI ----------
  async function openSettings() {
    try {
      state.providers = state.providers || (await apiGet("/api/providers"));
      state.settings = await apiGet("/api/settings");
      try { state.profiles = await apiGet("/api/profiles"); } catch { state.profiles = null; }
    } catch (e) {
      toast("Could not load settings — is the server running?");
      return;
    }
    const s = state.settings;
    const base = s.api_base || "";
    let provider = "custom";
    if (base.includes("openrouter")) provider = "openrouter";
    else if (base.includes("nvidia.com") || base.includes("integrate.api.nvidia")) provider = "nvidia";
    else if (base.includes("api.openai.com")) provider = "openai";
    else if (base.includes("groq")) provider = "groq";

    const profileEl = $("#set-profile");
    if (profileEl) {
      profileEl.value = s.active_profile || "openrouter";
    }
    $("#set-provider").value = provider;
    $("#set-api-base").value = base;
    $("#set-api-key").value = "";
    $("#key-status").textContent = s.api_key_set ? `(set: ${s.api_key_masked})` : "(not set)";
    $("#set-temp").value = s.temperature ?? 0.7;
    $("#temp-val").textContent = String(s.temperature ?? 0.7);
    $("#set-max-tokens").value = s.max_tokens ?? 1024;
    $("#set-system").value = s.system_prompt || "";
    const ar = $("#set-auto-route");
    if (ar) ar.checked = s.auto_route !== false;
    const pf = $("#set-prefer-free");
    if (pf) pf.checked = s.prefer_free_on_fail !== false;
    const as = $("#set-agent-steps");
    if (as) as.value = s.agent_max_steps ?? 8;
    fillModelSelect(provider, s.default_model || state.selectedModel);
    // ensure Auto option exists
    const sel = $("#set-model");
    if (sel && ![...sel.options].some((o) => o.value === "auto")) {
      const opt = document.createElement("option");
      opt.value = "auto";
      opt.textContent = "⚡ Auto (best + failover)";
      sel.prepend(opt);
    }
    $("#settings-modal").hidden = false;
  }

  function fillModelSelect(provider, selected) {
    const sel = $("#set-model");
    const p = state.providers?.[provider];
    const models = p?.models || [{ id: selected, label: selected }];
    sel.innerHTML = models.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)} (${escapeHtml(m.id)})</option>`).join("");
    // Ensure selected exists
    if (selected && ![...sel.options].some((o) => o.value === selected)) {
      const opt = document.createElement("option");
      opt.value = selected;
      opt.textContent = selected;
      sel.prepend(opt);
    }
    sel.value = selected;
  }

  async function saveSettings() {
    const body = {
      api_base: $("#set-api-base").value.trim(),
      default_model: $("#set-model").value,
      temperature: parseFloat($("#set-temp").value),
      max_tokens: parseInt($("#set-max-tokens").value, 10),
      system_prompt: $("#set-system").value,
    };
    const ar = $("#set-auto-route");
    if (ar) body.auto_route = !!ar.checked;
    const pf = $("#set-prefer-free");
    if (pf) body.prefer_free_on_fail = !!pf.checked;
    const as = $("#set-agent-steps");
    if (as) body.agent_max_steps = parseInt(as.value, 10) || 8;
    const profileEl = $("#set-profile");
    if (profileEl && profileEl.value) body.active_profile = profileEl.value;
    const key = $("#set-api-key").value.trim();
    if (key) body.api_key = key;

    try {
      state.settings = await apiPost("/api/settings", body);
      state.selectedModel = body.default_model;
      localStorage.setItem(MODEL_KEY, state.selectedModel);
      $("#settings-modal").hidden = true;
      toast("Settings saved");
      updateStatus();
      updateModelLabel();
    } catch (e) {
      toast("Save failed: " + e.message);
    }
  }

  // ---------- Export ----------
  function exportChat(chat) {
    if (!chat) return;
    const lines = [`# ${chat.title}`, "", `Model: ${chat.model || ""}`, `Updated: ${new Date(chat.updatedAt).toISOString()}`, ""];
    for (const m of chat.messages) {
      lines.push(`## ${m.role === "user" ? "You" : "Aurora"}`, "", m.content || "", "");
    }
    download(`${slug(chat.title)}.md`, lines.join("\n"));
  }

  function exportAll() {
    const blob = JSON.stringify(state.chats, null, 2);
    download(`aurora-chats-${new Date().toISOString().slice(0, 10)}.json`, blob, "application/json");
  }

  function download(name, content, type = "text/markdown") {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([content], { type }));
    a.download = name;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function slug(s) {
    return (s || "chat").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "chat";
  }

  // ---------- Theme ----------
  function applyTheme(theme) {
    document.body.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
    const meta = document.querySelector('meta[name="theme-color"]:not([media]), meta[name="theme-color"]');
    const color = theme === "light" ? "#f4f6ff" : "#0b1020";
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => {
      if (!m.media || window.matchMedia(m.media).matches) m.setAttribute("content", color);
    });
    // also set a generic one
    let g = document.querySelector('meta[name="theme-color"][data-dynamic]');
    if (!g) {
      g = document.createElement("meta");
      g.setAttribute("name", "theme-color");
      g.setAttribute("data-dynamic", "1");
      document.head.appendChild(g);
    }
    g.setAttribute("content", color);
  }

  // ---------- Model menu ----------
  function openModelMenu() {
    const menu = $("#model-menu");
    const models = allModels();
    let html = `<div class="group-label">Router</div>
      <button type="button" data-model="auto" class="${state.selectedModel === "auto" ? "active" : ""}">
        <span>⚡ Auto (best + failover)</span>
      </button>`;
    let lastProv = null;
    for (const m of models) {
      if (m.providerName && m.providerName !== lastProv) {
        lastProv = m.providerName;
        html += `<div class="group-label">${escapeHtml(lastProv)}</div>`;
      }
      html += `<button type="button" data-model="${escapeHtml(m.id)}" class="${m.id === state.selectedModel ? "active" : ""}">
        <span>${escapeHtml(m.label)}</span>
      </button>`;
    }
    menu.innerHTML = html || `<div class="group-label">No models</div>`;
    menu.hidden = false;
  }

  // ---------- Events ----------

  // ---------- MCP connectors ----------
  async function loadMcpCatalog(filter = "") {
    const el = $("#mcp-catalog");
    if (!el) return;
    try {
      const data = await apiGet("/api/mcp/catalog");
      const q = (filter || "").toLowerCase().trim();
      let items = data.items || [];
      if (q) items = items.filter((x) =>
        (x.name || "").toLowerCase().includes(q) ||
        (x.description || "").toLowerCase().includes(q) ||
        (x.category || "").toLowerCase().includes(q) ||
        (x.id || "").toLowerCase().includes(q)
      );
      // sort priority
      items = items.slice().sort((a,b) => (a.priority||9) - (b.priority||9) || (a.name||"").localeCompare(b.name||""));
      el.innerHTML = items.slice(0, 50).map((p) => `
        <div class="mcp-cat-item" data-id="${escapeHtml(p.id)}">
          <div>
            <div class="n">${escapeHtml(p.name)}</div>
            <div class="c">${escapeHtml(p.category || "MCP")}${p.free_local ? " · free local" : ""}</div>
            <div class="d">${escapeHtml(p.description || "")}</div>
          </div>
          <button type="button" data-catalog-add="${escapeHtml(p.id)}">Add</button>
        </div>`).join("") || `<div class="help-text">No matches</div>`;
    } catch (e) {
      el.innerHTML = `<div class="help-text">Catalog unavailable</div>`;
    }
  }

  async function installFreeMcps() {
    toast("Installing free open-source MCP presets…");
    try {
      const r = await apiPost("/api/mcp/install-free?max_count=50&connect_core=true", {});
      // apiPost may not support query; fallback fetch
      toast(`Added ${r.added_count || (r.added || []).length || 0} MCP servers`);
      await loadMcpPanel();
      await loadMcpCatalog($("#mcp-catalog-filter")?.value || "");
      updateStatus();
    } catch (e) {
      // try explicit URL
      try {
        const res = await fetch("/api/mcp/install-free?max_count=50&connect_core=true", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        const r = await res.json();
        toast(`Added ${r.added_count || (r.added || []).length || 0} MCP servers`);
        await loadMcpPanel();
        await loadMcpCatalog($("#mcp-catalog-filter")?.value || "");
        updateStatus();
      } catch (e2) {
        toast("Install failed: " + (e.message || e));
      }
    }
  }

  async function loadMcpPanel() {
    const list = $("#mcp-list");
    const presetsEl = $("#mcp-presets");
    const status = $("#mcp-status");
    if (!list) return;
    try {
      const [serversRes, presetsRes] = await Promise.all([
        apiGet("/api/mcp/servers"),
        apiGet("/api/mcp/presets"),
      ]);
      const servers = serversRes.servers || [];
      const st = serversRes.status || {};
      if (status) status.textContent = `${st.connected || 0}/${st.configured || 0} connected · ${st.tool_count || 0} tools`;
      if (presetsEl) {
        presetsEl.innerHTML = (presetsRes.presets || []).map((p) =>
          `<button type="button" data-preset="${escapeHtml(p.id)}" title="${escapeHtml(p.description || "")}">+ ${escapeHtml(p.name)}</button>`
        ).join("");
      }
      if (!servers.length) {
        list.innerHTML = `<div class="help-text">No MCP servers yet. Add the <strong>Aurora Demo MCP</strong> preset to try tools instantly.</div>`;
      } else {
        list.innerHTML = servers.map((s) => {
          const dot = s.connected ? "on" : (s.live_error ? "err" : "");
          const tools = (s.tools || []).slice(0, 8).join(", ") || "—";
          const err = s.live_error ? `<div class="meta" style="color:var(--danger)">${escapeHtml(s.live_error).slice(0,160)}</div>` : "";
          return `<div class="mcp-card" data-id="${escapeHtml(s.id)}">
            <div class="row">
              <div>
                <div class="title"><span class="dot ${dot}"></span>${escapeHtml(s.name || s.id)}</div>
                <div class="meta">${escapeHtml(s.transport)} · ${s.tool_count || 0} tools · ${s.enabled === false ? "disabled" : "enabled"}${s.auto_connect ? " · auto" : ""}</div>
                ${err}
                <div class="tools">${escapeHtml(tools)}</div>
              </div>
              <div class="actions">
                ${s.connected
                  ? `<button type="button" data-mcp-act="disconnect" data-id="${escapeHtml(s.id)}">Disconnect</button>`
                  : `<button type="button" class="primary" data-mcp-act="connect" data-id="${escapeHtml(s.id)}">Connect</button>`}
                <button type="button" data-mcp-act="toggle" data-id="${escapeHtml(s.id)}">${s.enabled === false ? "Enable" : "Disable"}</button>
                <button type="button" class="danger" data-mcp-act="delete" data-id="${escapeHtml(s.id)}">Delete</button>
              </div>
            </div>
          </div>`;
        }).join("");
      }
    } catch (e) {
      if (list) list.innerHTML = `<div class="help-text">MCP panel unavailable: ${escapeHtml(e.message || e)}</div>`;
    }
  }

  async function mcpAddPreset(id) {
    await apiPost(`/api/mcp/presets/${encodeURIComponent(id)}`, {});
    toast(`Added preset: ${id}`);
    await loadMcpPanel();
  }

  async function mcpConnect(id) {
    toast(`Connecting ${id}…`);
    try {
      const r = await apiPost(`/api/mcp/servers/${encodeURIComponent(id)}/connect`, {});
      if (r.error) toast(r.error);
      else toast(`Connected · ${r.tool_count || 0} tools`);
    } catch (e) {
      toast(`Connect failed: ${e.message || e}`);
    }
    await loadMcpPanel();
    updateStatus();
  }

  async function mcpDisconnect(id) {
    await apiPost(`/api/mcp/servers/${encodeURIComponent(id)}/disconnect`, {});
    toast("Disconnected");
    await loadMcpPanel();
    updateStatus();
  }

  async function mcpDelete(id) {
    if (!confirm(`Delete MCP server ${id}?`)) return;
    await fetch(`/api/mcp/servers/${encodeURIComponent(id)}`, { method: "DELETE" });
    toast("Deleted");
    await loadMcpPanel();
  }

  async function mcpToggle(id) {
    // fetch current raw via list then upsert enabled flip
    const data = await apiGet("/api/mcp/servers");
    const s = (data.servers || []).find((x) => x.id === id);
    if (!s) return;
    await apiPost("/api/mcp/servers", {
      id: s.id,
      name: s.name,
      transport: s.transport,
      enabled: s.enabled === false,
      auto_connect: !!s.auto_connect,
      command: s.command,
      args: s.args,
      url: s.url,
      description: s.description,
    });
    await loadMcpPanel();
  }

  async function mcpAddCustom() {
    const body = {
      name: $("#mcp-name")?.value?.trim() || "custom",
      transport: $("#mcp-transport")?.value || "stdio",
      command: $("#mcp-command")?.value?.trim() || "",
      args: $("#mcp-args")?.value?.trim() || "",
      url: $("#mcp-url")?.value?.trim() || "",
      auto_connect: !!$("#mcp-auto")?.checked,
      enabled: true,
    };
    await apiPost("/api/mcp/servers", body);
    toast("MCP server saved");
    ["mcp-name","mcp-command","mcp-args","mcp-url"].forEach((id) => { const el = document.getElementById(id); if (el) el.value = ""; });
    await loadMcpPanel();
  }


  function bindEvents() {
    $("#btn-new-chat").addEventListener("click", () => {
      createChat();
      closeArtifact();
      render();
      $("#input").focus();
      closeSidebarMobile();
    });

    $("#chat-list").addEventListener("click", (e) => {
      const open = e.target.closest("[data-open]");
      if (open) {
        state.activeId = open.dataset.open;
        saveChats();
        closeArtifact();
        render();
        closeSidebarMobile();
        return;
      }
      const menuBtn = e.target.closest("[data-menu]");
      if (menuBtn) {
        e.stopPropagation();
        showItemMenu(menuBtn);
      }
      const action = e.target.closest("[data-action]");
      if (action) {
        const id = action.dataset.id;
        if (action.dataset.action === "rename") {
          state.renameId = id;
          const c = state.chats.find((x) => x.id === id);
          $("#rename-input").value = c?.title || "";
          $("#rename-modal").hidden = false;
          $("#rename-input").focus();
        } else if (action.dataset.action === "delete") {
          if (confirm("Delete this chat?")) deleteChat(id);
        } else if (action.dataset.action === "export") {
          exportChat(state.chats.find((x) => x.id === id));
        }
        closeItemMenus();
      }
    });

    $("#chat-search").addEventListener("input", (e) => renderChatList(e.target.value));

    $("#composer").addEventListener("submit", (e) => {
      e.preventDefault();
      sendMessage($("#input").value);
      $("#input").value = "";
      autoResize();
      $("#btn-send").disabled = true;
    });

    const input = $("#input");
    input.addEventListener("input", () => {
      autoResize();
      $("#btn-send").disabled = state.streaming || !input.value.trim();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (!state.streaming && input.value.trim()) {
          $("#composer").requestSubmit();
        }
      }
    });

    $("#btn-stop").addEventListener("click", () => {
      try { state.abort?.abort(); } catch {}
      // Always unlock UI even if abort missing
      setTimeout(() => {
        if (state.streaming) forceUnlockUI();
      }, 100);
    });

    $("#suggestions").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-prompt]");
      if (!btn) return;
      sendMessage(btn.dataset.prompt);
    });

    $("#btn-settings").addEventListener("click", openSettings);
    $("#btn-save-settings").addEventListener("click", saveSettings);
    $("#set-provider").addEventListener("change", (e) => {
      const p = state.providers?.[e.target.value];
      if (p) {
        $("#set-api-base").value = p.base;
        fillModelSelect(e.target.value, p.models?.[0]?.id);
      }
    });
    const profileSelect = $("#set-profile");
    if (profileSelect) {
      profileSelect.addEventListener("change", async (e) => {
        const pid = e.target.value;
        // Apply known profile defaults from server
        try {
          const data = state.profiles || (await apiGet("/api/profiles"));
          state.profiles = data;
          const prof = (data.profiles || []).find((x) => x.id === pid);
          if (prof) {
            if (prof.api_base) $("#set-api-base").value = prof.api_base;
            const provider = prof.provider || "openrouter";
            $("#set-provider").value = provider in (state.providers || {}) ? provider : "custom";
            fillModelSelect($("#set-provider").value, prof.default_model || state.selectedModel);
            $("#key-status").textContent = prof.configured ? `(set: ${prof.api_key_masked})` : "(not set — enter key)";
          } else if (pid === "custom") {
            $("#set-provider").value = "custom";
          }
        } catch {}
      });
    }
    $("#set-temp").addEventListener("input", (e) => {
      $("#temp-val").textContent = e.target.value;
    });

    $$("[data-close]").forEach((el) => {
      el.addEventListener("click", () => {
        const which = el.dataset.close;
        if (which === "settings") $("#settings-modal").hidden = true;
        if (which === "rename") $("#rename-modal").hidden = true;
      });
    });

    $("#btn-rename-save").addEventListener("click", () => {
      if (state.renameId) renameChat(state.renameId, $("#rename-input").value);
      $("#rename-modal").hidden = true;
    });

    $("#btn-theme").addEventListener("click", () => {
      const next = document.body.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(next);
    });
    // Emergency unlock: double-click brand
    const brand = document.querySelector(".brand-pill, .empty-logo, .brand-name");
    if (brand) brand.addEventListener("dblclick", forceUnlockUI);
    const agentBtn = $("#btn-agent");
    if (agentBtn) {
      agentBtn.addEventListener("click", () => {
        setAgentMode(!state.agentMode);
        toast(state.agentMode ? "Agent mode ON" : "Agent mode OFF");
      });
    }
    const debateBtn = $("#btn-debate");
    if (debateBtn) {
      debateBtn.addEventListener("click", () => {
        setDebateMode(!state.debateMode);
        toast(state.debateMode ? "Debate mode ON (3 models → merge)" : "Debate mode OFF");
      });
    }
    
    const mcpPresets = $("#mcp-presets");
    if (mcpPresets) {
      mcpPresets.addEventListener("click", (e) => {
        const b = e.target.closest("[data-preset]");
        if (b) mcpAddPreset(b.dataset.preset);
      });
    }
    const mcpList = $("#mcp-list");
    if (mcpList) {
      mcpList.addEventListener("click", (e) => {
        const b = e.target.closest("[data-mcp-act]");
        if (!b) return;
        const id = b.dataset.id;
        const act = b.dataset.mcpAct;
        if (act === "connect") mcpConnect(id);
        else if (act === "disconnect") mcpDisconnect(id);
        else if (act === "delete") mcpDelete(id);
        else if (act === "toggle") mcpToggle(id);
      });
    }
    const mcpAdd = $("#btn-mcp-add");
    if (mcpAdd) mcpAdd.addEventListener("click", mcpAddCustom);
    const mcpInstall = $("#btn-mcp-install-free");
    if (mcpInstall) mcpInstall.addEventListener("click", installFreeMcps);
    const mcpFilter = $("#mcp-catalog-filter");
    if (mcpFilter) mcpFilter.addEventListener("input", (e) => loadMcpCatalog(e.target.value));
    const mcpCat = $("#mcp-catalog");
    if (mcpCat) mcpCat.addEventListener("click", (e) => {
      const b = e.target.closest("[data-catalog-add]");
      if (b) mcpAddPreset(b.dataset.catalogAdd);
    });

    const orchBtn = $("#btn-orch");
    if (orchBtn) {
      orchBtn.addEventListener("click", () => {
        setOrchMode(!state.orchMode);
        toast(state.orchMode ? `Orchestration ON (${state.pipeline || "auto"})` : "Orchestration OFF");
      });
    }
    const pipeSel = $("#orch-pipeline");
    if (pipeSel) {
      pipeSel.value = state.pipeline || "auto";
      pipeSel.addEventListener("change", () => {
        state.pipeline = pipeSel.value || "auto";
        localStorage.setItem("aurora.pipeline", state.pipeline);
        toast(`Pipeline: ${state.pipeline}`);
        updateStatus();
      });
    }

    $("#btn-model").addEventListener("click", (e) => {
      e.stopPropagation();
      const menu = $("#model-menu");
      if (menu.hidden) openModelMenu();
      else menu.hidden = true;
    });

    $("#model-menu").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-model]");
      if (!btn) return;
      state.selectedModel = btn.dataset.model;
      localStorage.setItem(MODEL_KEY, state.selectedModel);
      const chat = activeChat();
      if (chat) chat.model = state.selectedModel;
      $("#model-menu").hidden = true;
      updateModelLabel();
      updateStatus();
      toast(`Model: ${state.selectedModel}`);
    });

    document.addEventListener("click", (e) => {
      if (!e.target.closest(".model-picker-wrap")) $("#model-menu").hidden = true;
      if (!e.target.closest(".chat-item")) closeItemMenus();
    });

    $("#messages").addEventListener("click", async (e) => {
      const copyMsg = e.target.closest("[data-copy-msg]");
      if (copyMsg) {
        const chat = activeChat();
        const m = chat?.messages[+copyMsg.dataset.copyMsg];
        if (m) {
          await navigator.clipboard.writeText(m.content || "");
          toast("Copied");
        }
        return;
      }
      const retry = e.target.closest("[data-retry]");
      if (retry) {
        const chat = activeChat();
        if (!chat || state.streaming) return;
        // Remove assistant message and resend last user
        let idx = +retry.dataset.retry;
        while (idx >= 0 && chat.messages[idx]?.role !== "user") idx--;
        if (idx < 0) return;
        const userText = chat.messages[idx].content;
        chat.messages = chat.messages.slice(0, idx);
        saveChats();
        sendMessage(userText);
        return;
      }
      const action = e.target.closest("[data-action]");
      if (!action) return;
      const id = action.dataset.codeId;
      const codeEl = document.getElementById(`code-${id}`);
      if (!codeEl) return;
      const code = codeEl.textContent;
      if (action.dataset.action === "copy") {
        await navigator.clipboard.writeText(code);
        toast("Code copied");
      } else if (action.dataset.action === "artifact") {
        const pre = codeEl.closest("pre");
        openArtifact(pre?.dataset.lang || "text", code);
      }
    });

    $("#btn-artifacts-toggle").addEventListener("click", () => {
      const panel = $("#artifacts");
      if (panel.hidden) {
        if (state.artifact) panel.hidden = false;
        else toast("Open a code block with “Open artifact”");
      } else closeArtifact();
    });
    $("#btn-close-artifact").addEventListener("click", closeArtifact);
    $("#btn-copy-artifact").addEventListener("click", async () => {
      if (state.artifact) {
        await navigator.clipboard.writeText(state.artifact.code);
        toast("Artifact copied");
      }
    });
    $$(".seg-btn").forEach((b) => {
      b.addEventListener("click", () => showArtifactView(b.dataset.view));
    });

    $("#btn-export-chat").addEventListener("click", () => exportChat(activeChat()));
    $("#btn-export-all").addEventListener("click", exportAll);

    $("#btn-attach").addEventListener("click", () => $("#file-input").click());
    $("#file-input").addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const content = await file.text();
      sendMessage._attach = { name: file.name, content };
      toast(`Attached ${file.name}`);
      e.target.value = "";
    });

    $("#btn-sidebar-open").addEventListener("click", () => {
      $("#sidebar").classList.add("open");
      $("#sidebar-backdrop").hidden = false;
    });
    $("#btn-sidebar-close").addEventListener("click", closeSidebarMobile);
    $("#sidebar-backdrop").addEventListener("click", closeSidebarMobile);

    // Escape closes modals
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        $("#settings-modal").hidden = true;
        $("#rename-modal").hidden = true;
        $("#model-menu").hidden = true;
        if (state.streaming) forceUnlockUI();
      }
    });
  }

  function closeSidebarMobile() {
    $("#sidebar").classList.remove("open");
    $("#sidebar-backdrop").hidden = true;
  }

  function showItemMenu(btn) {
    closeItemMenus();
    const id = btn.dataset.menu;
    const menu = document.createElement("div");
    menu.className = "chat-item-menu";
    menu.innerHTML = `
      <button type="button" data-action="rename" data-id="${id}">Rename</button>
      <button type="button" data-action="export" data-id="${id}">Export</button>
      <button type="button" data-action="delete" data-id="${id}" class="danger">Delete</button>
    `;
    btn.parentElement.appendChild(menu);
  }

  function closeItemMenus() {
    $$(".chat-item-menu").forEach((m) => m.remove());
  }

  function autoResize() {
    const el = $("#input");
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }

  // ---------- Boot ----------
  async function boot() {
    applyTheme(localStorage.getItem(THEME_KEY) || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
    loadChats();
    // Clear any stuck streaming flags from previous session
    for (const c of state.chats) {
      for (const m of (c.messages || [])) m.streaming = false;
    }
    // Migrate retired model id
    if (!state.selectedModel || state.selectedModel.includes("claude-3.5-sonnet")) {
      state.selectedModel = "auto";
      localStorage.setItem(MODEL_KEY, "auto");
    }
    if (!state.chats.length) {
      state.activeId = null;
    }
    state.streaming = false;
    state.abort = null;
    bindEvents();
    render();
    setStreaming(false);

    try {
      state.providers = await apiGet("/api/providers");
      state.settings = await apiGet("/api/settings");
      if (state.settings.default_model && !localStorage.getItem(MODEL_KEY)) {
        state.selectedModel = state.settings.default_model;
      }
    } catch {
      // offline static open
    }
    updateStatus();
    updateModelLabel();
    // restore modes (mutually exclusive)
    if (state.orchMode) setOrchMode(true);
    else if (state.debateMode) setDebateMode(true);
    else setAgentMode(state.agentMode);
    // load pipeline catalog if available
    apiGet("/api/pipelines").then((d) => {
      const sel = $("#orch-pipeline");
      if (!sel || !d.pipelines) return;
      const cur = state.pipeline || "auto";
      sel.innerHTML = d.pipelines.map((p) =>
        `<option value="${p.id}">${p.name}</option>`
      ).join("");
      sel.value = cur;
    }).catch(() => {});
    $("#input").focus();
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
  }

  boot();
})();
