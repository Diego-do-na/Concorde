# Known Gaps and Cross-Task Dependencies

This file tracks integration points between parallel tasks that may not be
obvious from code alone. When a gap is resolved, remove its entry.

---

## [RESOLVED] /analyze no publica al feed

**Status**: ✓ Resuelto en el merge de T027+T028.

T027 implementó el feed, T028 implementó /analyze. Durante el rebase de T027
sobre T028, se agregó `state.feed.push(parsed.call_id.clone(), &analysis)` en
routes/analyze.rs línea 59 (función `process`, inmediatamente después de
obtener el Analysis del pipeline). Tanto /detect como /analyze ahora publican
al feed correctamente.
