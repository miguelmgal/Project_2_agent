# Bitácora del Proyecto — SupportOps Agent (AIQA-AGENT-002)

> Registro cronológico de avances, decisiones, problemas y aprendizajes.
> **Propósito:** que este proyecto se pueda **replicar** desde cero, y que quien tropiece con los mismos problemas encuentre aquí la solución sin volver a investigarla.

**Responsable:** Daniel · **Inicio:** 2026-07-31 · **Estimación:** 3 semanas (medio tiempo)
**Documentos relacionados:** [`PLAN_IMPLEMENTACION.md`](./PLAN_IMPLEMENTACION.md) · [`PROYECTO_2_Agente_SupportOps.md`](./PROYECTO_2_Agente_SupportOps.md)

---

## Cómo usar esta bitácora

1. **Una entrada por sesión de trabajo**, aunque la sesión sea de 30 minutos. Las entradas cortas y frecuentes valen más que un resumen semanal escrito de memoria.
2. **Escríbela al terminar la sesión, no al día siguiente.** El detalle que hace replicable un fix (la versión exacta, el mensaje de error literal) se olvida en horas.
3. **Todo problema que te cueste más de 15 minutos va al [Registro de problemas](#registro-de-problemas-y-soluciones)** con su solución. Ese registro es el activo más valioso del documento.
4. **Toda decisión técnica no obvia va al [Registro de decisiones](#registro-de-decisiones-adr-lite)**, con las alternativas que descartaste. "Por qué NO hicimos X" ahorra semanas a quien replique.
5. **Pega los mensajes de error literales.** El futuro tú (o un compañero) va a buscar por ese texto exacto.
6. Nunca pegues credenciales, ARNs completos, API keys ni datos reales de clientes. Usa `<REDACTED>`.

### Plantilla de entrada

```markdown
## AAAA-MM-DD — Día N · Fase X · [título corto]

**Tiempo invertido:** Xh
**Objetivo de la sesión:** ...

### Hecho
- ...

### Métricas (si aplica)
| Métrica | Valor | Umbral | Δ vs. anterior |
|---|---|---|---|

### Problemas encontrados
- **[P-XXX]** descripción → ver [Registro de problemas](#registro-de-problemas-y-soluciones)

### Decisiones tomadas
- **[D-XXX]** decisión → ver [Registro de decisiones](#registro-de-decisiones-adr-lite)

### Aprendizajes
- ...

### Siguiente paso
- ...
```

---

# Registro cronológico

## 2026-07-31 — Día 0 · Fase 0 · Planificación y validación del stack

**Tiempo invertido:** —
**Objetivo de la sesión:** analizar el spec del proyecto, validar que el stack propuesto sigue vigente en 2026, y producir un plan de implementación completo.

### Hecho
- Analizado el spec completo (`PROYECTO_2_Agente_SupportOps.md`).
- Investigado el estado real del ecosistema en julio 2026: LangGraph 1.x, `agentevals`, DeepEval, Bedrock + Claude 5, LangSmith vs. Langfuse, herramientas de red teaming.
- Producido [`PLAN_IMPLEMENTACION.md`](./PLAN_IMPLEMENTACION.md): stack completo con justificación y alternativas, arquitectura, estrategia de evaluación, seguridad, CI/CD, plan por fases de 21 días, riesgos.
- Creada esta bitácora.

### Estado del stack propuesto en el spec
Confirmado vigente en lo esencial. Tres actualizaciones y tres correcciones de diseño:

| Elemento del spec | Estado | Acción |
|---|---|---|
| LangGraph | ✅ Vigente (v1.0 desde oct-2025, ~400 empresas en producción) | Usar `StateGraph` de bajo nivel, no el prebuilt `create_agent` |
| Bedrock + "Claude Sonnet" | ⬆️ Concretar | `anthropic.claude-sonnet-5` (agente) + `anthropic.claude-opus-5` (juez) |
| LangSmith | ✅ Vigente | Añadir instrumentación OTel para portabilidad |
| "Trajectory eval nativa" | ⬆️ Tiene nombre | Paquete `agentevals` (`create_trajectory_match_evaluator`) |
| DeepEval | ✅ Vigente | `ToolCorrectnessMetric` + `TaskCompletionMetric` |
| Faker, SQLite, GitHub Actions | ✅ Vigente | Añadir seed fija, FTS5, y OIDC en Actions |
| "Garak o PyRIT" | ⬆️ Concretar | promptfoo (CI) + PyRIT (multi-turno). Garak es model-level, no aplica a la capa de aplicación |

### Problemas anticipados (aún no verificados en código)
- **[P-001]** Claude 5 rechaza `temperature`/`top_p`/`top_k` y `langchain-aws` podría enviarlos → ver registro.
- **[P-002]** `thinking` adaptativo siempre activo en Sonnet 5 sobre Bedrock, y conflicto con `tool_choice` forzado → ver registro.
- **[P-003]** Pérdida de determinismo por eliminación de `temperature` → ver registro.

> Los tres se validan en el **spike del Día 1**. Es deliberado: el 80% del riesgo del proyecto es la integración Bedrock ↔ Claude 5 ↔ langchain-aws, y descubrirlo en Fase 2 costaría el calendario.

### Decisiones tomadas
- **[D-001]** LangGraph `StateGraph` de bajo nivel en lugar del prebuilt.
- **[D-002]** Juez LLM distinto y más capaz que el agente (Opus 5 juzga a Sonnet 5).
- **[D-003]** `customer_id` inyectado desde el estado, nunca expuesto al LLM.
- **[D-004]** Escalación medida con matriz de confusión (recall/FPR), no accuracy.
- **[D-005]** CI en dos niveles (rápido determinista en PR / completo con LLM nocturno).
- **[D-006]** `search_knowledge_base` arranca con SQLite FTS5, no con vector store.
- **[D-007]** El golden set se escribe en Fase 1, **antes** del agente.

### Aprendizajes
- **El nombre correcto de la herramienta ahorra días.** El spec decía "trajectory eval en LangSmith" como si fuera una feature de la UI; en realidad es una librería específica (`agentevals`) con cuatro modos de comparación (`strict`/`unordered`/`subset`/`superset`). Buscar el término genérico no la encuentra.
- **Los frameworks de agentes convergieron, los de evaluación no.** Elegir orquestador en 2026 es casi trivial (LangGraph para grafos explícitos). Dónde está la decisión difícil es en la capa de evaluación, porque no hay una herramienta que cubra las cuatro métricas: hay que combinar `agentevals` + DeepEval + evaluadores propios.
- **La métrica que pide el cliente puede estar mal formulada.** El spec pedía "escalation accuracy ≥ 0.90". Con 10 de 40 tickets que deben escalar, un agente que **nunca** escala saca 0.75 — la métrica premia el comportamiento peligroso. Parte del trabajo de QA es detectar eso *antes* de construir la suite, no después.

### Siguiente paso
- **Día 1:** ejecutar el spike de Fase 0 y registrar aquí el resultado de P-001, P-002 y P-003 con versiones exactas.

---

## 2026-08-05 — Día 1 · Fase 0 · Repositorio y control de versiones

**Tiempo invertido:** ~0.5h
**Objetivo de la sesión:** conectar el proyecto al repositorio remoto y establecer las bases de control de versiones antes de escribir código.

### Hecho
- Repositorio remoto creado: [`miguelmgal/Project_2_agent`](https://github.com/miguelmgal/Project_2_agent) (estaba vacío).
- `git init -b main`, remoto `origin` configurado, primer commit y push.
- **`.gitignore`**: excluye `.env`, la DB generada (`*.db`, `*.sqlite`), checkpoints de LangGraph, cachés de pytest/mypy/ruff y resultados crudos de evaluación.
- **`.gitattributes`**: `* text=auto eol=lf` → LF en el repositorio, nativo en el working copy.
- Commit inicial con los 4 documentos base + spec original (1548 líneas).

### Decisiones tomadas
- **[D-009]** `.gitattributes` con `eol=lf` desde el primer commit → ver registro de decisiones.
- **[D-010]** La DB de juguete **no** se versiona; se reconstruye con `env/seed.py` (seed fija) → ver registro.

### Aprendizajes
- **Git avisó de la conversión CRLF en los 6 archivos del primer `git add`.** En Windows es fácil ignorar ese warning; ignorarlo significa que cuando CI corra en Linux, cada archivo aparecerá como modificado por completo y los diffs serán inservibles para revisar. Media hora de trabajo ahora evita esa clase de ruido durante todo el proyecto.
- **El `.gitignore` tiene un caso sutil que hay que documentar en el propio archivo:** los patrones típicos de virtualenv incluyen `env/`, pero en este proyecto `env/` es una carpeta de **código versionado** (`schema.sql`, `seed.py`, `knowledge_base/`). Se ignoran solo los artefactos de virtualenv que puedan caer dentro (`env/bin/`, `env/lib/`, `env/pyvenv.cfg`). Copiar un `.gitignore` genérico de Python habría hecho invisible media Fase 1.

### Siguiente paso
- **Spike de integración (2h):** `uv init`, dependencias pinneadas, y script mínimo que llame a `anthropic.claude-sonnet-5` vía `ChatBedrockConverse` con un tool-call real. Verificar y registrar aquí P-001, P-002 y P-003, y rellenar "Versiones verificadas".

---

## 2026-08-06 — Día 2 · Fase 0 · Andamiaje y spike de integración ✅ FASE CERRADA

**Tiempo invertido:** ~3h
**Objetivo de la sesión:** montar la base de ingeniería y ejecutar el spike que desbloquea la Fase 1.

### Cambio de proveedor: OpenAI en lugar de Bedrock

No hay acceso a Bedrock todavía. Se evaluó el impacto y resulta ser **~5% del proyecto**: un archivo (`llm.py`) y el `.env`. El grafo, las tools, el golden set, las 4 métricas, `agentevals`, DeepEval, los tests de seguridad y el CI son **agnósticos al proveedor**.

Decisión: **capa de modelo intercambiable** (`D-011`) en lugar de acoplarse a un proveedor. Coste cero y habilita un entregable que el spec no pedía: correr la misma suite contra dos proveedores y comparar métricas (*model-swap regression testing*).

⚠️ **Pendiente de confirmar con quien asignó el proyecto:** el spec justifica Bedrock con "aprendes Bedrock", que es un objetivo de aprendizaje explícito. Hay que saber si era requisito duro o camino sugerido.

### Hecho
- `pyproject.toml`: dependencias en grupos (principal/dev/test/eval) + extra `bedrock` preparado sin instalar. Ruff con reglas `S` (bandit), `ANN` y `T20`. Mypy `strict`.
- **Python fijado en 3.12** aunque la máquina tiene 3.14 (`D-013`).
- Marca de pytest `llm` → habilita el CI de dos niveles vía `pytest -m "not llm"`.
- `src/supportops/config.py`: `pydantic-settings` con `SecretStr`, `frozen=True` y validación *fail-fast*.
- `src/supportops/llm.py`: único archivo que conoce el proveedor; exhaustividad verificada por mypy con `assert_never`.
- `scripts/spike_llm.py`: diagnóstico de integración.
- `tests/test_config.py`: 11 tests deterministas, dos de seguridad.
- Identidad de git corregida a la cuenta `miguelmgal` (`D-014`); historia reescrita con mensajes en inglés y sin trailers de IA.

### Resultados del spike

| Modelo | Tool-calling | `temperature` | `seed` |
|---|---|---|---|
| `gpt-5.4-mini-2026-03-17` | ✅ | ✅ aceptado | ✅ aceptado |
| `gpt-5.5-2026-04-23` | ✅ | ✅ aceptado | ✅ aceptado |
| `gpt-5.5-pro-2026-04-23` | ✅ | ✅ aceptado | ❌ **rechazado** |

Los tres extrajeron `ORD-1042` del lenguaje natural y **ninguno inventó `customer_id`** (no está en el schema → no puede rellenarlo). Primera validación empírica de R1.

### Problemas encontrados
- **[P-001]** ✅ **CERRADO** — `temperature` **sí** se acepta en OpenAI. Era específico de Claude 5, no universal.
- **[P-004]** 🟢 **RESUELTO** — el `.env` no lo ve el SDK de OpenAI (`os.environ` ≠ archivo `.env`).
- **[P-005]** 🟡 **DOCUMENTADO** — los modelos `pro` van por la **Responses API**, no por Chat Completions.
- **[P-006]** 🟢 **RESUELTO** — la consola de Windows (cp1252) destroza caracteres no-ASCII.

### Decisiones tomadas
- **[D-011]** Capa de modelo agnóstica al proveedor.
- **[D-012]** Canary build para medir intentos de acceso cruzado.
- **[D-013]** Python 3.12, no 3.14.
- **[D-014]** Identidad de git local al repo, sin tocar la global.
- **[D-015]** Modelos **provisionales**; la elección definitiva se mide en Fase 3.
- **[D-016]** R4 (sin `temperature`) se mantiene aunque OpenAI la acepte.

### Aprendizajes
- **Hacer el spike primero cambió una premisa del plan.** El plan daba por hecho que `temperature` estaba bloqueada y construía toda la metodología estadística sobre eso. Es falso en OpenAI. La metodología estadística sigue siendo correcta, pero **por otra razón** (ver D-016). Si hubiera dado la suposición por buena, habría documentado un *por qué* equivocado — y en una review, un razonamiento correcto con premisa falsa se cae al primer empujón.
- **Un mensaje de error puede revelar arquitectura.** El fallo del modelo `pro` no fue "seed no soportado" sino `Responses.create() got an unexpected keyword argument 'seed'`. Ese nombre de función delata que hay **dos superficies de API distintas** por debajo, según el tier del modelo. Leer el error literal en vez de solo su categoría es lo que convirtió un "no funciona" en información arquitectónica.
- **Elegir modelo por intuición desperdicia el propio proyecto.** La pregunta "¿qué modelo uso?" es exactamente lo que la suite de evaluación responde. Decidirlo a ojo ahora y no medirlo en Fase 3 sería tener el instrumento y no usarlo. De ahí `D-015`.
- **Un alias flotante es una regresión invisible esperando.** `gpt-5.4-mini` puede reapuntar a otro modelo sin que ningún commit tuyo lo explique, y tus métricas cambian solas. Por eso se fija el **snapshot fechado**.
- **El coste de un cambio no es el coste de su código.** Añadir `temperature` son 8 líneas; el gasto real es **rehacer el baseline** y perder la comparabilidad del histórico. Igual que cambiar el prompt o el gold set.

### Siguiente paso
- **Fase 1:** entorno simulado (`schema.sql`, `seed.py`), repositorio con filtro obligatorio de cliente, las 5 tools, artículos de FAQ y el golden set de 40 tickets.

---

<!-- ▲ Añade las nuevas entradas ARRIBA de esta línea, en orden cronológico inverso o directo (elige uno y sé consistente). Recomendado: orden directo, más natural para replicar. -->

---

# Registro de problemas y soluciones

> **La sección más importante para replicar el proyecto.** Un problema por fila, con síntoma literal, causa raíz y solución verificada.
> Estados: `🔴 abierto` · `🟡 mitigado` · `🟢 resuelto` · `⚪ anticipado (sin verificar)`

---

### [P-001] Claude 5 rechaza parámetros de sampling (`temperature`, `top_p`, `top_k`)

**Estado:** 🟢 **CERRADO 2026-08-06** — verificado en el spike: **no aplica a OpenAI**
**Fase:** 0 · **Componente:** `agent/llm.py`, integración Bedrock

> **Resultado de la verificación:** los tres modelos de OpenAI probados
> (`gpt-5.4-mini-2026-03-17`, `gpt-5.5-2026-04-23`, `gpt-5.5-pro-2026-04-23`)
> **aceptan `temperature`**. El bloqueo es específico de Claude 5 / Opus 4.7+ y sigue
> siendo válido para la futura migración a Bedrock.
>
> **Aun así no usamos `temperature` (`D-016`)**, por dos razones que sobreviven al
> cambio de proveedor: (1) mantiene el código portable a Bedrock, y (2) fijarla
> ocultaría el no-determinismo que la suite está diseñada para medir — la métrica de
> estabilidad de trayectoria daría ~1.0 siempre, un número bonito y vacío.
>
> **Lección:** el plan original construía toda la metodología estadística sobre la
> premisa de que `temperature` estaba bloqueada. La premisa era falsa en OpenAI; la
> metodología sigue siendo correcta pero por otro motivo. Un razonamiento correcto
> con premisa falsa se cae en la primera review.

**Síntoma esperado (en Bedrock/Claude)**
```
botocore.errorfactory.ValidationException: An error occurred (ValidationException)
when calling the Converse operation: <parámetro> is not supported by this model
```
Error 400 en cualquier llamada, incluso con un payload por lo demás correcto.

**Causa raíz**
Los modelos Claude 5 (y Opus 4.7/4.8) **eliminaron** los parámetros de sampling. No están deprecados: están rechazados. `langchain-aws` puede enviar `temperature` con un valor por defecto aunque tú no lo pases explícitamente.

**Solución**
1. Instanciar `ChatBedrockConverse` **sin** `temperature`, `top_p` ni `top_k`.
2. Verificar el payload real que sale (logging del cliente boto3 o traza de LangSmith) — no asumir que "no pasarlo" significa "no enviarlo".
3. Para guiar el comportamiento del modelo se usa **prompting**, no sampling. Para controlar profundidad de razonamiento y coste, `output_config.effort` (`low`/`medium`/`high`/`xhigh`/`max`).

**Cómo evitarlo al replicar**
Añadir un smoke test en CI que haga una llamada real mínima y falle ruidosamente si el payload contiene parámetros de sampling. Este error aparece en runtime, no en tests unitarios.

---

### [P-002] `thinking` adaptativo y conflicto con `tool_choice` forzado en Bedrock

**Estado:** ⚪ anticipado — verificar en spike Día 1
**Fase:** 0 · **Componente:** `agent/llm.py`, `agent/graph.py`

**Síntoma esperado**
- `thinking: {type: "enabled", budget_tokens: N}` → error 400.
- Forzar `tool_choice: {type: "tool", name: "..."}` en Bedrock puede fallar o comportarse de forma inesperada, porque en Sonnet 5 sobre Bedrock el *adaptive thinking* está **siempre activo y no se puede desactivar**, y el `tool_choice` forzado en Bedrock requiere thinking desactivado.
- Existe además un issue abierto en `langchain-aws` (#647) sobre la lógica de `supports_tool_choice` cuando thinking está activo.

**Causa raíz**
Dos cambios simultáneos: (1) el presupuesto fijo de thinking se sustituyó por thinking adaptativo + `effort`; (2) Bedrock impone restricciones propias que **no coinciden** con la API directa de Anthropic. La documentación de Anthropic y el comportamiento de Bedrock divergen en este punto concreto.

**Solución (decisión de arquitectura, no workaround)**
**No diseñar el grafo asumiendo que se puede forzar una tool concreta.** En su lugar:
- `tool_choice: auto` + un system prompt que describa claramente cuándo usar cada tool.
- Un **nodo `GUARDA` determinista** que valide lo que el modelo decidió y corrija/escale si es incoherente.

Esto es más robusto de todos modos: un grafo que depende de forzar tools es un grafo frágil ante cambios de modelo.

**Cómo evitarlo al replicar**
- **Pinnear versiones exactas** de `langchain-aws`, `langgraph` y `boto3` que se hayan verificado funcionando, y anotarlas aquí.
- Documentar la región de Bedrock usada (la disponibilidad de modelos varía por región).
- No copiar ejemplos de la API directa de Anthropic sin verificarlos contra Bedrock.

---

### [P-003] Pérdida de determinismo: los evals se vuelven flaky

**Estado:** 🟡 mitigado por diseño
**Fase:** 3 · **Componente:** `eval/runners.py`, CI

**Síntoma**
El mismo ticket produce trayectorias distintas entre ejecuciones. Un test que pasa en local falla en CI sin que haya cambiado nada. La suite pierde credibilidad y la gente deja de mirarla.

**Causa raíz**
Al eliminarse `temperature` (ver P-001), desaparece la única palanca de reproducibilidad del modelo. **No es un bug: es la naturaleza del sistema bajo prueba.** El error es metodológico: tratar un sistema estocástico con aserciones binarias de una sola corrida.

**Solución (protocolo de medición)**
1. **Tres tipos de test, tres niveles de determinismo:**
   - Unit tests de tools → Python + SQLite con seed fija → **100% deterministas**, verde absoluto exigido.
   - Tests del grafo → **LLM fake** que devuelve secuencias de tool-calls predefinidas → deterministas, gratis, corren en cada PR.
   - Evals con LLM real → estocásticos → **K=3 ejecuciones por ticket**, gate sobre la media.
2. **El gate de CI nunca se aplica a una corrida individual.** Media + desviación estándar.
3. **Añadir `trajectory_stability` como métrica de primera clase:** % de tickets cuya trayectoria es idéntica en las K corridas. Un agente con tool-correctness 0.92 y estabilidad 0.55 es peor que uno con 0.90 y estabilidad 0.95 — el primero acierta por suerte.

**Aprendizaje generalizable**
La imposibilidad de fijar `temperature` convirtió un problema de configuración en una **mejora metodológica**. La estabilidad de trayectoria es una métrica que no estaba en el spec y que resultó ser una de las más informativas.

---

### [P-004] El archivo `.env` no lo ve el SDK del proveedor

**Estado:** 🟢 resuelto
**Fase:** 0 · **Componente:** `scripts/spike_llm.py`

**Síntoma**
```
openai.AuthenticationError: Error code: 401 - Incorrect API key provided
```
…con la key perfectamente escrita en `.env`. Es el error que más tiempo hace perder,
porque todo *parece* correcto y sospechas de la key en lugar del mecanismo de carga.

**Causa raíz**
**Un archivo `.env` no es una variable de entorno.** Es texto en disco; alguien tiene
que leerlo y cargarlo. En este proyecto conviven dos formas de acceder a credenciales:

| Componente | Lee de | ¿Ve el `.env`? |
|---|---|---|
| `Settings` (pydantic-settings) | `.env` + `os.environ` | ✅ configurado con `env_file=` |
| `OpenAI()` / `ChatOpenAI()` sin argumentos | solo `os.environ` | ❌ |

**Solución**
No `load_dotenv()`, sino **usar la capa de configuración del propio proyecto** y pasar
la key explícitamente:

```python
def _api_key() -> str:
    key = get_settings().openai_api_key
    if key is None:
        raise ValueError("OPENAI_API_KEY is not configured.")
    return key.get_secret_value()


client = OpenAI(api_key=_api_key())
```

**Por qué esta solución y no `load_dotenv()`:** con `load_dotenv()` tendrías **dos**
caminos distintos para leer secretos en el mismo proyecto, y tarde o temprano
divergen (uno valida, el otro no). Pasando por `Settings` hay un solo camino, y de
paso el spike ejercita el código de configuración real.

**Cómo evitarlo al replicar**
Regla general: **una sola vía de acceso a secretos en todo el proyecto.** Si un
componente de terceros no la usa, pásale el valor explícitamente en lugar de esperar
que adivine.

---

### [P-005] Los modelos `pro` usan otra API: la Responses API

**Estado:** 🟡 documentado (no bloquea, pero condiciona la elección del juez)
**Fase:** 0 · **Componente:** `llm.py`, evaluación

**Síntoma**
```
TypeError: Responses.create() got an unexpected keyword argument 'seed'
```
Solo con `gpt-5.5-pro-2026-04-23`. Los tiers `mini` y estándar aceptan `seed` sin más.

**Causa raíz**
Fíjate en el nombre de la función del error: **`Responses.create()`**, no
`Completions.create()`. `langchain-openai` enruta los modelos `pro` por la
**Responses API** en lugar de Chat Completions. Son **dos superficies de API
distintas**, con parámetros distintos.

**Implicación de diseño**
Elegir el modelo del juez no es solo elegir capacidad: es elegir **ruta de API**. Un
parámetro que funciona en el agente puede no existir en el juez. Es una razón
adicional (además del coste) para que el juez sea `gpt-5.5-2026-04-23` y no el `pro`.

**Cómo evitarlo al replicar**
No asumas que todos los modelos de un proveedor comparten superficie de API. Prueba
los parámetros **por modelo**, no por proveedor. El spike lo hace por eso.

---

### [P-006] La consola de Windows destroza caracteres no-ASCII

**Estado:** 🟢 resuelto
**Fase:** 0 · **Componente:** salida de consola de scripts

**Síntoma**
```
  in place (see PLAN_IMPLEMENTACION.md �6.4).
```

**Causa raíz**
La consola de Windows usa **cp1252** por defecto, no UTF-8, y no puede representar
caracteres como `§`, `→` o `✅`.

**Solución**
**ASCII puro en toda la salida de consola.** Se podría forzar UTF-8 en la consola,
pero eso depende del entorno de quien ejecuta — y un diagnóstico ilegible en la
máquina de otro es un diagnóstico inútil.

**Cómo evitarlo al replicar**
Los `.md` sí pueden llevar Unicode (los renderiza GitHub). Lo que se imprime en una
terminal, no.

---

### [P-XXX] (plantilla — copia esta estructura)

**Estado:** 🔴 abierto
**Fase:** X · **Componente:** ...

**Síntoma**
```
(pega el mensaje de error LITERAL aquí)
```

**Causa raíz**
...

**Solución**
1. ...

**Cómo evitarlo al replicar**
...

**Tiempo perdido:** Xh · **Referencias:** (issues, docs, commits)

---

# Registro de decisiones (ADR-lite)

> Una fila por decisión técnica no obvia. La columna de alternativas descartadas es la que evita que alguien repita un camino que ya se exploró.

| ID | Decisión | Alternativas descartadas | Por qué | Fecha | Estado |
|---|---|---|---|---|---|
| **D-001** | LangGraph `StateGraph` de bajo nivel | Prebuilt `create_agent`; OpenAI Agents SDK; AWS Strands; Pydantic AI; Bedrock Agents managed | El prebuilt oculta las transiciones que hay que auditar. Aquí el boilerplate *es* el entregable. Los managed son cajas negras: sin trazabilidad de decisiones no hay proyecto | 2026-07-31 | ✅ Vigente |
| **D-002** | Juez LLM más capaz que el evaluado (Opus 5 juzga a Sonnet 5) | Mismo modelo como juez; juez más barato (Haiku) | Usar el mismo modelo introduce *self-preference bias*: tiende a aprobar su propio output. Un juez debe ser ≥ el evaluado | 2026-07-31 | ✅ Vigente |
| **D-003** | `customer_id` inyectado desde el estado, oculto del schema del LLM | Pasarlo como parámetro de la tool (como decía el spec); validarlo en el prompt | Si el LLM controla el parámetro de identidad, **el LLM es tu control de acceso** — y un LLM es probabilístico. Cualquier jailcheck suficientemente bueno lo rompe. El fix tiene que ser estructural | 2026-07-31 | ✅ Vigente |
| **D-004** | Escalación con matriz de confusión (recall ≥ 0.95, FPR ≤ 0.10) | "Escalation accuracy ≥ 0.90" del spec | Con clases desbalanceadas la accuracy premia al agente que nunca escala. El falso negativo (no escalar cuando debía) es el error caro y hay que medirlo aparte | 2026-07-31 | ✅ Vigente |
| **D-005** | CI en dos niveles (rápido/determinista en PR, completo/LLM nocturno) | Todo en cada PR; todo nocturno | Evals con LLM en cada PR = minutos de espera, coste por commit y rojos intermitentes → la gente deja de mirar CI y el gate deja de servir | 2026-07-31 | ✅ Vigente |
| **D-006** | `search_knowledge_base` con SQLite FTS5 primero | Vector store del Proyecto 1 desde el inicio; `LIKE %query%` | FTS5 es determinista, cero infra y testeable. Si en Fase 3 se mide que falla en preguntas parafraseadas, se enchufa el índice vectorial → así hay un **antes/después medido**, que vale más en el informe que haber acertado a la primera | 2026-07-31 | ✅ Vigente |
| **D-007** | El golden set se escribe **antes** del agente | Escribirlo después, con el agente funcionando | Si lo escribes después, inconscientemente lo ajustas a lo que tu agente ya hace, y la suite deja de medir nada | 2026-07-31 | ✅ Vigente |
| **D-008** | LangSmith como backend primario, instrumentado vía OTel | Solo LangSmith nativo; Langfuse self-hosted; Laminar | LangSmith es coste cero con LangGraph y su vista de trayectorias es la mejor. Instrumentar con OTel (que LangSmith ya ingiere) hace la telemetría portable: cambiar de backend luego es cambiar un exporter | 2026-07-31 | ✅ Vigente |
| **D-009** | `.gitattributes` con `* text=auto eol=lf` desde el primer commit | No normalizar (dejar CRLF nativo de Windows) | Se desarrolla en Windows y CI corre en Linux. Sin normalizar, cada archivo aparece como modificado por completo en CI y los diffs se vuelven inservibles para revisión | 2026-08-05 | ✅ Vigente |
| **D-010** | La DB de juguete **no** se versiona | Commitear el `.db` para garantizar dataset idéntico | Con `Faker.seed()` fija, `env/seed.py` reproduce byte a byte el mismo dataset. Versionar un binario que se regenera añade ruido al diff sin aportar reproducibilidad. **Depende de que la seed nunca se toque sin registrarlo aquí** | 2026-08-05 | ✅ Vigente |
| **D-011** | Capa de modelo **agnóstica al proveedor** (`llm.py` es el único archivo que lo sabe) | Acoplarse a OpenAI ahora y refactorizar cuando llegue Bedrock | Cuesta lo mismo escribirlo bien desde el principio. Migrar pasa a ser una variable de entorno, y habilita *model-swap regression testing*: la misma suite contra dos proveedores | 2026-08-06 | ✅ Vigente |
| **D-012** | **Canary build** (`INSECURE_CANARY`) que expone `customer_id` al LLM, solo para red teaming | Solo el diseño seguro; solo el vulnerable | El diseño seguro hace la fuga *imposible*, pero también **imposible de medir**. El canary da un número ("el N% de los ataques lo intenta") en lugar de solo "lo hicimos imposible" | 2026-08-06 | ✅ Vigente |
| **D-013** | **Python 3.12**, no la 3.14 instalada | Usar la última versión disponible | El ecosistema de IA tarda meses en publicar wheels para versiones nuevas; `deepeval`/`langgraph` arrastran dependencias compiladas. En proyectos de IA la versión de Python es una decisión de compatibilidad, no de novedad | 2026-08-06 | ✅ Vigente |
| **D-014** | Identidad de git **local al repo** (`miguelmgal`), sin tocar la global | Cambiar la config global | La global es una identidad corporativa de otra empresa. Local evita mezclar identidades y commitear trabajo personal con email de trabajo. Se usa el email `noreply` de GitHub: siempre vincula a la cuenta y no expone el email personal en la historia pública | 2026-08-06 | ✅ Vigente |
| **D-015** | Modelos **provisionales**: `gpt-5.4-mini-2026-03-17` (agente) + `gpt-5.5-2026-04-23` (juez). Elección definitiva **medida en Fase 3** | Decidir ahora por intuición; usar el tier más alto por precaución | La pregunta "¿qué modelo?" es exactamente lo que responde la suite de evaluación. Decidirlo a ojo desperdicia el instrumento. Se itera en el tier barato (Fases 1–2, cientos de ejecuciones depurando) y en Fase 3 se compara con datos → **tabla coste/calidad como entregable extra**. Snapshots fechados, nunca alias flotantes | 2026-08-06 | 🔶 Provisional |
| **D-016** | **Sin `temperature`** aunque OpenAI la acepte | Usarla ya que está disponible; usarla solo en el juez | Dos razones que sobreviven al cambio de proveedor: portabilidad a Bedrock, y **fijarla ocultaría el no-determinismo que la suite mide** (la estabilidad de trayectoria daría ~1.0 siempre). En vez de asumir que el juez es estable, se **mide** su auto-consistencia en la calibración | 2026-08-06 | ✅ Vigente |
| **D-017** | **Modelo de datos de 4 tablas** (`customers`, `orders`, `tickets`, `faq_articles` + índice FTS5), con dinero en enteros, tablas `STRICT`, timestamps ISO-8601 UTC, FK con `ON DELETE RESTRICT` e invariantes de negocio como `CHECK` compuestos | Replicar la forma de un CRM real (direcciones, pagos, líneas de pedido, inventario, historial de estados) | Para un entorno simulado, profesional significa **determinista, restrictivo, justificado y documentado** — no "parecido a un CRM". Cada columna existe porque habilita un caso de prueba concreto; si no puedo nombrar el caso, la columna sobra. **Omitido a propósito:** direcciones, pagos, inventario, líneas de pedido, historial de estados, autenticación real — ninguna de las 5 tools los toca, serían columnas muertas. Los invariantes son la pieza clave: si un pedido incoherente **no puede existir**, entonces una respuesta mala del agente es culpa del agente y no de un dato corrupto, y eso es lo que hace **interpretables** los resultados de la evaluación en lugar de abrir una investigación de dos horas cada vez | 2026-08-07 | ✅ Vigente |
| **D-018** | **`tier` (`standard`/`premium`) se incluye Y se expone al agente vía `lookup_customer`** | No incluirlo (alcance mínimo); incluirlo sin exponerlo | Habilita un caso de seguridad que ninguna otra columna habilita: el agente **sabe** que eres premium pero **no tiene ninguna tool** para dar trato preferente. Un agente flojo improvisa ("como cliente premium agilizo su envío") = promesa que la empresa no puede cumplir → **OWASP LLM08, Excessive Agency**. Es un fallo distinto y más sutil que prometer reembolsos: aquí el agente tiene información legítima y la usa para inventar una acción. **Matiz clave:** si ninguna tool devuelve el `tier`, el agente nunca lo ve y la columna está muerta — la decisión real no es "¿añado la columna?" sino "¿lo expongo?". Condiciones: documentado como trampa, no como funcionalidad, y **ninguna tool acepta `tier` como parámetro de decisión**. Coste: 1 columna + ~2 tickets del golden set + 1 test | 2026-08-07 | ✅ Vigente |
| **D-019** | **Sin migraciones** (Alembic/Flyway). Esquema declarativo + base efímera: se recrea con `env/seed.py`, no se migra | Alembic desde el inicio "para que sea profesional" | Las migraciones existen para cambiar el esquema **sin perder los datos que ya hay dentro**. Aquí no hay datos que preservar: la base es un fixture desechable que se regenera en segundos y está en `.gitignore` (D-010). Añadir una herramienta que no resuelve un problema real es *cargo cult* — copiar la forma sin la función. Mismo patrón que la infraestructura inmutable: no parcheas el servidor, lo recreas desde su definición. **Fecha de caducidad de esta decisión:** si el proyecto acumula estado no regenerable (p. ej. memoria persistente del agente entre sesiones, uno de los stretch goals), las migraciones pasan a ser obligatorias y la herramienta es **Alembic** | 2026-08-07 | ✅ Vigente |
| **D-020** | Tests de esquema **de invariante**, no de snapshot. Más un test futuro de **cobertura esquema ↔ golden set** | Snapshot del DDL; lista fija de enums en el test | Un test cuyo arreglo es "regenerar" se arregla por reflejo, sin leer el diff — detecta el cambio pero no protege nada, y falla ante cambios inofensivos (añadir una columna, reordenar), lo que entrena a la gente a ignorarlo. Un invariante roto **no se puede arreglar regenerando**: o restauras la restricción o borras el test, y borrar un test es visible en la revisión. Para el crecimiento del dominio, en lugar de fijar la lista de estados se derivará la **cobertura**: cada estado que el esquema permite debe tener ≥1 ticket del golden set que lo ejercite — así el arreglo no es "editar la lista" sino "escribir el caso de prueba". Los valores se leen del DDL (`allowed_check_values`), nunca duplicados en Python: dos copias derivan | 2026-08-07 | ✅ Vigente |

---

# Historial de métricas

> Una fila por corrida completa de la suite de evaluación. Es el gráfico de progreso del proyecto y la evidencia del informe final.
> `commit` + `prompt_hash` permiten reconstruir exactamente qué se midió.

| Fecha | Commit | Prompt hash | Tool correctness | Trajectory match | Task completion | Escal. recall | Escal. FPR | Stability | Seguridad | Notas |
|---|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — | *baseline pendiente (Fase 3)* |

**Umbrales de gate:** tool-correctness ≥ 0.90 · trajectory ≥ 0.85 · task completion ≥ 0.85 · escalation recall ≥ 0.95 · escalation FPR ≤ 0.10 · stability ≥ 0.80 · seguridad = 100%
**Regla de regresión:** una caída > 5 puntos respecto a la última corrida de `main` falla el gate aunque el valor absoluto siga sobre el umbral.

---

# Versiones verificadas

> Rellenar tras el spike del Día 1. **Sin esto, el proyecto no es reproducible.**

**Verificado el 2026-08-06** con `uv run python -m scripts.spike_llm`.

| Componente | Versión | Notas |
|---|---|---|
| Python | **3.12.10** | Fijado en `.python-version`. NO usar 3.14 (D-013) |
| uv | 0.11.31 | |
| langgraph | 1.2.10 | |
| langchain | 1.3.14 | |
| langchain-core | 1.5.3 | |
| langchain-openai | 1.4.1 | |
| openai | 2.53.0 | |
| pydantic | 2.13.4 | |
| langsmith | 0.10.16 | |
| agentevals | 0.0.9 | |
| deepeval | 4.1.5 | |
| faker | 40.36.0 | |
| ruff | 0.16.1 | |
| **Proveedor** | **OpenAI** | `LLM_PROVIDER=openai`. Bedrock pendiente de acceso |
| Model ID (agente) | `gpt-5.4-mini-2026-03-17` | 🔶 provisional (D-015) |
| Model ID (juez) | `gpt-5.5-2026-04-23` | 🔶 provisional (D-015). NO el `pro`: otra API (P-005) |

> **Snapshots fechados, no alias.** `gpt-5.4-mini` puede reapuntar a otro modelo sin
> que ningún commit lo explique, y las métricas cambian solas. El sufijo de fecha es
> lo que hace comparable el histórico.

**Pendiente para cuando llegue Bedrock:** `langchain-aws`, `boto3`/`botocore`, región,
y verificar `P-002` (thinking adaptativo y `tool_choice`), que sigue sin comprobar.

---

# Guía de replicación (rellenar al cierre)

> Al terminar el proyecto, condensa aquí los pasos mínimos para levantarlo desde cero. Si alguien no puede ejecutarlo siguiendo solo esta sección, la bitácora no cumplió su función.

1. Prerrequisitos (cuenta AWS, acceso a modelos en Bedrock, cuenta LangSmith)
2. `uv sync` + variables de entorno (`.env.example`)
3. Seed de la base: `uv run python env/seed.py`
4. Smoke test de conectividad: `uv run pytest tests/test_smoke.py`
5. Correr el agente sobre un ticket
6. Correr la suite de evaluación
7. **Los 3 errores que te vas a encontrar y cómo resolverlos** → P-001, P-002, P-003
