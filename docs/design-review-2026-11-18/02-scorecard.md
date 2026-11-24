# Scorecard

Scores are based on repository evidence. Live frontend visual metrics were not available.

1. **Good design is innovative — 1/3**  
   Evidence: Native HA climate surface is reused (`climate.py:66-84`). This is appropriate but not a differentiated improvement.
2. **Good design makes a product useful — 1/3**  
   Evidence: Away cannot be configured for the documented date/hours/target flow; it silently falls back to 24 hours (`climate.py:192-210`, PLXE manual pp. 17–18).
3. **Good design is aesthetic — 2/3 (inferred)**  
   Evidence: No custom frontend is shipped; the integration uses native HA presentation. Visual quality was not directly inspected.
4. **Good design makes a product understandable — 1/3**  
   Evidence: “Timer Mode” hides the verified User Timer meaning, and “Away” hides its return deadline (`translations/en.json:57-64`, `climate.py:192-210`).
5. **Good design is unobtrusive — 2/3**  
   Evidence: The integration does not add a custom dashboard or duplicate climate controls. The hidden Away default is unobtrusive in the wrong way: it conceals important state.
6. **Good design is honest — 1/3**  
   Evidence: Away appears as a simple preset while writing an implicit 24-hour expiry. The result is deterministic but not disclosed at the point of control.
7. **Good design is long-lasting — 2/3**  
   Evidence: Native HA entities and official Dimplex concepts are used; the unresolved naming mismatch will age poorly as more timer features are added.
8. **Good design is thorough — 1/3**  
   Evidence: No UI exists for Away date/hours/target or schedule preview/editing; there is no explicit missing-capability explanation (`select.py:18-60`, capability matrix schedule boundary).
9. **Good design is environmentally friendly — 3/3 (inferred)**  
   Evidence: No frontend bundle or idle animation is added; native HA UI is reused.
10. **Good design is as little design as possible — 2/3**  
    Evidence: One climate entity is the right primary surface. The missing Away parameter surface is not simplification; it is hidden complexity.

**Total: 16/30 — REDESIGN the timer/Away control flow, while preserving the native climate surface.**
