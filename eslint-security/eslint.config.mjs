// Security rules for TypeScript and Angular code, used by the eslint-security wrapper.
// Every rule of @microsoft/eslint-plugin-sdl (including the Angular ones: no-angular-bypass-sanitizer,
// no-angular-sanitization-trusted-urls) and the DOM sinks of eslint-plugin-no-unsanitized, as warnings.
// The parser runs without type information, so no tsconfig of the analysed project is needed.
// Angular templates (.html) aren't parsed by these rules.
import tseslint from "typescript-eslint";
import sdl from "@microsoft/eslint-plugin-sdl";
import noUnsanitized from "eslint-plugin-no-unsanitized";

const sdlRules = Object.fromEntries(Object.keys(sdl.rules).map((rule) => [`@microsoft/sdl/${rule}`, "warn"]));

export default [
  {
    ignores: ["**/node_modules/**", "**/dist/**", "**/.angular/**", "**/coverage/**"],
  },
  {
    files: ["**/*.ts", "**/*.js", "**/*.mjs"],
    languageOptions: { parser: tseslint.parser },
    plugins: { "@microsoft/sdl": sdl, "no-unsanitized": noUnsanitized },
    rules: {
      ...sdlRules,
      "no-unsanitized/method": "warn",
      "no-unsanitized/property": "warn",
    },
  },
];
