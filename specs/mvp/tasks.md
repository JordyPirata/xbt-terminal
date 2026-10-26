# Terminal BTCB2 — Tareas MVP

Estados: `todo` · `doing` · `done` · `blocked (PA-NN)`.
Una tarea se cierra cuando sus tests pasan, `tools/spec_trace.py` lo refleja y no
queda nada fuera de lo que la tarea dice.

Las tareas `done` quedan aquí en una línea (título, trazas y decisiones). Su
detalle (qué pedía y qué se hizo) está en `specs/archive/tasks-done.md`; al
cerrar una tarea, su cuerpo se mueve allí.

## M0 — Base

### T-01 Esqueleto del repositorio · `done`

## M1 — Núcleo puro (sin red)

### T-02 Modelos y aritmética de mercado · `done` · RNF-07
### T-03 Configuración y secretos · `done` · RF-12, RF-13, RNF-08, CA-07
### T-03b Solo pares contra BTC · `done` · RNF-10 · dep T-03
### T-04 Journal JSONL · `done` · RF-11, CA-07 · dep T-03
### T-05 Parser de comandos · `done` · RF-06, RF-07, RF-08 · PA-07
### T-06 Validaciones de riesgo · `done` · RF-10, CA-04 · dep T-02 · PA-06
### T-07 Limitador de peticiones · `done` · RNF-03
### T-10 Book L2 · `done` · RF-02 · dep T-02
### T-11 Constructor de velas · `done` · RF-03 · dep T-02
### T-14a Cálculo del panel cruzado · `done` · RF-04 · dep T-10

## M2 — Market data

### T-08 Adaptador Neoxa: datos públicos · `done` · RF-01, RF-03, RNF-03, RNF-09 · dep T-07, T-10 · PA-01
### T-09 Adaptador NonKYC: datos públicos · `done` · RF-01, RF-03, RNF-05, RNF-09 · dep T-07, T-10 · PA-02
### T-12 Supervisor de conexión · `done` · RF-01, RF-14, CA-06 · dep T-08, T-09

## M3 — TUI en solo lectura

### T-13 Layout, books y gráficos · `done` · RF-02, RF-03, RNF-02, RNF-06 · dep T-10, T-11, T-12
### T-14b Panel cruzado en pantalla · `done` · RF-04 · dep T-13, T-14a
### T-15 Barra de estado · `done` · RF-14 · dep T-13

## M4 — Cuenta y órdenes

### T-16 Servicio de órdenes y dry-run · `done` · RF-06, RF-10, RF-11, RF-13, CA-04 · dep T-04, T-05, T-06
### T-16b Dry-run con saldos reales · `done` · RF-10, RF-13 · dep T-16, T-17 · D-16
### T-17 Adaptadores privados · `done` · RF-05, RF-08, RF-09, RNF-04 · dep T-08, T-09, T-16 · PA-02
### T-18 Orden doble · `done` · RF-07, CA-03 · dep T-16, T-17 · PA-08
### T-19 Paneles de balances y órdenes · `done` · RF-05, RF-08, CA-05 · dep T-17
### T-21 Órdenes más fáciles y ayuda · `done` · RF-15, RF-16, RNF-11 · dep T-19 · D-18
### T-22 Tamaño por orden porcentual · `done` · RF-10, CA-04 · PA-03 · D-19
### T-23 Cantidad como porcentaje del saldo · `done` · RF-17, RF-10 · dep T-21, T-22 · D-21
### T-24 Velas de 4h y diarias · `done` · RF-03 · dep T-11, T-12, T-13 · D-23

## M5 — Aceptación

### T-20 Pruebas de aceptación en real · `doing` · CA-01 … CA-07 · dep todo lo anterior
Checklist, con tamaños mínimos y resultado anotado en `decisions.md`:

- [x] CA-01 30 min en vivo sin intervención. (D-20)
- [x] CA-02 orden límite colocada y cancelada en cada venue; ambas en el journal. (D-43)
- [ ] CA-03 orden doble ejecutada; balances reflejan fills en < 10 s.
  (dobles reales hechas, D-43; falta comprobar los saldos)
  Aplazado por el usuario el 2026-10-23: mercado poco favorable para disparar.
