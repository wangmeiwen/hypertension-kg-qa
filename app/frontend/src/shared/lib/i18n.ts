import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import { resources } from "../i18n/resources";

const LANGUAGE_KEY = "workspace_language";
const DEFAULT_LANGUAGE = "zh-CN";

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
  window.localStorage.setItem(LANGUAGE_KEY, language);
});

export default i18n;
