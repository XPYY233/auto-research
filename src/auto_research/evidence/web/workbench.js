(() => {
  "use strict";

  const STORAGE_KEY = "auto-research-appearance-v1";
  const THEMES = new Set(["system", "light", "dark"]);
  const DENSITIES = new Set(["comfortable", "compact"]);
  const defaults = Object.freeze({ version: 1, theme: "system", density: "comfortable" });
  let preferences = { ...defaults };
  let settingsRevision = null;
  let settingsSaving = false;
  let aiCatalog = null;
  let aiSettings = null;

  function readPreferences() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (!parsed || parsed.version !== 1) return { ...defaults };
      return {
        version: 1,
        theme: THEMES.has(parsed.theme) ? parsed.theme : defaults.theme,
        density: DENSITIES.has(parsed.density) ? parsed.density : defaults.density,
      };
    } catch (_error) {
      return { ...defaults };
    }
  }

  function resolvedTheme(theme) {
    if (theme !== "system") return theme;
    return globalThis.matchMedia?.("(prefers-color-scheme: dark)")?.matches ? "dark" : "light";
  }

  function applyPreferences() {
    const root = document.documentElement;
    root.dataset.themePreference = preferences.theme;
    root.dataset.theme = resolvedTheme(preferences.theme);
    root.dataset.density = preferences.density;
    root.style.colorScheme = root.dataset.theme;
    document.querySelectorAll('input[name="workbench-theme"]').forEach(input => {
      input.checked = input.value === preferences.theme;
    });
    document.querySelectorAll('input[name="workbench-density"]').forEach(input => {
      input.checked = input.value === preferences.density;
    });
  }

  function appearanceStatus(message, failed = false) {
    const status = document.querySelector("#settings-appearance-status");
    if (!status) return;
    status.textContent = message;
    status.dataset.state = failed ? "error" : "ready";
  }

  function hydratePreferences(dto) {
    if (!dto || dto.schema_version !== "desktop-settings-v1") return false;
    const appearance = dto.appearance || {};
    if (!THEMES.has(appearance.theme) || !DENSITIES.has(appearance.density)) return false;
    if (!Number.isInteger(dto.revision) || dto.revision < 0) return false;
    preferences = { version: 1, theme: appearance.theme, density: appearance.density };
    settingsRevision = dto.revision;
    applyPreferences();
    try {
      // localStorage is only the next-launch, no-flash cache. The desktop DTO
      // remains authoritative whenever it is hydrated.
      localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
    } catch (_error) {}
    return true;
  }

  async function loadAuthoritativePreferences() {
    const port = globalThis.AutoResearchDesktopPorts?.loadDesktopSettings;
    if (typeof port !== "function") return appearanceStatus("桌面设置服务暂不可用；当前仅使用启动外观缓存。", true);
    try {
      const dto = await port();
      if (!hydratePreferences(dto)) throw new Error("设置格式无效");
      appearanceStatus("已与当前设备设置同步。 ");
    } catch (_error) {
      appearanceStatus("暂时无法读取设备设置；当前仅使用启动外观缓存，可稍后重试。", true);
    }
  }

  async function persistPreferences(next) {
    if (settingsSaving) return;
    const patch = globalThis.AutoResearchDesktopPorts?.patchDesktopPreferences;
    if (typeof patch !== "function" || !Number.isInteger(settingsRevision)) {
      applyPreferences();
      return appearanceStatus("尚未连接设备设置，未保存这次更改。", true);
    }
    settingsSaving = true;
    document.querySelectorAll('input[name="workbench-theme"],input[name="workbench-density"]').forEach(input => { input.disabled = true; });
    appearanceStatus("正在保存…");
    try {
      const dto = await patch({ appearance: next }, settingsRevision);
      if (!hydratePreferences(dto)) throw new Error("设置格式无效");
      appearanceStatus("已保存到当前设备。 ");
    } catch (error) {
      if (error?.latest && hydratePreferences(error.latest)) appearanceStatus("设置已在别处更新，请重新选择。", true);
      else {
        applyPreferences();
        appearanceStatus("保存失败，未更改设备设置；请重试。", true);
      }
    } finally {
      settingsSaving = false;
      document.querySelectorAll('input[name="workbench-theme"],input[name="workbench-density"]').forEach(input => { input.disabled = false; });
    }
  }

  const availabilityCopy = Object.freeze({ available: "可用", legacy_compatible: "兼容可用", credential_required: "需要密钥", verification_required: "需要验证", verification_failed: "验证失败" });
  function setAIStatus(message, failed = false) {
    const status = document.querySelector("#settings-ai-status");
    if (!status) return;
    status.textContent = message;
    status.dataset.state = failed ? "error" : "ready";
  }

  function selectedProviderProfile() {
    const id = document.querySelector("#settings-ai-provider")?.value;
    return aiCatalog?.providers?.find(provider => provider.provider_id === id) || null;
  }

  function renderAISettings(selectedProviderId = aiSettings?.provider_id) {
    const providerSelect = document.querySelector("#settings-ai-provider");
    if (!providerSelect || !aiCatalog || !aiSettings) return;
    providerSelect.innerHTML = aiCatalog.providers.map(provider => `<option value="${provider.provider_id}">${provider.display_name}</option>`).join("");
    providerSelect.value = selectedProviderId;
    providerSelect.disabled = false;
    const profile = selectedProviderProfile();
    document.querySelectorAll("[data-ai-model-task]").forEach(select => {
      const task = select.dataset.aiModelTask;
      const models = profile?.model_options?.[task] || [];
      select.innerHTML = models.map(model => `<option value="${model}">${model}</option>`).join("");
      select.value = aiSettings.task_models?.[task] || models[0] || "";
      select.disabled = !models.length;
    });
    document.querySelector("#settings-ai-model-save").disabled = !profile;
    const isCurrent = selectedProviderId === aiSettings.provider_id;
    const configured = document.querySelector("#settings-ai-configured");
    configured.textContent = isCurrent ? (aiSettings.configured ? "已保存" : "未配置") : "保存提供商后读取";
    configured.classList.toggle("ready", Boolean(isCurrent && aiSettings.configured));
    document.querySelector("#settings-ai-credential-note").textContent = `${profile?.display_name || "当前提供商"} 密钥由系统安全存储管理`;
    const verified = document.querySelector("#settings-ai-verified");
    verified.textContent = isCurrent ? (aiSettings.verified ? "已验证" : "未验证") : "保存提供商后读取";
    verified.classList.toggle("ready", Boolean(isCurrent && aiSettings.verified));
    document.querySelector("#settings-ai-availability").textContent = isCurrent ? (availabilityCopy[aiSettings.availability] || "不可用") : "尚未启用";
    const plan = aiCatalog.capability_test;
    document.querySelector("#settings-ai-test-plan").textContent = plan?.provider_id === aiSettings.provider_id
      ? `最多发起 ${Number(plan.maximum_model_calls || 0)} 次模型调用（${Number(plan.unique_model_count || 0)} 个模型）`
      : "切换并保存提供商后可生成精确测试计划";
    document.querySelector("#settings-ai-key-save").disabled = false;
    document.querySelector("#settings-ai-key-delete").disabled = !isCurrent || !aiSettings.configured;
    document.querySelector("#settings-ai-test").disabled = true;
    document.querySelector("#settings-ai-test").title = "一次性授权接口完成接线后才可测试；不会退回旧式布尔授权。";
    setAIStatus("AI 设置来自当前设备；连接测试正等待一次性授权接口。 ");
  }

  async function loadAISettings() {
    const port = globalThis.AutoResearchDesktopPorts?.loadAIPublicState;
    if (typeof port !== "function") return setAIStatus("桌面 AI 设置服务暂不可用。", true);
    try {
      const result = await port();
      aiCatalog = result.catalog;
      aiSettings = result.settings;
      renderAISettings();
    } catch (_error) {
      setAIStatus("无法读取受信 AI 提供商；所有 AI 操作保持关闭。", true);
    }
  }

  async function saveAIModels() {
    const profile = selectedProviderProfile();
    if (!profile || typeof globalThis.AutoResearchDesktopPorts?.patchAISettings !== "function") return setAIStatus("AI 设置服务暂不可用。", true);
    const taskModels = Object.fromEntries([...document.querySelectorAll("[data-ai-model-task]")].map(select => [select.dataset.aiModelTask, select.value]));
    try {
      aiSettings = await globalThis.AutoResearchDesktopPorts.patchAISettings(profile.provider_id, taskModels, aiSettings.revision);
      await loadAISettings();
      setAIStatus("模型分工已保存。 ");
    } catch (_error) {
      await loadAISettings();
      setAIStatus("模型设置未保存，请根据最新状态重试。", true);
    }
  }

  async function saveAIKey() {
    const input = document.querySelector("#settings-ai-key");
    const key = input?.value.trim();
    const profile = selectedProviderProfile();
    if (!key || !profile || typeof globalThis.AutoResearchDesktopPorts?.saveAICredential !== "function") return setAIStatus("请粘贴密钥，或稍后重试。", true);
    input.value = "";
    try {
      await globalThis.AutoResearchDesktopPorts.saveAICredential(profile.provider_id, key);
      await loadAISettings();
      setAIStatus("密钥已交给系统安全凭据存储。 ");
    } catch (_error) {
      setAIStatus("密钥未保存；请检查后重试。页面没有保留输入内容。", true);
    }
  }

  async function deleteAIKey() {
    const profile = selectedProviderProfile();
    if (!profile || typeof globalThis.AutoResearchDesktopPorts?.deleteAICredential !== "function") return;
    try {
      await globalThis.AutoResearchDesktopPorts.deleteAICredential(profile.provider_id);
      await loadAISettings();
      setAIStatus("本机密钥已删除。 ");
    } catch (_error) {
      setAIStatus("删除失败；本页不会猜测凭据状态，请重试。", true);
    }
  }

  function requestView(name) {
    if (typeof switchView !== "function") return;
    switchView(name);
  }

  function viewChanged(name) {
    const button = document.querySelector("#workbench-settings-open");
    if (!button) return;
    const active = name === "settings";
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  }

  function openCommandPalette() {
    const dialog = document.querySelector("#workbench-command-palette");
    if (!dialog || dialog.open) return;
    dialog.showModal();
    const query = document.querySelector("#workbench-command-query");
    if (query) {
      query.value = "";
      filterCommands("");
      setCommandOption(0, false);
      globalThis.requestAnimationFrame?.(() => query.focus());
    }
  }

  function closeCommandPalette() {
    document.querySelector("#workbench-command-palette")?.close();
  }

  function filterCommands(value) {
    const query = String(value || "").trim().toLocaleLowerCase("zh-CN");
    document.querySelectorAll("[data-command-view]").forEach(button => {
      button.hidden = Boolean(query) && !button.textContent.toLocaleLowerCase("zh-CN").includes(query);
    });
    setCommandOption(0, false);
  }

  function commandOptions() {
    return [...document.querySelectorAll("[data-command-view]")].filter(button => !button.hidden);
  }

  function setCommandOption(index, focus) {
    document.querySelectorAll("[data-command-view]").forEach(button => {
      button.tabIndex = -1;
      button.setAttribute("aria-selected", "false");
    });
    const options = commandOptions();
    if (!options.length) {
      document.querySelector("#workbench-command-query")?.removeAttribute("aria-activedescendant");
      return;
    }
    const activeIndex = (index + options.length) % options.length;
    options.forEach((button, optionIndex) => {
      const active = optionIndex === activeIndex;
      button.tabIndex = active ? 0 : -1;
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
    const active = options[activeIndex];
    document.querySelector("#workbench-command-query")?.setAttribute("aria-activedescendant", active.id);
    if (focus) active.focus();
  }

  function moveCommandOption(current, offset) {
    const options = commandOptions();
    const currentIndex = Math.max(0, options.indexOf(current));
    setCommandOption(currentIndex + offset, true);
  }

  function activateCommand(button) {
    if (!button?.dataset.commandView) return;
    closeCommandPalette();
    requestView(button.dataset.commandView);
  }

  function selectSettingsSection(name) {
    document.querySelectorAll("[data-settings-section]").forEach(button => {
      const active = button.dataset.settingsSection === name;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
      button.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll("[data-settings-panel]").forEach(panel => {
      const active = panel.dataset.settingsPanel === name;
      panel.classList.toggle("active", active);
      panel.hidden = !active;
    });
  }

  function applySettingsOrientation(media = globalThis.matchMedia?.("(max-width: 900px)")) {
    document.querySelector(".settings-navigation")?.setAttribute("aria-orientation", media?.matches ? "horizontal" : "vertical");
  }

  function handleShortcut(event) {
    if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
    if (event.target?.matches?.("input,textarea,select,[contenteditable='true']")) return;
    const viewByKey = { "1": "paper", "2": "search", "3": "personal", "4": "package" };
    if (viewByKey[event.key]) {
      event.preventDefault();
      requestView(viewByKey[event.key]);
      return;
    }
    if (event.key.toLocaleLowerCase("en-US") === "k") {
      event.preventDefault();
      openCommandPalette();
      return;
    }
    if (event.key === ",") {
      event.preventDefault();
      requestView("settings");
    }
  }

  function initialize() {
    document.querySelector("#workbench-settings-open")?.addEventListener("click", () => requestView("settings"));
    document.querySelector("#workbench-command-open")?.addEventListener("click", openCommandPalette);
    const shortcutByView = { paper: "Meta+1 Control+1", search: "Meta+2 Control+2", personal: "Meta+3 Control+3", package: "Meta+4 Control+4" };
    document.querySelectorAll("#workbench-navigation [data-view]").forEach(button => {
      button.setAttribute("aria-keyshortcuts", shortcutByView[button.dataset.view] || "");
    });
    const commandQuery = document.querySelector("#workbench-command-query");
    commandQuery?.addEventListener("input", event => filterCommands(event.target.value));
    commandQuery?.addEventListener("keydown", event => {
      if (!["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) return;
      event.preventDefault();
      const options = commandOptions();
      if (event.key === "Enter") activateCommand(options.find(button => button.getAttribute("aria-selected") === "true"));
      else setCommandOption(event.key === "ArrowDown" ? 0 : options.length - 1, true);
    });
    document.querySelectorAll("[data-command-view]").forEach(button => {
      button.addEventListener("click", () => activateCommand(button));
      button.addEventListener("keydown", event => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          moveCommandOption(button, event.key === "ArrowDown" ? 1 : -1);
        } else if (event.key === "Home" || event.key === "End") {
          event.preventDefault();
          const options = commandOptions();
          setCommandOption(event.key === "Home" ? 0 : options.length - 1, true);
        } else if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activateCommand(button);
        }
      });
    });
    document.querySelectorAll("[data-settings-section]").forEach(button => {
      button.addEventListener("click", () => selectSettingsSection(button.dataset.settingsSection));
      button.addEventListener("keydown", event => {
        if (!["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const tabs = [...document.querySelectorAll("[data-settings-section]")];
        const offset = event.key === "Home" ? -tabs.indexOf(button) : event.key === "End" ? tabs.length - 1 - tabs.indexOf(button) : ["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : -1;
        const next = tabs[(tabs.indexOf(button) + offset + tabs.length) % tabs.length];
        selectSettingsSection(next.dataset.settingsSection);
        next.focus();
      });
    });
    document.querySelectorAll("[data-settings-view]").forEach(button => button.addEventListener("click", () => requestView(button.dataset.settingsView)));
    document.querySelectorAll('input[name="workbench-theme"]').forEach(input => input.addEventListener("change", () => void persistPreferences({ theme: input.value })));
    document.querySelectorAll('input[name="workbench-density"]').forEach(input => input.addEventListener("change", () => void persistPreferences({ density: input.value })));
    document.querySelector("#settings-ai-provider")?.addEventListener("change", event => renderAISettings(event.target.value));
    document.querySelector("#settings-ai-model-save")?.addEventListener("click", () => void saveAIModels());
    document.querySelector("#settings-ai-key-save")?.addEventListener("click", () => void saveAIKey());
    document.querySelector("#settings-ai-key-delete")?.addEventListener("click", () => void deleteAIKey());
    globalThis.matchMedia?.("(prefers-color-scheme: dark)")?.addEventListener?.("change", () => {
      if (preferences.theme === "system") applyPreferences();
    });
    const narrowSettings = globalThis.matchMedia?.("(max-width: 900px)");
    applySettingsOrientation(narrowSettings);
    narrowSettings?.addEventListener?.("change", event => applySettingsOrientation(event));
    document.addEventListener("keydown", handleShortcut);
    selectSettingsSection("appearance");
    viewChanged(document.body.dataset.view);
    void loadAuthoritativePreferences();
    void loadAISettings();
  }

  preferences = readPreferences();
  applyPreferences();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
  else initialize();
  globalThis.AutoResearchWorkbench = Object.freeze({ applyPreferences, hydratePreferences, openCommandPalette, selectSettingsSection, viewChanged });
})();
