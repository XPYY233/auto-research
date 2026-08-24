(() => {
  "use strict";

  const SCHEMA_VERSION = "fusion-pane-layout-v2";
  const STORAGE_KEY = "fusion-pane-layout-v2";
  const LEGACY_STORAGE_KEY = "fusion-pane-layout-v1";
  const SIDE_SNAP = 48;
  const EDITOR_SNAP = 96;
  const SIDE_MAX = Object.freeze({ context: 480, inspector: 560 });
  const DEFAULTS = Object.freeze({ context: 244, inspector: 340, split: 0.58 });
  const LAYOUT_BREAKPOINTS = Object.freeze({ narrow: 900, medium: 1440 });
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, Number(value)));
  const exactKeys = (value, keys) => value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).sort().join(",") === [...keys].sort().join(",");

  function sideState(size) {
    return { collapsed: false, size, lastExpanded: size };
  }

  function defaultState() {
    return {
      context: sideState(DEFAULTS.context),
      inspector: sideState(DEFAULTS.inspector),
      editors: { primaryCollapsed: false, secondaryCollapsed: false, split: DEFAULTS.split, lastSplit: DEFAULTS.split },
    };
  }

  function layoutMode(width) {
    const value = Math.max(0, Number(width) || 0);
    return value < LAYOUT_BREAKPOINTS.narrow ? "narrow" : value < LAYOUT_BREAKPOINTS.medium ? "medium" : "wide";
  }

  function validSide(value, maximum) {
    return exactKeys(value, ["collapsed", "lastExpanded", "size"])
      && typeof value.collapsed === "boolean"
      && finite(value.size) && value.size >= 0 && value.size <= maximum
      && finite(value.lastExpanded) && value.lastExpanded > SIDE_SNAP && value.lastExpanded <= maximum
      && (value.collapsed ? value.size === 0 : value.size > SIDE_SNAP);
  }

  function validEditors(value) {
    return exactKeys(value, ["lastSplit", "primaryCollapsed", "secondaryCollapsed", "split"])
      && typeof value.primaryCollapsed === "boolean"
      && typeof value.secondaryCollapsed === "boolean"
      && !(value.primaryCollapsed && value.secondaryCollapsed)
      && finite(value.split) && value.split >= 0 && value.split <= 1
      && finite(value.lastSplit) && value.lastSplit > 0 && value.lastSplit < 1
      && (!value.primaryCollapsed || value.split === 0)
      && (!value.secondaryCollapsed || value.split === 1);
  }

  class PaneLayoutController {
    constructor({ storage = globalThis.localStorage, storageKey = STORAGE_KEY } = {}) {
      this.storage = storage;
      this.storageKey = storageKey;
      this.state = defaultState();
      this.restore();
    }

    replaceState(next) {
      this.state.context = { ...next.context };
      this.state.inspector = { ...next.inspector };
      this.state.editors = { ...next.editors };
      return this.state;
    }

    restore() {
      let raw = null;
      try { raw = JSON.parse(this.storage?.getItem?.(this.storageKey) || "null"); } catch (_error) { raw = null; }
      if (exactKeys(raw, ["context", "editors", "inspector", "schema_version"])
        && raw.schema_version === SCHEMA_VERSION
        && validSide(raw.context, SIDE_MAX.context)
        && validSide(raw.inspector, SIDE_MAX.inspector)
        && validEditors(raw.editors)) {
        this.replaceState({
          context: { ...raw.context },
          inspector: { ...raw.inspector },
          editors: { ...raw.editors },
        });
        return true;
      }
      let legacy = null;
      try { legacy = JSON.parse(this.storage?.getItem?.(LEGACY_STORAGE_KEY) || "null"); } catch (_error) { legacy = null; }
      if (exactKeys(legacy, ["context", "inspector", "schema_version", "split"])
        && legacy.schema_version === LEGACY_STORAGE_KEY
        && finite(legacy.context) && legacy.context > SIDE_SNAP && legacy.context <= SIDE_MAX.context
        && finite(legacy.inspector) && legacy.inspector > SIDE_SNAP && legacy.inspector <= SIDE_MAX.inspector
        && finite(legacy.split) && legacy.split > 0 && legacy.split < 1) {
        this.replaceState({
          context: sideState(legacy.context),
          inspector: sideState(legacy.inspector),
          editors: { primaryCollapsed: false, secondaryCollapsed: false, split: legacy.split, lastSplit: legacy.split },
        });
        return true;
      }
      this.replaceState(defaultState());
      return false;
    }

    snapshot() {
      return {
        schemaVersion: SCHEMA_VERSION,
        context: { ...this.state.context },
        inspector: { ...this.state.inspector },
        editors: { ...this.state.editors },
      };
    }

    persist() {
      const payload = {
        schema_version: SCHEMA_VERSION,
        context: { ...this.state.context },
        inspector: { ...this.state.inspector },
        editors: { ...this.state.editors },
      };
      try { this.storage?.setItem?.(this.storageKey, JSON.stringify(payload)); } catch (_error) {}
      return payload;
    }

    setSide(kind, value, { persist = true } = {}) {
      if (!Object.hasOwn(SIDE_MAX, kind) || !finite(Number(value))) return false;
      const side = this.state[kind], numeric = clamp(value, 0, SIDE_MAX[kind]);
      if (numeric <= SIDE_SNAP) {
        if (!side.collapsed && side.size > SIDE_SNAP) side.lastExpanded = side.size;
        side.collapsed = true; side.size = 0;
      } else {
        side.collapsed = false; side.size = numeric; side.lastExpanded = numeric;
      }
      if (persist) this.persist();
      return true;
    }

    collapseSide(kind, options = {}) { return this.setSide(kind, 0, options); }
    expandSide(kind, { persist = true } = {}) {
      if (!Object.hasOwn(SIDE_MAX, kind)) return false;
      const side = this.state[kind]; side.collapsed = false; side.size = clamp(side.lastExpanded || DEFAULTS[kind], SIDE_SNAP + 1, SIDE_MAX[kind]);
      if (persist) this.persist(); return true;
    }
    toggleSide(kind, options = {}) { return this.state[kind]?.collapsed ? this.expandSide(kind, options) : this.collapseSide(kind, options); }

    setEditorRatio(value, editorWidth, { persist = true } = {}) {
      const ratio = clamp(value, 0, 1), width = Math.max(0, Number(editorWidth) || 0), editors = this.state.editors;
      if (width > 0 && ratio * width <= EDITOR_SNAP) {
        if (!editors.primaryCollapsed && !editors.secondaryCollapsed && editors.split > 0 && editors.split < 1) editors.lastSplit = editors.split;
        editors.primaryCollapsed = true; editors.secondaryCollapsed = false; editors.split = 0;
      } else if (width > 0 && (1 - ratio) * width <= EDITOR_SNAP) {
        if (!editors.primaryCollapsed && !editors.secondaryCollapsed && editors.split > 0 && editors.split < 1) editors.lastSplit = editors.split;
        editors.primaryCollapsed = false; editors.secondaryCollapsed = true; editors.split = 1;
      } else {
        editors.primaryCollapsed = false; editors.secondaryCollapsed = false; editors.split = clamp(ratio, 0.01, 0.99); editors.lastSplit = editors.split;
      }
      if (persist) this.persist(); return true;
    }

    collapseEditor(groupId, { persist = true } = {}) {
      const editors = this.state.editors;
      if (groupId === "primary") {
        if (editors.secondaryCollapsed) return false;
        if (!editors.primaryCollapsed && editors.split > 0 && editors.split < 1) editors.lastSplit = editors.split;
        editors.primaryCollapsed = true; editors.secondaryCollapsed = false; editors.split = 0;
      } else if (groupId === "secondary") {
        if (editors.primaryCollapsed) return false;
        if (!editors.secondaryCollapsed && editors.split > 0 && editors.split < 1) editors.lastSplit = editors.split;
        editors.primaryCollapsed = false; editors.secondaryCollapsed = true; editors.split = 1;
      } else return false;
      if (persist) this.persist(); return true;
    }

    expandEditor(groupId, { persist = true } = {}) {
      const editors = this.state.editors;
      if (!['primary', 'secondary'].includes(groupId)) return false;
      editors.primaryCollapsed = false; editors.secondaryCollapsed = false; editors.split = clamp(editors.lastSplit || DEFAULTS.split, 0.01, 0.99);
      if (persist) this.persist(); return true;
    }

    toggleEditor(groupId, options = {}) {
      const key = `${groupId}Collapsed`;
      return this.state.editors[key] ? this.expandEditor(groupId, options) : this.collapseEditor(groupId, options);
    }

    reset(kind = null) {
      const defaults = defaultState();
      if (!kind) {
        this.state.context = defaults.context;
        this.state.inspector = defaults.inspector;
        this.state.editors = defaults.editors;
      }
      else if (kind === "context" || kind === "inspector") this.state[kind] = defaults[kind];
      else if (kind === "editor-groups") this.state.editors = defaults.editors;
      else return false;
      this.persist(); return true;
    }
  }

  globalThis.AutoResearchPaneLayout = Object.freeze({ PaneLayoutController, SCHEMA_VERSION, STORAGE_KEY, SIDE_SNAP, EDITOR_SNAP, DEFAULTS, SIDE_MAX, LAYOUT_BREAKPOINTS, layoutMode });
})();
