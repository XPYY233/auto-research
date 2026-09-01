(() => {
  "use strict";

  const BREAKPOINTS = Object.freeze({ wide: 1600, desktop: 1200, compact: 900 });
  const VIEWS = new Set(["paper", "search", "personal", "package", "settings"]);

  function modeForWidth(width) {
    const value = Math.max(0, Number(width) || 0);
    if (value >= BREAKPOINTS.wide) return "wide";
    if (value >= BREAKPOINTS.desktop) return "desktop";
    if (value >= BREAKPOINTS.compact) return "compact";
    return "narrow";
  }

  function project(input = {}) {
    const width = Math.max(320, Number(input.width) || 1440);
    const mode = modeForWidth(width);
    const view = VIEWS.has(input.view) ? input.view : "paper";
    const librarian = view === "search" && input.searchMode === "librarian";
    const hasSecondary = Boolean(input.hasSecondary) && !["personal", "package", "settings"].includes(view);
    const hasInspector = Boolean(input.hasInspectorSelection) && !["package", "settings"].includes(view) && !librarian;
    const contextPreferred = input.contextExpanded !== false;
    const inspectorPreferred = input.inspectorExpanded !== false;
    const resultNavigator = librarian && Boolean(input.librarianResultsOpen);
    const librarianDetail = librarian && Boolean(input.librarianDetailOpen);

    let context = "hidden";
    let secondary = "hidden";
    let inspector = "hidden";
    let librarianResults = "hidden";

    if (mode === "wide") {
      if (resultNavigator) librarianResults = "docked";
      else context = contextPreferred ? "docked" : "hidden";
      if (hasSecondary) secondary = "docked";
      else if (hasInspector && inspectorPreferred) inspector = "docked";
    } else if (mode === "desktop") {
      if (resultNavigator) librarianResults = "docked";
      else if (hasSecondary) secondary = "docked";
      else context = contextPreferred ? "docked" : "hidden";
      if (hasInspector && inspectorPreferred) inspector = "drawer";
    } else {
      context = contextPreferred ? "drawer" : "hidden";
      if (hasSecondary) secondary = "single";
      if (hasInspector && inspectorPreferred) inspector = "drawer";
      if (resultNavigator) librarianResults = "drawer";
    }

    if (view === "settings") inspector = secondary = librarianResults = "hidden";
    if (view === "package") inspector = secondary = librarianResults = "hidden";
    if (view === "personal") secondary = librarianResults = "hidden";
    if (librarian) {
      inspector = "hidden";
      // The librarian owns the primary research surface. Preserve unrelated
      // document tabs in the store without charging their editor column to chat.
      secondary = "hidden";
      if (resultNavigator) {
        if (mode === "wide") context = contextPreferred ? "docked" : "hidden";
        else if (mode === "desktop") context = contextPreferred ? "drawer" : "hidden";
      } else if (librarianDetail && hasSecondary) {
        if (mode === "wide") context = contextPreferred ? "docked" : "hidden";
        else if (mode === "desktop") context = contextPreferred ? "drawer" : "hidden";
        secondary = mode === "wide" || mode === "desktop" ? "docked" : "single";
      } else {
        if (mode === "wide" || mode === "desktop") context = contextPreferred ? "docked" : "hidden";
      }
    }

    const dockedColumns = 1
      + (context === "docked" ? 1 : 0)
      + (secondary === "docked" ? 1 : 0)
      + (inspector === "docked" ? 1 : 0)
      + (librarianResults === "docked" ? 1 : 0);
    if (dockedColumns > 3) throw new Error("workspace_layout_column_budget_exceeded");

    return Object.freeze({
      mode,
      view,
      librarian,
      librarianDetail,
      context,
      primary: "docked",
      secondary,
      inspector,
      librarianResults,
      dockedColumns,
      narrow: mode === "narrow" || mode === "compact",
    });
  }

  globalThis.AutoResearchWorkspaceLayout = Object.freeze({ BREAKPOINTS, modeForWidth, project });
})();
