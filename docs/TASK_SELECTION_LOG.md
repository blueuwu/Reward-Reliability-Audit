# Task Selection Log

Per Sections 6.4 and 27.6 of `CODEX_TASK_HUD_GRADER_RELIABILITY_AUDIT.md`, every
discarded task candidate is recorded here with the exact reason. This log is
evidence of engineering judgment. It is populated from Gate 3 (development
mining) onward. Accepted development tasks appear at the bottom.

| Date | Candidate repository | Baseline commit | Fix commit | Reason for rejection | Status |
|---|---|---|---|---|---|
| 2026-08-06 | skorokithakis/shortuuid (BSD-3) | — | 496cddf186b6598ebebe7be5c99c0715e7fc5ab5 | Closest candidate ("Fix string_to_int radix when alphabet_index is provided", #115) is authored by an automated bot ("patchwright"), dated June 2026, and fixes a docstring-vs-implementation mismatch with marginal behavioral substance; remaining commits are version bumps or refactors without regression tests. Not a classic upstream bug fix. | rejected |
| 2026-08-06 | pallets-eco/blinker (MIT) | — | 42561fd / 44d29b3 | Candidates concern "Exception ignored" during interpreter shutdown and weakref finalization — not deterministically testable in a hermetic `--network none` container; no clean regression tests. | rejected |
| 2026-08-06 | litl/backoff (MIT) | — | 732eaa34f782e31e891bbbd4aa524e42b27f3bc7 | "Get max_tries/max_time values for every call" (#160) is a genuine fix but its behavioral distinction (callable evaluated per call vs once) is subtle, touches two functions, and needs elaborate monkeypatching in the visible tests; higher authoring/verification risk than selected tasks. | rejected |
| 2026-08-06 | dbader/schedule (MIT) — `.at('MM:SS')` parsing | — | 1dab2d43acb6920dfa4c112a4b1c1f92e22f57f2 | The upstream regression test depends on a `mock_datetime` context manager, making authoritative tests time-sensitive and complex; the deterministic `repr`-on-partial-job fix was selected instead. | rejected |
| 2026-08-06 | hukkin/tomli (MIT) — dotted-key error / file-mode TypeError | — | 9e56735 / 8b962e1 | Larger diffs or meaningful regression tests require the full burntsushi TOML test-data corpus; the small, self-contained `loads` TypeError fix was selected instead. | rejected |
| 2026-08-06 | jpvanhal/inflection (MIT) — `titleize` non-ASCII | 1969b3a06a9ff06d023863e388cf8af01978d297 | e32443bb7dc1ba91a15336b5baab908d25f4b93a | Accepted (see below). | **accepted** |
| 2026-08-06 | hukkin/tomli (MIT) — `loads` TypeError | facdab0f5aacc5eb223753c42604d5de7bdaee9d | 4e245a4bbbefed99e550e196095ea65c851cf31d | Accepted (see below). | **accepted** |
| 2026-08-06 | dbader/schedule (MIT) — `repr` on partial job | a3ecd3548aff70bef15a61baee0d2d7f22f57992 | 3863effe15ec8239ba88c65a4231a67e43b116df | Accepted (see below). | **accepted** |

## Accepted development tasks (Gate 3)

| Task id | Repository | License | Baseline commit | Fix commit | Vendored tree sha256 |
|---|---|---|---|---|---|
| inflection-titleize | jpvanhal/inflection | MIT | 1969b3a06a9ff06d023863e388cf8af01978d297 | e32443bb7dc1ba91a15336b5baab908d25f4b93a | 685239fcd689d6f1b5269d9bf126e30ba8177e2470a0c7e0cf706eddcc6716bd |
| tomli-type-error | hukkin/tomli | MIT | facdab0f5aacc5eb223753c42604d5de7bdaee9d | 4e245a4bbbefed99e550e196095ea65c851cf31d | de0c07d25f53cd9da71c0e9b675ee6934ad6c1566f44bb378c73e06399416323 |
| schedule-repr-partial-job | dbader/schedule | MIT | a3ecd3548aff70bef15a61baee0d2d7f22f57992 | 3863effe15ec8239ba88c65a4231a67e43b116df | 181ce4d889a27ee2243eb76368c268c75b84b5db52561d08f4ebe69838d78bd5 |

All three accepted tasks are small, permissively licensed (MIT), pure-Python,
stdlib-only projects with fast pytest tests, a small reproducible upstream fix
commit, and no runtime dependency on network, services, or GPU. None appears in
popular coding benchmarks (SWE-bench and derivatives), so benchmark
contamination and answer leakage are avoided.
