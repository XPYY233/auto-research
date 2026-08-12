(() => {
  "use strict";

  const STORAGE_KEY = "auto-research-appearance-v1";
  const THEMES = new Set(["system", "light", "dark"]);
  const DENSITIES = new Set(["comfortable", "compact"]);
  const defaults = Object.freeze({ version: 1, theme: "system", density: "comfortable" });
  let preferences = { ...defaults };

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

  function savePreferences(next) {
    preferences = { ...preferences, ...next, version: 1 };
    applyPreferences();
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
    } catch (_error) {
      // Appearance remains active for this session when storage is unavailable.
    }
  }

  function hydratePreferences(dto) {
    if (!dto || dto.schema_version !== "desktop-settings-v1") return false;
    const appearance = dto.appearance || {};
    if (!THEMES.has(appearance.theme) || !DENSITIES.has(appearance.density)) return false;
    preferences = { version: 1, theme: appearance.theme, density: appearance.density };
    applyPreferences();
    try {
      // localStorage is only the next-launch, no-flash cache. The desktop DTO
      // remains authoritative whenever it is hydrated.
      localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
    } catch (_error) {}
    return true;
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
    document.querySelectorAll('input[name="workbench-theme"]').forEach(input => input.addEventListener("change", () => savePreferences({ theme: input.value })));
    document.querySelectorAll('input[name="workbench-density"]').forEach(input => input.addEventListener("change", () => savePreferences({ density: input.value })));
    globalThis.matchMedia?.("(prefers-color-scheme: dark)")?.addEventListener?.("change", () => {
      if (preferences.theme === "system") applyPreferences();
    });
    const narrowSettings = globalThis.matchMedia?.("(max-width: 900px)");
    applySettingsOrientation(narrowSettings);
    narrowSettings?.addEventListener?.("change", event => applySettingsOrientation(event));
    document.addEventListener("keydown", handleShortcut);
    selectSettingsSection("appearance");
    viewChanged(document.body.dataset.view);
  }

  preferences = readPreferences();
  applyPreferences();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
  else initialize();
  globalThis.AutoResearchWorkbench = Object.freeze({ applyPreferences, hydratePreferences, openCommandPalette, viewChanged });
})();
