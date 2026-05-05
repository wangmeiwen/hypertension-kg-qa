/// <reference types="vite/client" />

// Vite 注入的前端环境变量类型声明。
interface ImportMetaEnv {
  readonly VITE_BACKEND_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
