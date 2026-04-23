# Frontend Workspace

This frontend is now organized as a production-oriented React workspace with routed workspaces, nested admin routes, a shared design system, and a formal i18n layer.

## Stack

- React 18
- TypeScript with `strict: true`
- Vite
- React Router
- TanStack Query
- Zustand
- i18next / react-i18next
- Zod
- TanStack Virtual
- Vite PWA
- Vitest + React Testing Library
- Playwright
- ESLint + Prettier + Stylelint

## Development

```bash
npm install
npm run dev
```

Default local URL:

```text
http://127.0.0.1:5173
```

## Commands

```bash
npm run dev
npm run typecheck
npm run lint
npm run stylelint
npm run check:boundaries
npm run test
npm run test:coverage
npm run test:e2e
npm run build
npm run preview
```

Required local quality bar before merging frontend changes:

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

## Environment

Create a `.env` file in `app/frontend`:

```bash
VITE_BACKEND_URL=http://127.0.0.1:8000
```

## Source Layout

```text
src/
  app/         app shell, providers, route guards, router
  entities/    entity-level state and domain objects
  features/    feature-scoped UI and behavior
  pages/       route entry pages
  shared/      design system, i18n, api clients, query layer, utilities
  widgets/     composed view-level building blocks
```

The root `src/App.tsx` and `src/main.tsx` files are entry shims only. Business code must live in the layered structure above; old root-level business components, numbered CSS files, and JS/JSX application files are not allowed to return.

## Design System

The frontend uses design tokens plus CSS Modules as the formal styling system. Do not introduce styled-components, emotion, Tailwind, or global page CSS without an architecture decision.

Token scope:

- Colors and semantic status colors.
- Spacing, radius, shadows, typography, motion, and z-index.
- Shared primitives under `src/shared/ui`, including buttons, forms, cards, modal, tables, skeletons, error states, toolbars, confirm dialogs, and virtual lists.

Global CSS is limited to reset, base typography, global utilities, markdown rendering, and medical disclaimer highlighting. Page layout and component styling must use CSS Modules.

## Performance / PWA

- Routes are lazy-loaded and high-cost graph rendering is split behind a module-level lazy boundary.
- Vite manual chunks separate React, TanStack Query, and graph/canvas dependencies.
- PWA support caches only the application shell and static assets. Backend data routes such as `/api`, `/sessions`, `/admin`, `/graph`, `/qa`, and `/upload_file` are network-only and must not be cached offline.

## TypeScript / i18n Rules

- New application code must use `.ts` or `.tsx`.
- Do not add new `.js` or `.jsx` files under `src/`.
- Do not add business components directly under `src/`; keep only `src/App.tsx` and `src/main.tsx` as entry shims.
- Do not import old numbered CSS or `src/styles.css`.
- Prefer runtime-validated contracts from `src/shared/api/contracts.ts`.
- Use i18n message keys for user-facing copy; do not hardcode strings in new UI code.
- Avoid `any` and `@ts-ignore`.

## Quality Tooling

- ESLint enforces import order and unused import cleanup.
- Stylelint covers the new CSS Module / shared style layer.
- `npm run check:boundaries` blocks root-level business files, `.js/.jsx` source rollback, and old numbered CSS references.
- Vitest covers unit and component tests.
- Playwright smoke tests cover routed workspace boot.
- GitHub Actions runs install, typecheck, lint, stylelint, tests, Playwright smoke, and production build.
