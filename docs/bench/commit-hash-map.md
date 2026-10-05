# Commit hash map (history rewrite, 2026-10-04)

Before publishing, every commit message was rewritten to drop a `Claude-Session:` trailer (a private session
link). Nothing else changed: file contents, authors, dates and the `Co-Authored-By` lines are identical, so the
**frozen hashes** recorded by benchmark runs (content hashes, e.g. `2c56c658bc5f10f9`) are still valid. The
`git` field inside `bench/results/*/meta.json` still holds the **old** commit id — look it up here.

| old commit | new commit | subject |
|---|---|---|
| `0c86e66` (0c86e66d6918542a41a0d6ae1dd1562a4a2375ca) | `c711b81` (c711b8135e28a47bd5ed0276e0063b7fb2bc3cda) | feat: storage core with schema registry and append-only event ledger |
| `db3cc17` (db3cc17b64bb3d03026de47a847e9cded841cc62) | `83bcce6` (83bcce654886b41f7b9d5a150ecd47a0699e7242) | feat: question pipeline, record tool and MCP server |
| `28d607b` (28d607b077f221a9a36b502a9f94657ec2c16a0c) | `749e10f` (749e10fd5708daeeb497a33dcd4468b470575488) | feat: benchmark harness (generator, three renderings, graded questions) |
| `d60878a` (d60878a06f48d16537e13679fa9f57cc2266506a) | `09e4517` (09e45170654a56d2b8f8eda1c9f67d503e8cfb4f) | feat(bench): reduced v0 plan, narration reusing M, resumable runs |
| `025c13a` (025c13a7289fb8fe9cc565b1c1059ed5ba16b75d) | `6f336c9` (6f336c96a3f6d09440f9d1ab91a421e36bb95eb7) | docs(bench): v0 reduced benchmark results, README table and scale curves |
| `16e1f35` (16e1f35debb5ec0da0b16a01a0d6a64a3060c517) | `1a5f6ff` (1a5f6ff5e34b40f2df57974075a910c4c38b948b) | docs: task 0004 (v1 accuracy) approved with review conditions; ADR-0012 |
| `38620ee` (38620ee8faea0c05ccf0da33e8d427971bf5825b) | `fb66ecd` (fb66ecdbb33491662e8c4a92496e37ec620ff33f) | feat(query): reached filter, having, order_by_event (task 0004 ①②) |
| `e925f2c` (e925f2c0ad2855cc371d42a9e22dd3bd518a5de2) | `756965b` (756965ba70e8fe9deabcf657caa2dabd6c4a8bcb) | feat(bench): harness v2 — eval set v2, same-hint condition, cost cap (task 0004 ③) |
| `3569f7e` (3569f7ea504d8af3b480af2dbfae70b95ccf6769) | `ed070a8` (ed070a846d500f3421c0938251ebeff348aa68d6) | fix(query): accept measure names in any language; v1 regression results |
| `5ca9882` (5ca9882b8a6cf358c8440a7699d631327ecdfbe9) | `4fb00b8` (4fb00b89bc22569104a64e81d81872c54857812b) | feat(bench): eval set v2 unseen answer keys (V1-V9) and the v2 plan |
| `91a7ee2` (91a7ee2152497696b60370037487ac598171018d) | `64d4d5c` (64d4d5c793dcc55272b10f4366f7f93f4c6a2b41) | docs(bench): dev regression rerun after the measure-name fix (11/12 at 100 and 1,000) |
| `6eabf2a` (6eabf2a383747304b5845186f438d2375ca08191) | `3891f84` (3891f848901bd76ed38389d5ed9747b7bb0fc0f5) | feat(bench): per-system models (2x2 plan); opus pilot results |
| `832de04` (832de046718313c1d25dde9ab9074aa5a40cc37b) | `bf6ba6b` (bf6ba6b3cb546a14dcbf50acc606a560131a24a6) | fix(bench): freeze the whole package, schemas and harness |
| `17a7263` (17a7263ede06ed6858fd51223bb288ac68c97d41) | `6387a44` (6387a44f4ff32bb750d7b568aa7c52bc6e6e7856) | docs(bench): v2 results at 100 records (haiku/opus 2x2), README table, task 0005 |
| `9327925` (93279252cf61e64fb252c71d789e9e58ab1cb5d2) | `f883e30` (f883e30ac2066dc07122f1025d3fee22d66c5ed7) | fix(query): v1.1 — five interpreter weaknesses found in v2; eval set v3 |
| `121305f` (121305ff69784b04edb0d1b256fec3e56912ee86) | `802e259` (802e259ff300346fbc12f319108c7d239df3a7c2) | docs(bench): v1.1 regression — M-haiku 26/29 on the v2 data (dev), 1 repetition |
| `438f3bb` (438f3bba18a1a4bc9457d282cd6bb4bf7dc4628e) | `41e4bb3` (41e4bb329467a791ec0f1093b121225c15d8525c) | feat(bench): eval set v3 unseen answer keys (W1-W9), v3 plan, no-answer column |
| `ae52f74` (ae52f747c313bb00512af6ff78cb134ad8d4e58d) | `92d4633` (92d46333fa4c46c1a6d261b2e5e498118b19c08e) | docs(bench): v3 results, per-question tables, README for publication |
| `c3d9b84` (c3d9b84e6cc0857d4cf48a4a42f05daee4fc1b06) | `4052d5d` (4052d5d10af1545d392a8f67fb77cec027ec8d52) | docs(bench): v3 B1-haiku results, README complete |

