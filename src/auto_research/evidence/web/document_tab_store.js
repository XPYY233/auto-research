(() => {
  "use strict";

  const SCHEMA_VERSION = "workspace-layout-v1";
  const STORAGE_KEY = "auto-research-workspace-layout-v1";
  const MAX_GROUPS = 2;
  const MAX_TABS = 40;
  const KINDS = new Set(["paper", "evidence", "pdf", "personal-table", "package-job", "librarian"]);
  const VIEWS = new Set(["paper", "search", "personal", "package"]);
  const clean = (value, limit = 300) => String(value ?? "").trim().slice(0, limit);

  function publicIdentity(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const result = {};
    for (const key of ["paperId", "sourceScope", "sourceId", "entityType", "entityUid", "jobId", "conversationId"]) {
      const text = clean(value[key], 500);
      if (text) result[key] = text;
    }
    return Object.keys(result).length ? result : null;
  }

  function normalizedTab(raw, { persisted = false } = {}) {
    const kind = clean(raw?.kind, 40), ownerView = clean(raw?.ownerView, 40), tabId = clean(raw?.tabId, 500);
    if (!KINDS.has(kind) || !VIEWS.has(ownerView) || !tabId || !tabId.startsWith(`${kind}:`)) return null;
    const identity = publicIdentity(raw.identity);
    if (!identity) return null;
    const tab = { tabId, kind, ownerView, title: clean(raw.title, 160) || "未命名标签", identity, groupId: raw.groupId === "secondary" ? "secondary" : "primary" };
    if (!persisted && raw.payload !== undefined) tab.payload = raw.payload;
    return tab;
  }

  class DocumentTabStore {
    constructor({ storage = globalThis.localStorage, storageKey = STORAGE_KEY } = {}) {
      this.storage = storage;
      this.storageKey = storageKey;
      this.tabs = [];
      this.groups = [{ id: "primary", activeTabId: null }];
      this.activeGroupId = "primary";
      this.listeners = new Set();
      this.restore();
    }

    restore() {
      let raw = null;
      try { raw = JSON.parse(this.storage?.getItem?.(this.storageKey) || "null"); } catch (_error) { raw = null; }
      if (raw?.schema_version !== SCHEMA_VERSION || !Array.isArray(raw.tabs) || !Array.isArray(raw.groups)) return false;
      const tabs = raw.tabs.slice(0, MAX_TABS).map(value => normalizedTab(value, { persisted: true })).filter(Boolean);
      const ids = new Set();
      this.tabs = tabs.filter(tab => !ids.has(tab.tabId) && ids.add(tab.tabId));
      const wantsSecondary = raw.groups.some(group => group?.id === "secondary") && this.tabs.some(tab => tab.groupId === "secondary");
      this.groups = [{ id: "primary", activeTabId: null }, ...(wantsSecondary ? [{ id: "secondary", activeTabId: null }] : [])];
      for (const group of this.groups) {
        const requested = clean(raw.groups.find(value => value?.id === group.id)?.activeTabId, 500);
        group.activeTabId = this.tabs.some(tab => tab.groupId === group.id && tab.tabId === requested) ? requested : this.tabs.find(tab => tab.groupId === group.id)?.tabId || null;
      }
      this.activeGroupId = this.groups.some(group => group.id === raw.active_group_id) ? raw.active_group_id : "primary";
      return true;
    }

    persist() {
      const payload = {
        schema_version: SCHEMA_VERSION,
        active_group_id: this.activeGroupId,
        groups: this.groups.map(group => ({ id: group.id, activeTabId: group.activeTabId })),
        tabs: this.tabs.map(({ tabId, kind, ownerView, title, identity, groupId }) => ({ tabId, kind, ownerView, title, identity, groupId }))
      };
      try { this.storage?.setItem?.(this.storageKey, JSON.stringify(payload)); } catch (_error) {}
      return payload;
    }

    snapshot() {
      return { schemaVersion: SCHEMA_VERSION, activeGroupId: this.activeGroupId, groups: this.groups.map(group => ({ ...group })), tabs: this.tabs.map(tab => ({ ...tab, identity: { ...tab.identity } })) };
    }

    subscribe(listener) { if (typeof listener !== "function") return () => {}; this.listeners.add(listener); return () => this.listeners.delete(listener); }
    emit() { const snapshot = this.snapshot(); this.persist(); for (const listener of this.listeners) listener(snapshot); return snapshot; }
    group(id = this.activeGroupId) { return this.groups.find(group => group.id === id) || null; }
    activeTab(groupId = this.activeGroupId) { const group = this.group(groupId); return this.tabs.find(tab => tab.groupId === groupId && tab.tabId === group?.activeTabId) || null; }

    open(raw, { activate = true, groupId = this.activeGroupId } = {}) {
      const tab = normalizedTab({ ...raw, groupId });
      if (!tab) return null;
      let existing = this.tabs.find(value => value.tabId === tab.tabId);
      if (existing) Object.assign(existing, tab);
      else { if (this.tabs.length >= MAX_TABS) this.tabs.shift(); this.tabs.push(tab); existing = tab; }
      if (activate) { this.activeGroupId = existing.groupId; this.group(existing.groupId).activeTabId = existing.tabId; }
      this.emit();
      return { ...existing };
    }

    activate(tabId) {
      const tab = this.tabs.find(value => value.tabId === tabId); if (!tab) return null;
      this.activeGroupId = tab.groupId; this.group(tab.groupId).activeTabId = tab.tabId; this.emit(); return { ...tab };
    }

    split(tabId = this.activeTab()?.tabId) {
      const tab = this.tabs.find(value => value.tabId === tabId); if (!tab) return false;
      if (!this.group("secondary") && this.groups.length >= MAX_GROUPS) return false;
      if (!this.group("secondary")) this.groups.push({ id: "secondary", activeTabId: null });
      tab.groupId = "secondary"; this.activeGroupId = "secondary"; this.group("secondary").activeTabId = tab.tabId;
      const primary = this.group("primary"); if (primary.activeTabId === tab.tabId) primary.activeTabId = this.tabs.find(value => value.groupId === "primary")?.tabId || null;
      this.emit(); return true;
    }

    merge() {
      const secondary = this.group("secondary"); if (!secondary) return false;
      for (const tab of this.tabs) if (tab.groupId === "secondary") tab.groupId = "primary";
      const primary = this.group("primary"); if (!primary.activeTabId) primary.activeTabId = secondary.activeTabId;
      this.groups = [primary]; this.activeGroupId = "primary"; this.emit(); return true;
    }

    close(tabId) {
      const index = this.tabs.findIndex(tab => tab.tabId === tabId); if (index < 0) return null;
      const [closed] = this.tabs.splice(index, 1), group = this.group(closed.groupId);
      if (group?.activeTabId === tabId) group.activeTabId = this.tabs.filter(tab => tab.groupId === closed.groupId).at(-1)?.tabId || null;
      if (closed.groupId === "secondary" && !this.tabs.some(tab => tab.groupId === "secondary")) this.merge(); else this.emit();
      return { ...closed };
    }

    setNarrow(narrow) { if (narrow) return this.merge(); return false; }
  }

  globalThis.AutoResearchDocumentTabs = Object.freeze({ DocumentTabStore, SCHEMA_VERSION, STORAGE_KEY });
})();
