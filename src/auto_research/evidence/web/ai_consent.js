(function (global) {
  "use strict";

  const STORAGE_KEY = "auto-research-ai-consent-v1";
  const SCHEMA = "auto-research-ai-consent-v1";
  const DISCLOSURES = Object.freeze({
    librarian: {
      title: "使用图书管理员",
      outbound: "你的研究问题，以及从官方资料库召回的有界结构化证据摘要",
    },
    literature_extraction: {
      title: "自动提取与核验当前论文",
      outbound: "当前论文 PDF 中选取的页内文字、图注或表格文字及附近正文",
    },
    personal_suggestion: {
      title: "识别当前实验工作表",
      outbound: "工作表名、行数、列名、数据类型、本地初步角色/含义/单位和最多 5 行样例",
    },
  });

  function readAcceptedScopes() {
    try {
      const parsed = JSON.parse(global.localStorage?.getItem(STORAGE_KEY) || "null");
      if (parsed?.schema !== SCHEMA || !Array.isArray(parsed.accepted_scopes)) return new Set();
      return new Set(parsed.accepted_scopes.filter(scope => Object.hasOwn(DISCLOSURES, scope)));
    } catch (_error) {
      return new Set();
    }
  }

  function writeAcceptedScopes(scopes) {
    try {
      global.localStorage?.setItem(STORAGE_KEY, JSON.stringify({
        schema: SCHEMA,
        accepted_scopes: [...scopes].sort(),
      }));
    } catch (_error) {
      // Non-sensitive preference storage is optional. If it is unavailable,
      // the next AI action asks again rather than weakening the gate.
    }
  }

  function disclosureText(scope) {
    const disclosure = DISCLOSURES[scope];
    if (!disclosure) return "";
    return `${disclosure.title}\n\n将使用你保存在本机安全凭据存储中的 DeepSeek API 密钥，并可能产生少量 API 费用。\n\n本次会发送给 DeepSeek：${disclosure.outbound}。\n\n不会发送本机文件路径、API 密钥、完整私人数据库或其他未选择的文件。是否继续？`;
  }

  function ensure(scope) {
    if (!Object.hasOwn(DISCLOSURES, scope)) return false;
    const accepted = readAcceptedScopes();
    if (accepted.has(scope)) return true;
    if (typeof global.confirm !== "function" || !global.confirm(disclosureText(scope))) return false;
    accepted.add(scope);
    writeAcceptedScopes(accepted);
    return true;
  }

  global.AutoResearchAIConsent = Object.freeze({
    schema: SCHEMA,
    ensure,
    disclosureText,
  });
})(globalThis);
