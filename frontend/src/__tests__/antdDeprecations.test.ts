// antd 6 keeps deprecated props working but logs a warning in dev builds
// and drops them in the next major. Scan the sources so a reintroduced
// spelling fails here instead of in a console nobody reads.
const sources = import.meta.glob<string>(
  ["../**/*.tsx", "!../**/__tests__/**", "!../**/*.test.tsx"],
  { query: "?raw", import: "default", eager: true },
);

const DEPRECATED: [RegExp, string][] = [
  [/<Space\b[^>]*\bdirection=/, "Space direction → orientation"],
  [/\bdestroyOnClose\b/, "destroyOnClose → destroyOnHidden"],
];

test.each(DEPRECATED)("no deprecated antd prop: %s", (pattern, rename) => {
  const hits = Object.entries(sources)
    .filter(([, code]) => pattern.test(code))
    .map(([path]) => path);
  expect(hits, rename).toEqual([]);
});
