# Proceso: Spec Driven Development

La spec manda sobre el código. Si el código y la spec discrepan, uno de los dos
está mal, y se decide explícitamente cuál.

## Documentos

La spec vive en `specs/mvp/` con cuatro ficheros, que se leen en este orden.
La fase 2 (bot, D-31) y las ampliaciones posteriores siguen en la misma carpeta,
como versiones nuevas de `requirements.md`:

| Fichero | Responde a | Lo cambia |
| --- | --- | --- |
| `requirements.md` | **Qué** y **por qué**: alcance, RF, RNF, criterios de aceptación | Solo el dueño del producto, subiendo la versión |
| `design.md` | **Cómo**: arquitectura, interfaces, flujos, formatos | Quien implementa, antes de implementar |
| `tasks.md` | **En qué orden**: tareas pequeñas trazadas a RF/RNF/CA | Quien implementa, al cerrar cada tarea |
| `decisions.md` | Decisiones tomadas y preguntas abiertas resueltas | Cualquiera, append-only |

`specs/archive/` guarda la historia y no hace falta leerla para trabajar:
`changelog.md` (qué cambió en cada versión de `requirements.md`),
`tasks-done.md` (el detalle de cada tarea cerrada) y `handoff-historia.md`
(estados viejos de `HANDOFF.md`).

## Identificadores

Todo lo que se puede verificar tiene un ID estable. Los IDs no se reutilizan ni
se renumeran; si un requerimiento se elimina, queda tachado con su ID.

| Prefijo | Qué es | Dónde se define |
| --- | --- | --- |
| `RF-NN` | Requerimiento funcional | `requirements.md` |
| `RNF-NN` | Requerimiento no funcional | `requirements.md` |
| `CA-NN` | Criterio de aceptación (del MVP, de la fase 2 o de una ampliación) | `requirements.md` |
| `PA-NN` | Pregunta abierta | `requirements.md` |
| `D-NN` | Decisión | `decisions.md` |
| `T-NN` | Tarea | `tasks.md` |

## Ciclo de una tarea

1. **Elegir** la primera tarea `todo` de `tasks.md` cuyas dependencias estén `done`.
2. **Comprobar la spec.** Si la tarea depende de una `PA-NN` sin resolver, se
   resuelve primero (y se anota en `decisions.md`) o se trabaja con el supuesto
   por defecto escrito en la pregunta.
3. **Test primero.** Los tests que la tarea lista se escriben antes que el
   código y se marcan con el requerimiento que verifican:

   ```python
   @pytest.mark.spec("RF-10", "CA-04")
   def test_rechaza_cruce_de_orden_propia(): ...
   ```

4. **Implementar** lo mínimo para que pasen. Nada que no esté en la tarea.
5. **Cerrar**: `uv run pytest`, `uv run ruff check`, `uv run python tools/spec_trace.py`,
   y marcar la tarea `done` en `tasks.md`, dejando solo su cabecera; el cuerpo
   pasa a `specs/archive/tasks-done.md`.

## Cambios de alcance

`requirements.md` §Fuera de alcance es vinculante. Una idea nueva no se
implementa: se apunta como `PA-NN` o se propone una nueva versión del documento.
Si durante el diseño se descubre que un RF es ambiguo o imposible (por ejemplo,
por la API de un venue), se para, se documenta en `decisions.md` y se ajusta
la spec antes de seguir.

## Trazabilidad

`tools/spec_trace.py` cruza los IDs definidos en `requirements.md` con las marcas
`@pytest.mark.spec(...)` de `tests/` e informa qué RF, RNF y CA no tienen
ningún test. Con `--strict` falla si queda alguno; ese es el gate para declarar
el MVP terminado.
