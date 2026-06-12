/* eslint-env node */
module.exports = {
  root: true,
  env: { browser: true, node: true, es2022: true },
  parser: 'vue-eslint-parser',
  parserOptions: {
    parser: '@typescript-eslint/parser',
    ecmaVersion: 'latest',
    sourceType: 'module',
    extraFileExtensions: ['.vue'],
  },
  extends: [
    'eslint:recommended',
    'plugin:vue/vue3-recommended',
    'plugin:@typescript-eslint/recommended',
  ],
  plugins: ['@typescript-eslint'],
  rules: {
    'vue/multi-word-component-names': 'off',
    '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
    // Reject raw Tailwind palette names (`bg-blue-500`, `text-red-700`, …).
    // Only the project's semantic palette (success/warning/danger/info/neutral/accent)
    // is permitted; coloured utilities outside that set must go through a token.
    'no-restricted-syntax': [
      'error',
      {
        selector:
          'Literal[value=/(?:bg|text|border|ring|outline|divide|placeholder|caret|fill|stroke|from|to|via|shadow)-(?:slate|gray|zinc|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}/]',
        message:
          'Use a semantic Tailwind token (success/warning/danger/info/neutral/accent) instead of a raw palette name.',
      },
      {
        selector:
          'TemplateElement[value.raw=/(?:bg|text|border|ring|outline|divide|placeholder|caret|fill|stroke|from|to|via|shadow)-(?:slate|gray|zinc|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}/]',
        message:
          'Use a semantic Tailwind token (success/warning/danger/info/neutral/accent) instead of a raw palette name.',
      },
    ],
  },
  overrides: [
    {
      files: ['*.vue'],
      rules: {
        // Vue templates surface the same risk; lint by AST plus textual sweep.
        'vue/no-restricted-syntax': [
          'error',
          {
            selector:
              'VLiteral[value=/(?:bg|text|border|ring|outline|divide|placeholder|caret|fill|stroke|from|to|via|shadow)-(?:slate|gray|zinc|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}/]',
            message:
              'Use a semantic Tailwind token (success/warning/danger/info/neutral/accent) instead of a raw palette name.',
          },
        ],
      },
    },
  ],
  ignorePatterns: ['dist/', 'node_modules/', '.eslintrc.cjs'],
}
