# S5: tree-sitter on Windows

- `tree_sitter_language_pack.get_parser(lang)` works for `typescript`, `tsx`, `javascript` and `python` (wheels, no compiler).
- A TS class parses into `import_statement → import_clause/named_imports/string_fragment` and
  `export_statement → class_declaration → type_identifier/class_body`, with constructor parameter properties
  (`private tokens: TokenService`) visible as `required_parameter` with an accessibility modifier and type annotation.

**Decision:** Phase 3 walks the tree-sitter nodes directly (a small explicit visitor per language) rather than `.scm` query files:
it is easier to debug and to unit-test with snippet tables.
