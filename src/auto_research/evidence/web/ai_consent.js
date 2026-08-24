(function (global) {
  "use strict";

  const STORAGE_KEY = "auto-research-ai-consent-v2";
  const SCHEMA = "auto-research-ai-consent-v2";
  const DISCLOSURES = Object.freeze({
    librarian: {
      version: "librarian-disclosure-v1",
      title: "使用图书管理员",
      outbound: "你的研究问题，以及从官方资料库召回的有界结构化证据摘要",
    },
    literature_extraction: {
      version: "literature-extraction-disclosure-v1",
      title: "自动提取与核验当前论文",
      outbound: "当前论文 PDF 中选取的页内文字、图注或表格文字及附近正文",
    },
    personal_suggestion: {
      version: "personal-suggestion-disclosure-v1",
      title: "识别当前实验工作表",
      outbound: "工作表名、行数、列名、数据类型、本地初步角色、含义、单位和最多 5 行样例",
    },
    selected_evidence_chat: {
      version: "selected-evidence-chat-disclosure-v1",
      title: "解读当前选中证据",
      outbound: "当前选中的一条证据、你的问题、有限对话历史，以及所属论文的相关页面文字",
    },
  });
  const trustedProviders = new Map();

  function updateTrustedProviders(catalog) {
    trustedProviders.clear();
    if (!Array.isArray(catalog)) return false;
    for (const provider of catalog) {
      if (!provider || typeof provider.provider_id !== "string" || typeof provider.display_name !== "string") continue;
      if (!/^[a-z][a-z0-9_-]{1,31}$/.test(provider.provider_id) || !provider.display_name.trim()) continue;
      trustedProviders.set(provider.provider_id, provider.display_name.trim());
    }
    return trustedProviders.size > 0;
  }

  function consentKey(providerId, scope, disclosureVersion) {
    return `${providerId}\u0000${scope}\u0000${disclosureVersion}`;
  }

  function readAcceptedDisclosures() {
    try {
      const parsed = JSON.parse(global.localStorage?.getItem(STORAGE_KEY) || "null");
      if (parsed?.schema !== SCHEMA || !Array.isArray(parsed.accepted_disclosures)) return new Set();
      return new Set(parsed.accepted_disclosures.filter(value => typeof value === "string" && value.length <= 160));
    } catch (_error) {
      return new Set();
    }
  }

  function writeAcceptedDisclosures(disclosures) {
    try {
      global.localStorage?.setItem(STORAGE_KEY, JSON.stringify({
        schema: SCHEMA,
        accepted_disclosures: [...disclosures].sort(),
      }));
    } catch (_error) {
      // This non-sensitive cache only prevents repeated disclosure prompts.
      // If unavailable, the next AI action asks again.
    }
  }

  function validatedContext(scope, context) {
    const disclosure = DISCLOSURES[scope];
    if (!disclosure || !context || typeof context !== "object") return null;
    const providerId = context.provider_id;
    const trustedLabel = trustedProviders.get(providerId);
    if (!trustedLabel || context.label !== trustedLabel || context.disclosure_version !== disclosure.version) return null;
    return { providerId, label: trustedLabel, disclosure };
  }

  function disclosureSummary(scope, context) {
    const valid = validatedContext(scope, context);
    if (!valid) return "";
    return `${valid.disclosure.title}\n\n将使用你保存在本机安全凭据存储中的 ${valid.label} API 密钥，并可能产生少量 API 费用。\n\n本次会发送给 ${valid.label}：${valid.disclosure.outbound}。\n\n不会发送本机文件路径、API 密钥、完整私人数据库或其他未选择的文件。`;
  }

  function disclosureText(scope, context) {
    const summary = disclosureSummary(scope, context);
    return summary ? `${summary}\n\n是否继续？` : "";
  }

  function accepted(scope, context) {
    const valid = validatedContext(scope, context);
    if (!valid) return false;
    return readAcceptedDisclosures().has(consentKey(valid.providerId, scope, valid.disclosure.version));
  }

  function remember(scope, context) {
    const valid = validatedContext(scope, context);
    if (!valid) return false;
    const disclosures = readAcceptedDisclosures();
    disclosures.add(consentKey(valid.providerId, scope, valid.disclosure.version));
    writeAcceptedDisclosures(disclosures);
    return true;
  }

  function ensure(scope, context) {
    const valid = validatedContext(scope, context);
    if (!valid) return false;
    const acceptedDisclosures = readAcceptedDisclosures();
    const key = consentKey(valid.providerId, scope, valid.disclosure.version);
    if (acceptedDisclosures.has(key)) return true;
    if (typeof global.confirm !== "function" || !global.confirm(disclosureText(scope, context))) return false;
    acceptedDisclosures.add(key);
    writeAcceptedDisclosures(acceptedDisclosures);
    return true;
  }

  global.AutoResearchAIConsent = Object.freeze({
    schema: SCHEMA,
    disclosureVersions: Object.freeze(Object.fromEntries(Object.entries(DISCLOSURES).map(([scope, value]) => [scope, value.version]))),
    updateTrustedProviders,
    ensure,
    accepted,
    remember,
    disclosureSummary,
    disclosureText,
  });
})(globalThis);
