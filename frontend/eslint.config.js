import { defineConfig, globalIgnores } from 'eslint/config'
import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // Only the two long-established react-hooks rules, not the plugin's bundled
      // "recommended"/"recommended-latest" presets — those pull in React Compiler
      // readiness rules (e.g. set-state-in-effect) that flag this codebase's ordinary
      // fetch-on-mount pattern (`useEffect(() => { void reload() }, [reload])`, used
      // throughout src/pages/*), and this project doesn't build with the compiler
      // (no babel-plugin-react-compiler in vite.config.ts).
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
  {
    files: ['vite.config.ts'],
    languageOptions: {
      globals: globals.node,
    },
  },
])