- [x] CA-04 en dry-run, cada caso de RF-10 rechazado. (D-20)
- [x] CA-05 cancelar todo deja cero órdenes abiertas. (D-58, D-59)
- [ ] CA-06 corte de red (p. ej. `nmcli networking off` 60 s) y recuperación sola.
  Aplazado por el usuario el 2026-10-23.
- [x] CA-07 `grep` de cada key en `logs/` y en la captura de pantalla: sin resultados.
  (D-20; repetido tras las órdenes reales el 2026-10-23, D-59)

## M6 — Producción (D-25)

### T-25 Envíos con resultado incierto · `done` · RNF-12 · dep T-18 · PA-09
### T-26 Envíos atómicos por venue · `done` · RNF-13 · dep T-25
### T-27 Fallos aislados · `done` · RNF-14 · dep T-26
### T-28 Salida segura · `done` · RNF-15 · dep T-26
### T-29 Frescura con reloj local y desfase · `done` · RNF-16 · dep T-12
### T-30 Rutas, permisos e instancia única · `done` · RNF-17, RNF-18, RNF-20 · dep T-03, T-04
### T-31 Log de diagnóstico · `done` · RNF-19 · dep T-30

### T-32 CI · `todo` · RNF-21
- `.github/workflows/ci.yml`: `uv sync --frozen`, pytest, ruff y `spec_trace`.
- Tests: el workflow existe y ejecuta esos cuatro pasos.
- Aplazada por el usuario: el repo no tiene remoto (D-54). Mientras tanto, los
  cuatro pasos se corren a mano al cerrar cada tarea.

### T-33 Comprobación en real de M6 · `todo` · RNF-12 … RNF-21 · dep T-25 … T-32
- Live con importe mínimo: cortar la red durante un envío → incierta → reconciliada.
- Segunda instancia, `.env` con permisos abiertos, arranque desde otro directorio.
- Resultado en `decisions.md`.

## M7 — Bot taker (fase 2, D-31)

Se adelanta a T-32 y T-33 a petición del usuario (D-34): depende de M6 hasta T-31.

### T-34 Config del bot · `done` · RF-18, RF-22 · dep T-03, T-31
### T-35 Decisión del bot · `done` · RF-19, RF-20, RF-22, RNF-22, CA-08 · dep T-34
### T-36 Servicio del bot · `done` · RF-19, RF-20, RF-21, RF-23, CA-08, CA-09 · dep T-35
### T-37 Bot en la pantalla · `done` · RF-18, RF-23 · dep T-36
### T-38 Bot en real · `done` · CA-10 · dep T-37
### T-39 Descuento de fee en el panel cruzado · `done` · RF-24, RF-04 · dep T-14b · D-38

## M8 — Resultados (v0.10, D-44)

### T-40 Ventana de resultados · `done` · RF-25, CA-11 · dep T-36, T-18 · D-44, D-45

## M9 — Ratio de inventario (v0.11, D-46)

### T-41 Ratio de inventario por venue · `done` · RF-26, RF-27, CA-12 · dep T-35, T-19 · D-46

## M10 — Direcciones de depósito (v0.12, D-50)

### T-42 Ventana de direcciones de depósito · `done` · RF-28, CA-13 · dep T-17 · D-50
### T-44 Copiar de verdad y QR de la dirección · `done` · RF-28, CA-13 · dep T-42 · D-52

## M11 — Plan de reequilibrio (v0.14, D-53)

### T-45 Plan de reequilibrio · `done` · RF-29, CA-14 · dep T-19, T-41, T-42 · D-53

## M12 — Daemon y TUI conectada (v0.17, D-61)

### T-50 Motor sin UI · `done` · RF-30, RNF-12, RNF-15 · D-61
### T-51 `btcb2t daemon` · `done` · RF-30, RNF-15, RNF-17 · dep T-50 · D-61
### T-52 Servidor de socket · `done` · RF-31 · dep T-51 · D-61
### T-53 `btcb2t attach` · `done` · RF-31, CA-15 · dep T-52 · D-61
### T-54 Un binario con args · `done` · RF-30, RF-31, RNF-20 · dep T-53 · D-61

