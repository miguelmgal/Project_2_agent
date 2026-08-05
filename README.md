# 🎫 SupportOps Agent

> Agente autónomo de soporte al cliente construido con **LangGraph** sobre **Amazon Bedrock**, con una suite de
> **evaluación de trayectorias** que corre en CI y bloquea regresiones.

[![CI](https://img.shields.io/badge/CI-pendiente-lightgrey)]()
[![Fase](https://img.shields.io/badge/fase-0%20·%20setup-blue)]()
[![Python](https://img.shields.io/badge/python-3.12+-blue)]()

**ID:** `AIQA-AGENT-002` · **Estado:** 🚧 en desarrollo (Fase 0 de 5)

---

## Qué hace

Resuelve tickets de soporte de punta a punta: entiende el problema, decide qué hacer, usa herramientas reales
(base de conocimiento, consulta de pedidos, creación de tickets) y responde o ejecuta la acción —
**escalando a un humano cuando no está seguro o cuando el cliente está molesto.**

## Qué demuestra (el objetivo real)

El agente es el **sujeto de prueba**, no el entregable. Lo que se demuestra es el **testing de agentes**, que es
un mundo distinto al testing de RAG: aquí no evalúas una sola respuesta, evalúas **la trayectoria completa de
decisiones**.

| Métrica | Qué responde | Umbral |
|---|---|---|
| **Tool-call correctness** | ¿Llamó las herramientas correctas? | ≥ 0.90 |
| **Trajectory match** | ¿Tomó el camino eficiente, o dio vueltas? | ≥ 0.85 |
| **Task completion** | ¿Resolvió realmente el ticket? (LLM-as-judge calibrado) | ≥ 0.85 |
| **Escalation recall / FPR** | ¿Sabe cuándo **no** actuar? | ≥ 0.95 / ≤ 0.10 |
| **Trajectory stability** | ¿Es reproducible, o acierta por suerte? | ≥ 0.80 |
| **Seguridad** | Cero fuga de datos cruzados, cero acción no autorizada | 100% |

> Un agente puede dar la respuesta correcta **por el camino equivocado** — adivinando, o accediendo a datos que no
> le correspondían. Los tres casos pasan un test de output y los tres son bugs.

## Documentación

| Documento | Para qué |
|---|---|
| **[`PLAN_IMPLEMENTACION.md`](./PLAN_IMPLEMENTACION.md)** | Stack con alternativas evaluadas, arquitectura, estrategia de evaluación, seguridad, CI/CD y plan por fases |
| **[`CLAUDE.md`](./CLAUDE.md)** | **Reglas normativas para agentes de IA** que trabajen en este repo. Lectura obligatoria antes de la primera edición |
| **[`BITACORA.md`](./BITACORA.md)** | Registro de avances, **problemas y sus soluciones**, decisiones (ADR-lite) e historial de métricas |
| **[`PROYECTO_2_Agente_SupportOps.md`](./PROYECTO_2_Agente_SupportOps.md)** | Spec original del proyecto |
| `EVALUATION_REPORT.md` | *(Fase 5)* Informe de las métricas con trazas y mapeo OWASP LLM Top 10 |

**¿Vas a replicar este proyecto?** Empieza por `BITACORA.md` → *Guía de replicación*, y lee el
*Registro de problemas y soluciones*: los tres primeros (`P-001`, `P-002`, `P-003`) te ahorrarán varias horas
de depuración de la integración con Bedrock.

## Stack

| Capa | Herramienta |
|---|---|
| Orquestación | LangGraph 1.x (`StateGraph` explícito) |
| LLM agente / juez | Bedrock · `claude-sonnet-5` / `claude-opus-5` |
| Entorno simulado | SQLite + FTS5 + Faker (seed fija) |
| Observabilidad | LangSmith + OpenTelemetry |
| Evaluación | `agentevals` + DeepEval + evaluadores propios |
| Red teaming | promptfoo + PyRIT |
| Tooling | uv · ruff · mypy `--strict` · pytest |

Justificación de cada elección y alternativas descartadas: [`PLAN_IMPLEMENTACION.md`](./PLAN_IMPLEMENTACION.md) §2.

## Arquitectura

```
ticket ──► AUTENTICAR ──► RAZONAR ◄─────────────┐
           (sin LLM,      (Sonnet 5)            │
            inyecta          │                  │
            identidad)       ▼                  │
                    ┌────────┴────────┬─────────┴──────┐
                    ▼        ▼        ▼                │
                EJECUTAR  PREGUNTAR  RESPONDER  ESCALAR│
                 TOOL     AL USUARIO  Y CERRAR   (HITL)│
                    │                                  │
                    ▼                                  │
                 GUARDA (sin LLM: valida, corta bucles)┘

Todo nodo + tool-call ──► LangSmith
```

Dos nodos deterministas hacen el trabajo pesado de seguridad y fiabilidad:
- **`AUTENTICAR`** resuelve la identidad del cliente e **inyecta** el `customer_id` en las herramientas, de modo que
  el LLM nunca lo ve en el schema → la fuga de datos cruzados es *estructuralmente imposible*, no "improbable".
- **`GUARDA`** valida los resultados y corta el bucle si el agente da vueltas.

## Cómo empezar

> ⚠️ Pendiente hasta que cierre la Fase 0. Los comandos vivirán en [`CLAUDE.md`](./CLAUDE.md) §5.

```bash
git clone https://github.com/miguelmgal/Project_2_agent.git
cd Project_2_agent
uv sync
cp .env.example .env          # rellenar credenciales AWS y LangSmith
uv run python env/seed.py     # generar la base de datos de juguete
```

## Estado por fases

- [x] **Fase 0** — Setup, repo y spike de integración *(en curso)*
- [ ] **Fase 1** — Entorno simulado, 5 herramientas y golden set de 40 tickets
- [ ] **Fase 2** — El agente (grafo, estado, prompts)
- [ ] **Fase 3** — Suite de evaluación ⭐
- [ ] **Fase 4** — Seguridad y CI/CD
- [ ] **Fase 5** — Informe y demo

## Licencia

Proyecto de aprendizaje interno. Todos los datos son ficticios, generados con Faker.
