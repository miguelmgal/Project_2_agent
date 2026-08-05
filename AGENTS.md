# AGENTS.md

Las reglas de este proyecto para agentes de IA viven en **[`CLAUDE.md`](./CLAUDE.md)**.

Este archivo existe para que cualquier agente que busque `AGENTS.md` (la convención abierta
adoptada por la mayoría de herramientas) encuentre el punto de entrada correcto.

**No dupliques contenido aquí.** `CLAUDE.md` es la única fuente de verdad; mantener dos copias
garantiza que se desincronicen.

## Lectura obligatoria antes de la primera edición

1. **[`CLAUDE.md`](./CLAUDE.md)** — reglas normativas, stack, convenciones, definición de "terminado".
   Presta atención especial a las **5 reglas inviolables** (§3).
2. **[`BITACORA.md`](./BITACORA.md)** — problemas ya resueltos (P-001…) y decisiones ya tomadas (D-001…).
   Revísalo antes de investigar cualquier problema: probablemente ya esté ahí.
3. **[`PLAN_IMPLEMENTACION.md`](./PLAN_IMPLEMENTACION.md)** — arquitectura, estrategia de evaluación y fase actual.

## Las tres reglas que más se incumplen

- 🔴 El **`customer_id` nunca lo controla el LLM** — se inyecta desde el estado del grafo (`CLAUDE.md` §3 R1).
- 🔴 **Nunca** pases `temperature` / `top_p` / `top_k` al modelo: Claude 5 responde 400 (`CLAUDE.md` §3 R4).
- 🔴 **Nunca** ajustes el golden set para que pase el agente (`CLAUDE.md` §3 R3).
