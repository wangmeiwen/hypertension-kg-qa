import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import { resources } from "../i18n/resources";

const LANGUAGE_KEY = "workspace_language";
const DEFAULT_LANGUAGE = "zh-CN";

// 优先恢复用户保存的语言偏好，未设置时使用中文。
const initialLanguage = window.localStorage.getItem(LANGUAGE_KEY) || DEFAULT_LANGUAGE;

void i18n.use(initReactI18next).init({
  resources,
  lng: initialLanguage,
  fallbackLng: "en-US",
  interpolation: {
    escapeValue: false,
  },
});

i18n.on("languageChanged", (language) => {
  // 语言切换后持久化，下次刷新保持同一语言。
  window.localStorage.setItem(LANGUAGE_KEY, language);
});

export default i18n;