# Commit hash map (history rewrite, 2026-10-05)

Before publishing task 0008, example strings that named real securities (a code and a nickname from the author's own
records) were replaced by fictional ones ("바이오주(900001)", "샘플펀드(900002)") in three files — ADR-0019,
`keeper/identity.py` docstrings and one example in the interpreter's system prompt — and in one commit message. Nothing
else changed. Commits before `9e892aa` have identical contents, so their benchmark frozen hashes stay valid. From
`9e892aa` on, the interpreter prompt differs by that example string: the step-3b regression (`v1.8-regression`, frozen
`245233b797a2ec1c`) measured the earlier text and was re-run on the rewritten code (`v1.9-regression`).

| old commit | new commit | subject |
|---|---|---|
| `5f0e36e` | `5293617` | docs: v2 roadmap (task 0008) and ADRs 0015-0017 |
| `1fbc03f` | `3f4144e` | fix(schema): starting with older schema files keeps a database that moved ahead |
| `a3ef66d` | `8880398` | feat: query log (ADR-0015, task 0008 step 1) |
| `81cdaa2` | `018a6de` | docs(bench): query-log regression — dev questions on eval set v3, M-haiku 27/28, M-opus 28/28 |
| `f8080a0` | `903e166` | feat(query): elapsed-time conditions and default readings for vague words (ADR-0018, task 0008 step 2) |
| `f439963` | `5a3f972` | fix(schema): schema files may skip versions when loaded |
| `1994b28` | `c5a8711` | docs(bench): step-2 regression — dev questions on eval set v3, M-haiku 27/28, M-opus 27/28 |
| `0051462` | `2d8e448` | fix(bench): converter reports the average when count measures come along |
| `b27a544` | `d316bc1` | feat(schema): relation notes between record types (ADR-0017, task 0008 step 3) |
| `341be5e` | `5b09c59` | docs(bench): step-2 regression rerun with the fixed converter — M-haiku 26/28, M-opus 28/28 |
| `157eaf0` | `c30f3d9` | feat: idempotent import helper and example investment schemas (task 0008 step 3a) |
| `a7f34c4` | `86c81e3` | docs(bench): step-3a regression — dev questions on eval set v3, M-haiku 27/28, M-opus 28/28 |
| `03fa4c2` | `9e892aa` | feat(identity): external identifier fields are matched first (ADR-0019, task 0008 step 3b) |
| `0f4d31c` | `86a5a04` | feat(importer): change_kind — state records change with updated events at the source's date |
| `3ff961b` | `5d39bdb` | fix(importer): vanished records only among the records a source owns |
| `1e059f8` | `398dd3a` | fix(query): relation-linked types are shown to the interpreter; plan cache keyed by interpreter |
| `2ba9723` | `0634344` | docs(bench): step-3b regression — dev questions on eval set v3, M-haiku 26/28, M-opus 28/28 |
