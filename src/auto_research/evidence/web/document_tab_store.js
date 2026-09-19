(() => {
  "use strict";

  const SCHEMA_VERSION = "workspace-layout-v2";
  const LEGACY_SCHEMA_VERSION = "workspace-layout-v1";
  const STORAGE_KEY = "auto-research-workspace-layout-v1";
  const MAX_GROUPS = 2;
  const MAX_TABS = 40;
  const KINDS = new Set(["paper", "evidence", "pdf", "personal-table", "package-job", "librarian"]);
  const VIEWS = new Set(["paper", "search", "personal", "package"]);
  const clean = (value, limit = 300) => String(value ?? "").trim().slice(0, limit);
  const cleanFocusToken = value => {
    const token = clean(value, 200);
    return /^[A-Za-z0-9_.:-]{1,200}$/.test(token) ? token : "";
  };

  function publicIdentity(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const result = {};
    for (const key of ["paperId", "paperUid", "sourceScope", "sourceId", "entityType", "entityUid", "jobId", "operation", "conversationId", "reviewOrdinal"]) {
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
    const pinned = persisted && raw.pinned === undefined ? true : raw.pinned !== false;
    const tab = { tabId, kind, ownerView, title: clean(raw.title, 160) || "未命名标签", identity, groupId: raw.groupId === "secondary" ? "secondary" : "primary", pinned, preview: !pinned, revision: Number.isSafeInteger(raw?.revision) && raw.revision >= 0 ? raw.revision : 0 };
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
      this.narrow = false;
      this.recentlyClosed = [];
      this.requestGenerations = new Map();
      this.presentations = new Map();
      this.listeners = new Set();
      this.restore();
    }

    restore() {
      let raw = null;
      try { raw = JSON.parse(this.storage?.getItem?.(this.storageKey) || "null"); } catch (_error) { raw = null; }
      if (![SCHEMA_VERSION, LEGACY_SCHEMA_VERSION].includes(raw?.schema_version) || !Array.isArray(raw.tabs) || !Array.isArray(raw.groups)) return false;
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
        migrates_from: LEGACY_SCHEMA_VERSION,
        active_group_id: this.activeGroupId,
        groups: this.groups.map(group => ({ id: group.id, activeTabId: group.activeTabId })),
        tabs: this.tabs.map(({ tabId, kind, ownerView, title, identity, groupId, pinned }) => ({ tabId, kind, ownerView, title, identity, groupId, pinned }))
      };
      try { this.storage?.setItem?.(this.storageKey, JSON.stringify(payload)); } catch (_error) {}
      return payload;
    }

    snapshot() {
      return { schemaVersion: SCHEMA_VERSION, activeGroupId: this.activeGroupId, narrow: this.narrow, groups: this.groups.map(group => ({ ...group })), tabs: this.tabs.map(tab => ({ ...tab, identity: { ...tab.identity } })) };
    }

    subscribe(listener) { if (typeof listener !== "function") return () => {}; this.listeners.add(listener); return () => this.listeners.delete(listener); }
    emit() { const snapshot = this.snapshot(); this.persist(); for (const listener of this.listeners) listener(snapshot); return snapshot; }
    group(id = this.activeGroupId) { return this.groups.find(group => group.id === id) || null; }
    activeTab(groupId = this.activeGroupId) { const group = this.group(groupId); return this.tabs.find(tab => tab.groupId === groupId && tab.tabId === group?.activeTabId) || null; }

    rememberPresentation(tabId, value = {}) {
      if (!this.tabs.some(tab => tab.tabId === tabId) || !value || typeof value !== "object") return false;
      const prior = this.presentations.get(tabId) || { scrollTop: 0, focusToken: "" }, scrollTop = Number(value.scrollTop), focusToken = cleanFocusToken(value.focusToken);
      this.presentations.set(tabId, {
        scrollTop: Number.isFinite(scrollTop) ? Math.max(0, Math.min(10_000_000, Math.round(scrollTop))) : prior.scrollTop,
        focusToken: focusToken || prior.focusToken,
      });
      return true;
    }

    presentation(tabId) { const value = this.presentations.get(tabId); return value ? { ...value } : { scrollTop: 0, focusToken: "" }; }

    focusGroup(groupId) { if (!this.group(groupId)) return false; if (this.activeGroupId === groupId) return false; this.activeGroupId = groupId; this.emit(); return true; }

    open(raw, { activate = true, groupId = null, preview = false, pin = false } = {}) {
      const prior = this.tabs.find(value => value.tabId === raw?.tabId), selectedGroup = groupId || prior?.groupId || this.activeGroupId;
      if (selectedGroup === "secondary" && !this.group("secondary")) this.groups.push({ id: "secondary", activeTabId: null });
      const tab = normalizedTab({ ...raw, groupId: selectedGroup, pinned: pin || !preview });
      if (!tab) return null;
      let existing = this.tabs.find(value => value.tabId === tab.tabId);
      if (existing) {
        const previousGroupId = existing.groupId, previousRevision = existing.revision;
        Object.assign(existing, tab, {
          pinned: existing.pinned || pin || !preview,
          preview: !(existing.pinned || pin || !preview),
          revision: previousRevision + 1,
        });
        if (previousGroupId !== existing.groupId) {
          const previousGroup = this.group(previousGroupId);
          if (previousGroup?.activeTabId === existing.tabId) previousGroup.activeTabId = this.tabs.find(value => value.groupId === previousGroupId && value.tabId !== existing.tabId)?.tabId || null;
        }
      } else {
        if (preview) {
          const replace = this.tabs.findIndex(value => value.groupId === selectedGroup && value.preview && !value.pinned);
          if (replace >= 0) {
            const [removed] = this.tabs.splice(replace, 1), group = this.group(selectedGroup);
            this.requestGenerations.delete(removed.tabId); this.presentations.delete(removed.tabId);
            if (group?.activeTabId === removed.tabId) group.activeTabId = null;
          }
        }
        if (this.tabs.length >= MAX_TABS) {
          const removed = this.tabs.shift();
          if (removed) {
            const removedGroup = this.group(removed.groupId);
            this.requestGenerations.delete(removed.tabId);
            this.presentations.delete(removed.tabId);
            if (removedGroup?.activeTabId === removed.tabId) {
              removedGroup.activeTabId = this.tabs.find(value => value.groupId === removed.groupId)?.tabId || null;
            }
            if (removed.groupId === "secondary" && !this.tabs.some(value => value.groupId === "secondary")) {
              this.groups = this.groups.filter(group => group.id !== "secondary");
              if (this.activeGroupId === "secondary") this.activeGroupId = "primary";
            }
          }
        }
        this.tabs.push(tab); existing = tab;
      }
      if (activate) { this.activeGroupId = existing.groupId; this.group(existing.groupId).activeTabId = existing.tabId; }
      this.emit();
      return { ...existing };
    }

    activate(tabId) {
      const tab = this.tabs.find(value => value.tabId === tabId); if (!tab) return null;
      this.activeGroupId = tab.groupId; this.group(tab.groupId).activeTabId = tab.tabId; this.emit(); return { ...tab };
    }

    update(tabId, patch = {}) {
      const tab = this.tabs.find(value => value.tabId === tabId); if (!tab || !patch || typeof patch !== "object") return null;
      if (patch.title !== undefined) tab.title = clean(patch.title, 160) || tab.title;
      if (patch.payload !== undefined) tab.payload = patch.payload;
      if (patch.pin === true) { tab.pinned = true; tab.preview = false; }
      tab.revision += 1;
      this.emit(); return { ...tab };
    }

    pin(tabId) {
      const tab = this.tabs.find(value => value.tabId === tabId); if (!tab) return null;
      tab.pinned = true; tab.preview = false; this.emit(); return { ...tab };
    }

    beginRequest(tabId) {
      const tab = this.tabs.find(value => value.tabId === tabId); if (!tab) return 0;
      const previous = this.requestGenerations.get(tabId), generation = Number(previous?.generation || 0) + 1;
      this.requestGenerations.set(tabId, { generation, groupId: tab.groupId }); return generation;
    }

    requestBinding(tabId) {
      const value = this.requestGenerations.get(tabId);
      return value ? { generation: value.generation, groupId: value.groupId } : null;
    }

    completeRequest(tabId, generation, patch = {}, expectedGroupId = null) {
      const tab = this.tabs.find(value => value.tabId === tabId), binding = this.requestGenerations.get(tabId);
      if (!tab || !generation || binding?.generation !== generation || binding.groupId !== tab.groupId) return null;
      if (expectedGroupId && tab.groupId !== expectedGroupId) return null;
      return this.update(tabId, patch);
    }

    fallbackTabId(groupId, tabId) {
      const groupTabs = this.tabs.filter(tab => tab.groupId === groupId);
      const index = groupTabs.findIndex(tab => tab.tabId === tabId);
      if (index < 0) return groupTabs.at(-1)?.tabId || null;
      return groupTabs[index - 1]?.tabId || groupTabs[index + 1]?.tabId || null;
    }

    move(tabId, targetGroupId) {
      if (!["primary", "secondary"].includes(targetGroupId)) return false;
      const tab = this.tabs.find(value => value.tabId === tabId); if (!tab) return false;
      if (targetGroupId === "secondary" && !this.group("secondary")) this.groups.push({ id: "secondary", activeTabId: null });
      const previous = this.group(tab.groupId), previousFallback = this.fallbackTabId(tab.groupId, tabId); tab.groupId = targetGroupId;
      const request = this.requestGenerations.get(tabId); if (request) request.groupId = targetGroupId;
      tab.pinned = true; tab.preview = false;
      if (previous?.activeTabId === tabId) previous.activeTabId = previousFallback;
      this.activeGroupId = targetGroupId; this.group(targetGroupId).activeTabId = tabId;
      if (previous?.id === "secondary" && !this.tabs.some(value => value.groupId === "secondary")) this.groups = this.groups.filter(group => group.id !== "secondary");
      this.emit(); return true;
    }

    split(tabId = this.activeTab()?.tabId) {
      return this.move(tabId, "secondary");
    }

    merge() {
      const secondary = this.group("secondary"); if (!secondary) return false;
      for (const tab of this.tabs) if (tab.groupId === "secondary") tab.groupId = "primary";
      const primary = this.group("primary"); if (!primary.activeTabId) primary.activeTabId = secondary.activeTabId;
      this.groups = [primary]; this.activeGroupId = "primary"; this.emit(); return true;
    }

    close(tabId) {
      const index = this.tabs.findIndex(tab => tab.tabId === tabId); if (index < 0) return null;
      const closing = this.tabs[index], fallback = this.fallbackTabId(closing.groupId, tabId), [closed] = this.tabs.splice(index, 1), group = this.group(closed.groupId), presentation = this.presentation(tabId);
      this.recentlyClosed.unshift({ ...closed, presentation }); this.recentlyClosed = this.recentlyClosed.slice(0, 10); this.requestGenerations.delete(tabId); this.presentations.delete(tabId);
      if (group?.activeTabId === tabId) group.activeTabId = fallback;
      if (closed.groupId === "secondary" && !this.tabs.some(tab => tab.groupId === "secondary")) this.merge(); else this.emit();
      return { ...closed };
    }

    reopenClosed({ activate = true, groupId = null } = {}) {
      const closed = this.recentlyClosed.shift(); if (!closed) return null;
      const reopened = this.open(closed, { activate, groupId: groupId || closed.groupId, pin: closed.pinned });
      if (reopened && closed.presentation) this.rememberPresentation(reopened.tabId, closed.presentation);
      return reopened;
    }

    setNarrow(narrow) { const next = Boolean(narrow); if (this.narrow === next) return false; this.narrow = next; this.emit(); return true; }
  }

  globalThis.AutoResearchDocumentTabs = Object.freeze({ DocumentTabStore, SCHEMA_VERSION, STORAGE_KEY });
})();
