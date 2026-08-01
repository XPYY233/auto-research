(() => {
  "use strict";

  const button = document.querySelector("#librarian-brief-export");
  const status = document.querySelector("#librarian-brief-status");
  const messages = document.querySelector("#librarian-messages");
  if (!button || !status || !messages) return;

  const latestPayload = () => globalThis.autoResearchLibrarianBrief?.getLatestPayload?.() || null;

  function updateAvailability() {
    const available = Boolean(latestPayload());
    button.disabled = !available;
    button.title = available
      ? "导出当前已绑定回答及其实际引用证据；不会重新调用 DeepSeek"
      : "完成一次非澄清、含实际引用证据的图书管理员检索后可导出";
  }

  async function exportBrief() {
    const payload = latestPayload();
    if (!payload) {
      globalThis.autoResearchLibrarianBrief?.clearAuthorization?.();
      status.textContent = "当前回答尚未满足研究简报导出条件。";
      updateAvailability();
      return;
    }
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "生成简报中…";
    status.textContent = "正在整理当前回答，不会重新检索或调用模型。";
    try {
      const response = await fetch("/api/agents/librarian/research-brief.md", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || `导出失败（${response.status}）`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "librarian-research-brief.md";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      status.textContent = "研究简报已下载；本次导出未重新调用 DeepSeek。";
    } catch (error) {
      globalThis.autoResearchLibrarianBrief?.clearAuthorization?.(payload.snapshot_token);
      status.textContent = `导出失败：${error.message}`;
    } finally {
      button.textContent = label;
      updateAvailability();
    }
  }

  button.addEventListener("click", exportBrief);
  new MutationObserver(updateAvailability).observe(messages, { childList: true, subtree: true });
  updateAvailability();
})();
