import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Honor the `_` prefix the codebase already uses to mark intentionally
      // unused args/vars/catch-bindings (the rule wasn't configured for it).
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
      // R-72: surface these as warnings (non-blocking) and fix incrementally —
      // they are advisory/strictness, not correctness bugs, so the CI lint gate
      // can be enabled now (errors block) without a big-bang refactor.
      //  - no-explicit-any: type-strictness preference.
      //  - react-refresh/only-export-components: dev-only fast-refresh hint.
      //  - set-state-in-effect / purity / refs / incompatible-library are the
      //    new react-compiler-adjacent react-hooks checks, advisory until the
      //    compiler is adopted. (rules-of-hooks stays an error.)
      '@typescript-eslint/no-explicit-any': 'warn',
      'react-refresh/only-export-components': 'warn',
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/purity': 'warn',
      'react-hooks/refs': 'warn',
      'react-hooks/incompatible-library': 'warn',
    },
  },
])