### T-55 CA-15 · `doing` · CA-15 · dep T-54 · D-61
- Automático: daemon dry-run con venues simulados, attach que arma y se va,
  el bot dispara, otra conexión ve lo mismo, SIGTERM drena.
- Manual en live en el server: el bot sigue armado tras cerrar la TUI y la
  sesión SSH; reiniciar el daemon lo deja `off` con los contadores del día.
- Hecho (automático, 2026-10-24): `tests/ui/test_ca15.py`. `run_daemon` con
  las piezas de `BotRig` (bot, cuenta y órdenes reales sobre `DryRunVenue`):
  arranca `off`; la TUI conectada rechaza un `buy`, arma con la confirmación y
  sale; el bot sigue `armed` (un solo `bot_state`) y dispara al cruzar; otra
  conexión ve la doble y el mismo `cooldown`; al parar, bot `off`, socket
  borrado y la doble en vuelo con sus dos `order_ack`. Falta la parte manual
  en live en el server.

## M13 — Daemon servidor y TUI cliente (v0.21, D-66)

### T-61 Spec v0.21 · `done` · RF-30, RF-31, RNF-11, RNF-17, CA-15, CA-16 · D-66

### T-62 Mercado por el socket · `done` · RF-31, RNF-03, CA-16 · dep T-61 · D-66

### T-63 Órdenes por el socket · `done` · RF-06, RF-07, RF-08, RF-10, RF-31, RNF-11, CA-16 · dep T-62 · D-66

### T-64 Resultados, depósitos y reequilibrio por el socket · `done` · RF-25, RF-28, RF-29, RF-31 · dep T-63 · D-66

### T-65 `btcb2t` es el cliente · `done` · RF-30, RF-31, RNF-17, CA-15, CA-16 · dep T-64 · D-66

### T-66 CA-16 en real · `todo` · CA-16 · dep T-65 · D-66
- Daemon `--live` en el server, cliente por SSH: orden mínima colocada y
  cancelada, con permiso del usuario. Resultado en `decisions.md`.

### T-67 Piernas junto a los balances · `done` · RF-05, RF-07, RNF-02 · D-67

### T-68 El journal se pone al día al arrancar · `done` · RF-11, RF-25 · dep T-59 · D-68

### T-69 Valor de la cuenta en el tiempo · `done` · RF-32, CA-17 · dep T-64 · D-69

### T-70 Resultados se refrescan solos · `done` · RF-25, RF-32, CA-17 · dep T-69 · D-69

### T-71 Valor de la cuenta ajustado al último tramo · `done` · RF-32, CA-17 · dep T-69 · D-69

### T-72 Bot: menos complejidad, mismo comportamiento, latencia medida · `done` · RF-19, RF-20, RNF-22, RNF-23 · D-70

### T-73 El bot no dispara tras un movimiento brusco · `done` · RF-19, CA-08 · dep T-72 · D-71

## Correcciones

### T-43 Errores sin texto · `done` · RF-19, RF-21, RF-11 · dep T-36 · D-51
### T-46 Correcciones de la auditoría · `done` · RF-22, RNF-01, CA-10 · dep T-35, T-36 · D-54
### T-47 Cancelar todas en NonKYC · `done` · RF-08, CA-05 · dep T-17 · D-58
### T-48 Activas de NonKYC sin las terminadas · `done` · RF-08, RF-10, CA-05 · dep T-17 · D-59
### T-49 Bot recorre el book · `done` · RF-20, CA-08 · dep T-35 · D-60
### T-56 El daemon no veía los fills · `done` · RF-07, RF-21, RF-30, CA-15 · dep T-50 · D-62
### T-57 El bot con el descuento de fee pagada en token · `done` · RF-19, RF-24, RF-25 · dep T-58 · D-63
### T-58 Órdenes y fills de NonKYC de otros mercados · `done` · RF-05, RF-08, RF-09 · D-63
### T-59 Fee corregida de un fill · `done` · RF-11, RF-25 · dep T-57 · D-64
### T-60 Fees en token valoradas en resultados · `done` · RF-24, RF-25 · dep T-59 · D-65
### T-74 Salto tras un book quieto · `done` · RF-19, CA-08 · dep T-73 · D-72
