"use strict";

(() => {
  const config = {
    csrf_token: document.querySelector('meta[name="csrf-token"]').content,
    api_port: document.querySelector('meta[name="api-port"]').content,
    version: document.querySelector('meta[name="app-version"]').content,
  };
  const $ = (id) => document.getElementById(id);
  let nodes = [];
  let currentNode = "";
  let chatBusy = false;
  let apiKeyVisible = false;
  let apiKey = "";
  let noticeTimer;

  function notify(message, error = false) {
    clearTimeout(noticeTimer);
    const notice = $("notice");
    notice.textContent = message;
    notice.classList.toggle("notice-error", error);
    notice.hidden = false;
    if (!error) noticeTimer = setTimeout(() => { notice.hidden = true; }, 7000);
  }

  async function request(path, options = {}) {
    const method = options.method || "GET";
    const headers = { Accept: "application/json" };
    if (method !== "GET") {
      headers["X-CSRF-Token"] = config.csrf_token;
      headers["Content-Type"] = "application/json";
    }
    let response;
    try {
      response = await fetch(path, {
        method,
        headers,
        credentials: "same-origin",
        cache: "no-store",
        ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
      });
    } catch (_) {
      throw new Error("暂时无法连接服务器，请稍后重试。");
    }
    if (response.status === 401) {
      window.location.assign("/login");
      throw new Error("登录已过期，请重新登录。");
    }
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error("服务器未返回有效结果，请稍后重试。"); }
    if (!response.ok) throw new Error(data.error || "操作未完成，请稍后重试。");
    return data;
  }

  async function busy(buttonIds, action, busyLabel) {
    const buttons = buttonIds.map($);
    if (buttons.some((button) => button.dataset.busy === "true")) return;
    const previous = buttons.map((button) => ({ text: button.textContent, disabled: button.disabled }));
    buttons.forEach((button, i) => {
      button.dataset.busy = "true";
      button.disabled = true;
      if (busyLabel && i === 0) button.textContent = busyLabel;
    });
    try { return await action(); }
    catch (error) { notify(error.message, true); }
    finally {
      buttons.forEach((button, i) => {
        delete button.dataset.busy;
        button.textContent = previous[i].text;
        button.disabled = previous[i].disabled;
      });
    }
  }

  function setStatus(prefix, label, state) {
    $(`${prefix}-status`).textContent = label;
    $(`${prefix}-dot`).className = `dot ${state}`;
  }

  async function loadStatus(silent = true) {
    try {
      const status = await request("/api/status");
      const proxy = status.proxy || {};
      setStatus("proxy", proxy.ok ? "已连接" : "等待连接", proxy.ok ? "ok" : "warning");
      setStatus("api", status.api && status.api.ok ? "运行中" : "尚未就绪", status.api && status.api.ok ? "ok" : "warning");
      setStatus("codex", status.codex_installed ? "已安装" : "待安装", status.codex_installed ? "ok" : "warning");
      currentNode = proxy.node || currentNode;
      $("current-node").textContent = currentNode === "REJECT" ? "请先选择一个订阅节点" : (currentNode || "尚未选择节点");
      $("current-node").title = currentNode;
      $("subscription-hint").textContent = proxy.subscription_set
        ? "已配置订阅。链接不会回显，留空可更新已保存的订阅。"
        : "订阅仅保存在服务器，保存后不会回显。";
      if (status.api_port) $("base-url").value = `http://127.0.0.1:${status.api_port}/v1`;
    } catch (error) {
      setStatus("proxy", "暂时无法检查", "warning");
      setStatus("api", "暂时无法检查", "warning");
      setStatus("codex", "暂时无法检查", "warning");
      if (!silent) notify(error.message, true);
    }
  }

  async function loadNodes() {
    const data = await request("/api/nodes");
    nodes = Array.isArray(data.nodes) ? data.nodes : [];
    const select = $("node-select");
    select.replaceChildren();
    if (!nodes.length) {
      select.add(new Option("暂无节点，请先导入订阅", ""));
    } else {
      nodes.forEach((node) => {
        const delay = typeof node.delay === "number" && node.delay > 0 ? ` · ${node.delay} ms` : "";
        select.add(new Option(`${node.name}${delay}`, node.name));
      });
      currentNode = data.selected || currentNode;
      if (nodes.some((node) => node.name === currentNode)) select.value = currentNode;
    }
    select.disabled = nodes.length === 0;
    $("node-switch").disabled = nodes.length === 0;
    $("node-hint").textContent = nodes.length
      ? (currentNode === "REJECT" ? `共 ${nodes.length} 个节点。请选择一个节点并点击“切换节点”后开始使用。` : `共 ${nodes.length} 个节点。Codex 和模型接口共用当前出口。`)
      : "支持的订阅导入后，节点会出现在这里。";
  }

  async function saveSubscription(refreshOnly = false) {
    const url = $("subscription-url").value.trim();
    const content = $("subscription-content").value.trim();
    if (!refreshOnly && url && content) {
      notify("订阅链接和订阅文本只需填写一种。", true);
      return;
    }
    if (!refreshOnly && !url && !content) {
      notify("请填写订阅链接或粘贴订阅文本。", true);
      return;
    }
    await busy(["subscription-save", "subscription-refresh"], async () => {
      const payload = refreshOnly ? {} : (url ? { url } : { content });
      const result = await request("/api/subscription", { method: "POST", body: payload });
      $("subscription-url").value = "";
      $("subscription-content").value = "";
      notify(result.message || `订阅已更新，共 ${result.count || 0} 个节点。`);
      await Promise.allSettled([loadNodes(), loadStatus()]);
    }, "正在更新…");
  }

  async function loadModels(quiet = false) {
    const select = $("model-select");
    const previous = select.value;
    try {
      const data = await request("/api/models");
      const models = Array.isArray(data.models) ? data.models : [];
      select.replaceChildren();
      models.forEach((model) => select.add(new Option(model.id, model.id)));
      if (models.some((model) => model.id === previous)) select.value = previous;
      if (!models.length) select.add(new Option("暂无模型，请先完成账号授权", ""));
      select.disabled = models.length === 0;
      $("chat-send").disabled = chatBusy || models.length === 0;
      if (!quiet) notify(models.length ? `已获取 ${models.length} 个模型。` : "未获取到模型，请检查代理和账号授权。", models.length === 0);
    } catch (error) {
      select.replaceChildren(new Option("暂时无法获取，请点击刷新模型", ""));
      select.disabled = true;
      $("chat-send").disabled = true;
      if (!quiet) throw error;
    }
  }

  async function sendQuestion(event) {
    event.preventDefault();
    if (chatBusy) return;
    const prompt = $("prompt").value.trim();
    const model = $("model-select").value;
    const reasoning_effort = $("reasoning-effort").value;
    const fast_mode = $("fast-mode").value === "true";
    if (!prompt || !model) { notify("请选择模型并输入问题。", true); return; }
    chatBusy = true;
    $("answer").textContent = "";
    $("answer-model").textContent = "";
    $("answer-options").textContent = "";
    $("answer-options").hidden = true;
    $("answer-region").hidden = true;
    $("answer-empty").hidden = true;
    $("chat-status").textContent = "正在等待模型回答…";
    $("prompt").readOnly = true;
    $("model-select").disabled = true;
    $("reasoning-effort").disabled = true;
    $("fast-mode").disabled = true;
    try {
      await busy(["chat-send", "chat-clear", "models-refresh"], async () => {
        const result = await request("/api/chat", { method: "POST", body: { model, prompt, reasoning_effort, fast_mode } });
        $("answer").textContent = result.answer || "模型返回了空回答。";
        $("answer-model").textContent = result.model || model;
        if (result.effective_tier) {
          $("answer-options").textContent = `上游返回的处理级别：${result.effective_tier}`;
          $("answer-options").hidden = false;
        } else if (fast_mode) {
          $("answer-options").textContent = "优先处理请求已发送，以上游返回为准。";
          $("answer-options").hidden = false;
        }
        $("answer-region").hidden = false;
        $("chat-status").textContent = "回答完成";
      }, "正在回答…");
    } finally {
      chatBusy = false;
      $("prompt").readOnly = false;
      $("model-select").disabled = false;
      $("reasoning-effort").disabled = false;
      $("fast-mode").disabled = false;
      $("chat-send").disabled = !$("model-select").value;
      if ($("answer-region").hidden) {
        $("answer-empty").hidden = false;
        $("chat-status").textContent = "调用未完成，可重试。";
      }
    }
  }

  $("subscription-form").addEventListener("submit", (event) => { event.preventDefault(); saveSubscription(); });
  $("subscription-refresh").addEventListener("click", () => saveSubscription(true));
  $("nodes-refresh").addEventListener("click", () => busy(["nodes-refresh"], async () => { await loadNodes(); await loadStatus(); }, "刷新中…"));
  $("node-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("node-select").value;
    if (!name) return;
    busy(["node-switch"], async () => {
      await request("/api/nodes/select", { method: "POST", body: { name } });
      currentNode = name;
      $("current-node").textContent = name;
      $("node-hint").textContent = `共 ${nodes.length} 个节点。Codex 和模型接口共用当前出口。`;
      $("proxy-result").hidden = true;
      notify(`已切换到：${name}`);
      await loadStatus();
    }, "切换中…");
  });
  $("proxy-check").addEventListener("click", () => busy(["proxy-check"], async () => {
    const result = await request("/api/proxy/check", { method: "POST", body: {} });
    $("proxy-result").textContent = `出口 IP：${result.ip || "未返回"}\n当前节点：${result.node || currentNode || "未选择"}`;
    $("proxy-result").hidden = false;
    await loadStatus();
  }, "检测中…"));
  $("models-refresh").addEventListener("click", () => busy(["models-refresh"], () => loadModels(), "刷新中…"));
  $("chat-form").addEventListener("submit", sendQuestion);
  $("chat-clear").addEventListener("click", () => {
    if (chatBusy) return;
    $("prompt").value = "";
    $("answer").textContent = "";
    $("answer-model").textContent = "";
    $("answer-options").textContent = "";
    $("answer-options").hidden = true;
    $("answer-region").hidden = true;
    $("answer-empty").hidden = false;
    $("chat-status").textContent = "";
    $("prompt").focus();
  });
  $("api-key-show").addEventListener("click", () => busy(["api-key-show"], async () => {
    if (!apiKeyVisible) {
      const result = await request("/api/access");
      apiKey = result.api_key || "";
      $("base-url").value = result.base_url || $("base-url").value;
      $("api-key").value = apiKey;
      $("api-key").type = "text";
      apiKeyVisible = true;
    } else {
      $("api-key").type = "password";
      $("api-key").value = "";
      apiKey = "";
      apiKeyVisible = false;
    }
    // Defer the label until the generic busy handler restores its original text.
    setTimeout(() => { $("api-key-show").textContent = apiKeyVisible ? "隐藏密钥" : "显示密钥"; }, 0);
  }));
  $("base-url-copy").addEventListener("click", async () => {
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText($("base-url").value);
      notify("接口地址已复制。");
    } catch (_) {
      $("base-url").focus();
      $("base-url").select();
      notify("地址已选中，请手动复制。");
    }
  });
  $("password-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const current = $("current-password").value;
    const next = $("new-password").value;
    if (next.length < 12) { notify("新密码至少需要 12 个字符。", true); return; }
    busy(["password-save"], async () => {
      await request("/api/password", { method: "POST", body: { current, new: next } });
      $("current-password").value = "";
      $("new-password").value = "";
      window.location.assign("/login");
    }, "保存中…");
  });
  $("logout-button").addEventListener("click", () => busy(["logout-button"], async () => {
    await request("/logout", { method: "POST", body: {} });
    window.location.assign("/login");
  }));

  Promise.allSettled([loadStatus(), loadNodes(), loadModels(true)]);
  setInterval(() => { if (!document.hidden) loadStatus(); }, 30000);
  window.addEventListener("pagehide", () => {
    $("prompt").value = "";
    $("answer").textContent = "";
    $("api-key").value = "";
    $("subscription-url").value = "";
    $("subscription-content").value = "";
    apiKey = "";
  });
})();
