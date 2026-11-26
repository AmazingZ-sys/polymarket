import ts from "typescript-eslint";
import vue from "eslint-plugin-vue";
export default [
  {
    ignores: [
      "**/dist/**",
      "**/node_modules/**",
      "**/test-results/**",
      "**/.venv/**",
      "**/playwright-report/**",
    ],
  },
  ...ts.configs.recommended,
  ...vue.configs["flat/essential"],
  {
    files: ["**/*.vue"],
    languageOptions: {
      parserOptions: { parser: ts.parser, extraFileExtensions: [".vue"] },
    },
  },
  {
    rules: {
      "vue/multi-word-component-names": "off",
      "@typescript-eslint/no-explicit-any": "warn",
    },
  },
];
