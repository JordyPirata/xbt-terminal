# HANDOFF: historia

Bloques de estado viejos de `HANDOFF.md`, del más nuevo al más viejo, tal como
estaban el 2026-10-25. Es historia: el estado vigente está en `HANDOFF.md`, y
las decisiones, en `specs/mvp/decisions.md`.

> **Daemon en local, hasta la migración al servidor (2026-10-27 03:38 UTC).**
>
> - Migración local a `xbt` hecha el 2026-10-27 00:37 UTC: carpetas movidas a
>   las rutas `xbt` y daemon `xbt daemon --live` arrancado.
> - En local: carpeta `~/Code/xbt-terminal`, daemon `xbt daemon --live` lanzado
>   con `nohup`, sin servicio. En `ps` cada comando salía dos veces (el padre
>   `uv run`); no eran dos daemons.
> - Pendiente entonces: migrar el server a `xbt` (parar el daemon `btcb2t`,
>   actualizar y mover carpetas según `docs/despliegue.md` §Migrar). Hecho al
>   pasar el daemon al servidor.

> **Estado de la tarde del 2026-10-26 (T-67 a T-73), antes de liberar la sesión.**
>
> - Spec en **v0.26**: v0.21 (D-66) hizo del daemon el servidor y de la TUI su
>   cliente; v0.22 (D-69) añade el valor de la cuenta en el tiempo (RF-32); v0.23
>   hace que la ventana de resultados se refresque sola cada minuto (T-70); v0.24
>   añade `z` (último tramo, T-71) y v0.25 el presupuesto de latencia del bot
>   (RNF-23, D-70, T-72); v0.26 el filtro de movimiento brusco (RF-19, D-71, T-73). Hechas T-61 a T-65, T-67 a T-73. 1363 tests, ruff
>   limpio, `spec_trace` 67/72. Sin test: RNF-01 (stack), RNF-21 (CI, T-32), CA-01, CA-02 y CA-10 (se
>   pasaron en real: D-20, D-43, D-57).
> - `btcb2t daemon [--live]` es el único proceso que habla con los venues (keys,
>   lock, journal, órdenes, bot, resultados, depósitos, comisiones). `btcb2t` (o
>   `btcb2t attach`) es la pantalla: se conecta al socket y lo opera todo por él,
>   sin config ni keys. **La terminal todo en uno ya no existe.** El daemon sin
>   keys arranca en solo lectura. Protocolo en design.md §6.13.
> - **Corriendo en local:** `btcb2t daemon --live` desde 2026-10-26 15:54 hora
>   local (pid 304722) y la pantalla `btcb2t` desde las 15:54 (pid 304778), los
>   hijos de `uv run`, con todo hasta T-73 y `max_move_pct = "1.0"`. Bot
>   armado desde la pantalla a las 21:54:48 UTC. Cada comando sale con dos PID en `ps` porque `uv run` queda como
>   padre: no son dos daemons, y el lock no deja arrancar un segundo.
> - **Probado en local con la pantalla de verdad:**
>   - dry-run (19:06 UTC): bot armado y parado desde la pantalla, 8 disparos con
>     sus dos piernas enviadas y canceladas a los 3 s (en dry-run nada se llena),
>     sin errores;
>   - live (19:18 UTC): primera doble por el daemon, **d-fb32e2**, 0.0051 XBT,
>     compra en NonKYC (3 escalones del pool, media 0.00470263) y venta en Neoxa
>     (AMM, 0.00476901), llenas sin descuadre; los 3 `fill_fee` en NKYC llegaron
>     a los 32 s; resultado **+25.3 sats (1.05 %)** frente al 0.62 % esperado.
>     NonKYC cobró 0.149 % en NKYC; Neoxa 0.206 % en BTC.
> - **T-67 (D-67):** las piernas de la última doble salen a la derecha de los
>   balances, en la misma fila; sin doble, los balances a todo el ancho. Pierna
>   sin id de orden (está en `o`) y balances sin `+ 0` si no hay nada en órdenes.
>   Hay que reabrir la pantalla para verlo (el daemon no hace falta reiniciarlo).
> - **T-68 (D-68):** al arrancar, el daemon pone el journal al día con los trades
>   que el venue ya tenía (`PastFill` del primer refresco, solo en live, journal
>   de 7 días): fee corregida mientras estaba parado → `fill_fee`; fill de una
>   orden nuestra sin apuntar → `fill` con `recovered: true` y `trade_ts`; lo
>   ajeno se ignora. Límites: NonKYC solo da sus 50 trades más recientes
>   (`MY_TRADES`), y un fill perdido de Neoxa no se recupera (sin orden, D-45).
> - **T-69 (D-69):** el daemon apunta un evento `value` cada `value_every_s`
>   (`[polling]`, 300 s por defecto) con el valor total en BTC y por venue; en
>   resultados (`r`), `Tab` abre la pestaña "Valor de la cuenta" con el total, cada
>   venue y un resumen. La serie empieza el 2026-10-25 22:25 UTC (no se rehace
>   hacia atrás); depósitos y retiros salen como saltos, los huecos son ratos sin
>   daemon. Comprobado el 2026-10-26 15:25 UTC: 205 muestras sin un hueco, total
>   ~0.0131 BTC. El equipo no ha hibernado desde el 2026-10-24.
> - **T-71 (D-69, adenda):** en la pestaña de valor, `z` ajusta las dos gráficas
>   al último tramo (desde el último salto > 5 % entre dos muestras, en el total o
>   en un venue: una transferencia en tránsito) y las dibuja en sats desde su
>   inicio. El usuario pidió escala logarítmica; con un rango de ~3× no ayudaba.
> - **T-70 (D-69, adenda):** la ventana de resultados vuelve a pedir el informe
>   cada 60 s mientras está abierta; sin cambios no redibuja y la tabla conserva
>   la fila seleccionada. Sale de que el usuario vio la gráfica quieta y creyó que
>   el daemon había dejado de muestrear: solo se releía al abrirla, al cambiar de
>   rango o con un fill.
> - **T-72 (D-70):** la decisión del bot es la misma pero barata: con
>   `max_walk_pct` y 50 niveles, `decide` baja de ~50 ms a ~0.5 ms y del cruce a
>   `prepare_double` de 78 a 3.6 ms. Un oráculo congelado
>   (`tests/unit/bot_reference.py`) vigila que no cambie el comportamiento; si un
>   cambio futuro del bot es a propósito, se actualiza con su decisión. Tests de
>   latencia en `tests/perf/` (`pytest tests/perf -s` imprime lo medido). **Entra
>   al reiniciar el daemon.**
> - **T-73 (D-71):** con `max_move_pct` en `[bot]`, el bot no dispara si el mid
>   de algún venue se movió más de ese % en `move_window_ms` (2000 por defecto).
>   Sale de d-e988bd: un barrido de −2.8 % en Neoxa y otro bot que se llevó el
>   ask barato 15 ms antes de que decidiéramos. **No es el WiFi** (−40 dBm, 3.6
>   ms al router, 0 % de pérdida; ~300 ms de confirmación es del exchange).
>   La config real tiene `max_move_pct = "1.0"` y `move_window_ms = 2000` desde
>   el 2026-10-26 (copia previa en `config.toml.bak-20261026`); `bot_fire` apunta
>   `move` por venue para ajustar el umbral con datos.
> - **Hoy en live:** 49 disparos del bot y dos piernas colgadas, las dos con la
>   compra en Neoxa cancelada sin llenar y la venta llena en NonKYC:
>   **d-f65e93** (13:47 UTC, 0.0038 XBT; rearmado a las 14:44) y **d-e988bd**
>   (20:54 UTC, 0.0059 XBT; rearmado a las 21:07). El bot se paró solo en las dos,
>   como pide RF-21.
> - **Descuadres (todo el journal en live, 2026-10-26):** 14 de 469 dobles. Se ha
>   comprado de más +0.0817 XBT y vendido de más −0.0216: neto **+0.0601 XBT**,
>   que costaron 0.00026685 BTC netos (media 0.00444). A 0.0076 BTC/XBT ese neto
>   valdría +18 991 sats; solo lo vendido de más pierde (−6 896 sats).

> **Estado de la mañana del 2026-10-25 (antes de D-66).**
> - Spec en v0.20 (D-65). 1263 tests, ruff limpio, `spec_trace` 63/68. Sin test:
>   RNF-01 (stack), RNF-21 (CI, T-32), CA-01, CA-02 y CA-10 (se pasaron en real:
>   D-20, D-43, D-57).
> - Última tarea cerrada: **T-60** (D-65). La ventana de resultados valora la fee en
>   NKYC al mid de NKYC/BTC (`NonKYCVenue.token_price`, caché de 60 s en
>   `ResultsService`) y la resta; sin precio, la muestra aparte. Con NKYC a
>   0.00013089: d-2e18a2 +27.49 sats, d-78b0e7 +22.09, d-68e0b2 +16.33 (≈ +65.9).
> - Journal corregido a mano con `fill_fee` (D-64) en d-2e18a2 y d-78b0e7.
>
> ### Tareas abiertas
>
> - **T-55** `doing`: falta la parte manual de CA-15 en live en el server.
> - **T-20** `doing`: CA-03 y CA-06 aplazados por el usuario (2026-10-23).
>   CA-03: saldos < 10 s tras una doble llena. CA-06: `bot off`,
>   `nmcli networking off` 60 s y `on` (corta la red de todo el equipo).
> - **T-33** `todo` (comprobación en real de M6) y **T-32** `todo` (CI; aplazada:
>   el repo no tiene remoto).
>
> ### Siguiente
>
> Abrir `btcb2t daemon` y `btcb2t attach` en dos terminales locales en dry-run y
> comprobarlo a ojo (`attach` solo se ha probado con el pilot de Textual); luego
> T-55 (CA-15) en el server. CA-03, CA-06 y T-33 necesitan al usuario en `--live`.

Última sesión: 2026-10-24: daemon y TUI conectada (D-61, v0.17): T-50, T-51,
T-52, T-53, T-54, la parte automática de T-55 y T-56 (fallo visto en la
primera prueba en live del daemon, D-62).

> **Estado actual (2026-10-24).** Este bloque es lo vigente; los de abajo
> son historia, del más nuevo al más viejo.
> - 1263 tests, ruff limpio, `spec_trace` 63/68.
> - **T-60 (D-65, requirements v0.20, 2026-10-25) `done`:** la ventana de
>   resultados valora la fee en NKYC al mid actual de NKYC/BTC
>   (`NonKYCVenue.token_price`, caché de 60 s en `ResultsService`) y la resta;
>   sin precio, aparte como antes. d-78b0e7 corregida a mano con dos
>   `fill_fee` (2026-10-25, terminal cerrada). Con NKYC a 0.00013089: d-2e18a2
>   +27.49 sats, d-78b0e7 +22.09, d-68e0b2 +16.33 (total ≈ +65.9).
> - **T-59 (D-64, requirements v0.19, 2026-10-25):** NonKYC publica el trade
>   con la fee en BTC y luego la rehace en NKYC; ahora el tracker emite
>   `FeeUpdate`, el journal apunta `fill_fee` y los resultados usan la última.
>   Primera doble con NKYC (d-2e18a2, 05:24 UTC): neta esperada 11.32 sats
>   (0.725 %, con 0.15 % en NonKYC), real ≈ +27.49 sats (1.76 %); NonKYC cobró
>   0.1498 % en NKYC y el AMM de Neoxa, 0. Su journal se quedó con la fee vieja;
>   corregido a mano con dos `fill_fee` (2026-10-25 05:47 UTC, terminal cerrada):
>   la ventana `r` da +29.83 sats en BTC + 0.00017846 NKYC aparte (≈ 2.34 sats). Un fill normal de Neoxa (04:10) cobró 0.2 % en
>   BTC, sin el 50 % del token: por confirmar con más fills.
> - **NKYC y D-63 (requirements v0.18, 2026-10-25).** El usuario paga las fees de
>   NonKYC en NKYC (25 % menos; medido 0.15 % en la compra del propio NKYC, aún
>   sin un fill de BTCB2/BTC que lo confirme). **T-57** `done`: con `fee_token` y
>   `fee_token_min`, el bot calcula la neta con la fee rebajada mientras haya
>   NKYC disponible ≥ el mínimo; cantidad, RiskCheck e inventario siguen con la
>   fee completa. Config real: `fee_discount_pct = "25"`, `fee_token = "NKYC"`,
>   `fee_token_min = "1"` en `[venues.nk]`. **T-58** `done`: `/account/orders` y
>   `/account/trades` ignoran `symbol` y traían NKYC/BTC (la venta de NKYC salía
>   como activa del par); ahora se filtran. **Reiniciar terminal y daemon para
>   cargar las dos.** Sin verificar si `/cancelallorders` respeta `symbol`: no
>   usar `x` con órdenes de NKYC abiertas. Al primer fill de BTCB2/BTC, mirar
>   que `fee_asset` sea `NKYC` y ≈ 0.15 %. Retiro de BTC en NonKYC: 0.00005.
>   `test_el_dialogo_no_espera_a_la_red` (límite de 0.3 s de reloj real) falla
>   a veces con la suite entera; solo pasa.
> - **Primera prueba en live del daemon (2026-10-24 17:53 UTC, D-62):** el
>   daemon no veía los fills (el enlace cuenta → piernas se había quedado en
>   la app). d-68e3ed se llenó entera en los dos venues, pero el bot la dio por
>   colgada y se paró. Arreglado en T-56. **El daemon que quedó corriendo
>   (pid 6467) es el del fallo: hay que pararlo y arrancarlo de nuevo.** Sin test: RNF-01, RNF-21,
>   CA-01, CA-02 y CA-10 (las tres CA se pasaron en real: D-20, D-43, D-57).
> - **Daemon (D-61, requirements v0.17, RF-30, RF-31, CA-15).** Pedido por el
>   usuario: el bot en un server sin pantalla y entrar por SSH a mirarlo.
>   Un binario, tres formas: `btcb2t` (terminal de siempre), `btcb2t daemon
>   [--live]` (sin pantalla, en primer plano, SIGTERM lo para en orden) y
>   `btcb2t attach` (TUI que solo mira y arma o para el bot; `q` desconecta y el
>   bot sigue). Mismos `--config`, `--env`, `--data-dir`, más `--socket`.
>   Nada de systemd en el repo: el usuario configura el servicio a mano.
>   El bot arranca `off` también en el daemon (decisión del usuario).
> - Piezas: `services/engine.py` (bucles que vivían en la UI), `daemon.py`,
>   `control.py` (socket Unix `0600`, `<datos>/btcb2t.sock`), `wire.py` (foto
>   del estado en JSON) y `remote.py` (cliente). La TUI conectada pide el
>   mercado público ella misma con 1/5 del límite por IP y escribe en
>   `attach.log`.
> - Probado en real (dry-run, config real, 2026-10-24): el daemon conecta los
>   dos WS, el socket da los saldos de los dos venues, rechaza `buy`, y SIGTERM
>   sale con 0 y borra el socket. **`attach` no se ha abierto aún en una
>   terminal de verdad**: solo con el pilot de Textual.
> - Ojo: la ruta del socket no puede pasar de 107 bytes (AF_UNIX); con un
>   `--data-dir` largo, usar `--socket`. El default XDG cabe.
> - README: sección "En un server (daemon)" con los tres comandos y
>   `ssh -t server btcb2t attach` (T-54).
> - CA-15 automático hecho (`tests/ui/test_ca15.py`): daemon en dry-run, la
>   TUI conectada arma y se va, el bot sigue armado y dispara, otra conexión ve
>   lo mismo, la parada espera a la doble en vuelo.
> - Ojo en la TUI conectada (igual que en la normal): tras escribir un comando,
>   el foco queda en la línea; `Esc` antes de `q` para salir.
> - Tareas abiertas: **T-55** `doing`
>   (falta la parte manual de CA-15 en live en el server), **T-20**
>   `doing` (CA-03 y CA-06 aplazados), **T-33** y **T-32** `todo`.
> - Preguntas abiertas: PA-09 y PA-10, sin cambios.
> - **Siguiente:** abrir `btcb2t daemon` y `btcb2t attach` en dos terminales
>   locales en dry-run y comprobar a ojo; luego T-55 (CA-15) en el server.

> **Estado al cerrar el 2026-10-23.**
> - 1130 tests, ruff limpio, `spec_trace` 60/65. Sin test: RNF-01, RNF-21,
>   CA-01, CA-02 y CA-10 (las tres CA se pasaron en real: D-20, D-43, D-57).
> - Tareas abiertas: **T-20** `doing`: CA-01, CA-02, CA-04, CA-05 y CA-07
>   hechos; **CA-03 y CA-06 aplazados por el usuario** (2026-10-23: el mercado
>   no está para disparos del bot; CA-06 corta la red del equipo). **T-33**
>   `todo` y **T-32** `todo` (aplazada: no hay remoto). Hechas hoy: T-38 (CA-10,
>   D-57), T-46 (D-55), T-47 (D-58) y T-48 (D-59).
> - Preguntas abiertas: **PA-09** (Neoxa sin client_id, con su supuesto desde
>   T-25) y **PA-10** (¿NonKYC descuenta la comisión de retiro? Se supone que
>   sí; se resuelve con el primer retiro desde NonKYC). PA-08 cerrada por D-15.
> - **Bot:** pasa a `halted` con `day_cap` también si el tope ya estaba gastado
>   al rearmar o reiniciar, o si queda menos de un lote (T-46). `max_daily_btc`
>   volvió a 0.01 en `~/.config/btcb2t/config.toml`; entra al reiniciar.
> - **NonKYC:** cancelar todas va con `side: "all"` (T-47) y las activas se
>   filtran por estado, porque `status=active` trae canceladas (T-48). Reiniciar
>   la terminal para cargar T-48. OpenAPI en `docs/api/nonkyc/openapi.json`;
>   diferencias con la API real en `docs/api/README.md`. `btcb2-arb` nunca se
>   probó en real: sus endpoints son pistas, no datos.
> - **Inventario 2026-10-23 tarde:** NonKYC 23.0 %, Neoxa 27.7 %, total 25.5 %
>   tras el reequilibrio de 0.2 XBT Neoxa → NonKYC y la compra a mano de 0.1 XBT
>   en Neoxa (D-56). Neoxa cobra la comisión de retiro aparte (confirmado).
> - **T-49 (D-60, requirements v0.16):** nueva clave opcional
>   `bot.max_walk_pct = { nk = "0.8" }`. Con ella, la pierna de NonKYC puede
>   tomar varios escalones del pool AMM (0.0018 cada uno; el mínimo del venue
>   ≈ 0.0029). Sin la clave, el bot hace lo mismo que antes. Sin órdenes market
>   ni segundo bot (fuera de alcance; la límite ya llena escalón a escalón). Para
>   activarla hay que añadir la línea a `~/.config/btcb2t/config.toml` (no
>   tocado) y reiniciar. Falta verla disparar en live.
> - **Siguiente:** CA-03 (saldos < 10 s tras una doble llena, con el bot armado
>   cuando el mercado cruce), CA-06 (`bot off`, `nmcli networking off` 60 s y
>   `on`) y T-33. Todo necesita al usuario en `--live`.

> **Auditoría de la spec (2026-10-23, D-54, v0.15).** Corregidos CLAUDE.md
> ("nada de bots"), specs/README (`specs/fase2/`), PA-06 en USDT, design.md
> (cabecera v0.1, capas, modelos, `Skip`, atajos y comandos de §9), README
> (estado de M4, comisión de NonKYC) y notas de sustitución en D-07, D-14, D-18
> y D-47. Lo que el código no cumple va a T-46.

> **Spec v0.14 (2026-10-23, D-53): plan de reequilibrio. T-45 hecha.** Tecla
> `e` o `rebalance`: una transferencia (BTCB2 del venue con más % al otro, o BTC
> al revés) que iguala el % de los dos venues, con las comisiones reales pedidas
> al abrir, coste en sats, viabilidad y dobles que la pagan. Solo calcula. El
> usuario eligió esto frente a dobles con neta reducida (no construido).
> **Corrección a D-47:** el BTC de NonKYC en red principal (`BTC-MAIN`) cuesta
> 0.00005 al retirar, no 0.0008 (era el ticker padre). Estado real 2026-10-23:
> NonKYC ~0 % XBT, Neoxa 28 %, total 18 %; el plan recomienda pedir
> 0.14002494 XBT Neoxa → NonKYC (salen 0.14012494) por ~41 sats.
> **D-53 corregida:** Neoxa cobra la comisión de retiro **aparte** (visto en su
> web); NonKYC se supone descontada, sin confirmar. 1104 tests, `spec_trace` 60/65.
> **`[inventory]` reactivado (2026-10-23)** en `~/.config/btcb2t/config.toml`
> (copia en `config.toml.bak-20261023-reinventory`); entra al reiniciar la app.

> **Spec v0.13 (2026-10-22, D-52): copiar de verdad y QR. T-44 hecha.** En
> GNOME Terminal copiar la dirección de depósito no llegaba al portapapeles
> (OSC 52 ignorado) y la app decía "copiada". Ahora copia también con
> `wl-copy`/`xclip`/`xsel` y lo dice; si no hay ninguno, avisa (Shift +
> arrastrar). QR de la fila seleccionada en la ventana (`segno`, dependencia
> nueva). El clic en el book también copia con el sistema. 1050 tests,
> `spec_trace` 58/63.

> **T-43 (2026-10-23, D-51): errores sin texto.** En un corte de red a las 01:23
> UTC el bot disparó dos veces con los saldos fallando: `ConnectTimeout` tiene
> `str() == ""` y `if acct.error:` lo tomaba por "sin error". Las 4 piernas no
> salieron (comprobado en los venues). Arreglado: `describe(exc)` nunca vacío,
> el bot bloquea con `is not None` y una pierna sin conexión dice "no enviada".
> 1035 tests. El bot quedó parado: rearmar con `bot on` tras reiniciar.

> **Spec v0.12 (2026-10-22, D-50): direcciones de depósito. T-42 hecha.** Tecla
> `w` o `deposit`: BTC y BTCB2 de cada venue con keys, pedidas al abrir, Enter/`c`
> copia. NonKYC: BTC es el ticker `BTC-MAIN`; su dirección de BTCB2 es `bc1q…`,
> igual que una de BTC (por eso cada fila dice el ticker real). Neoxa:
> `/deposits/addresses`. Comprobado en real. 1026 tests, `spec_trace` 58/63.
> **D-49 (2026-10-22): book centrado con barras de profundidad** acumulada a
> los lados (ancho sobrante; ~3 columnas en la pantalla del usuario, ninguna a
> 160 columnas). Clic en la barra = acumulado.
> **D-48 (2026-10-22): BTCB2 se muestra como XBT** en la UI y los mensajes
> (`core/models.py` `ASSET_LABELS`). Config, símbolos, saldos y journal siguen
> con `BTCB2`.

> **Spec v0.11 (2026-10-22, D-46): ratio de inventario por venue. T-41 hecha.**
> `[inventory] target_base_pct = "25"`, `band_pct = "10"` (elegido por el
> usuario). El bot no dispara si deja un venue fuera de 15-35 % de BTCB2 y más
> lejos del 25 %; nunca compra para llegar. Panel de balances con el % por venue
> y total. `[inventory]` añadida al `config.toml` del usuario (copia en
> `config.toml.bak-20261022`). 986 tests, `spec_trace` 56/61.
> **Comisiones de retiro (D-47; corregido por D-53: NonKYC BTC en `BTC-MAIN` es
> 0.00005 y sí se puede sacar):** NonKYC BTC 0.0008 (¡80 000
> sats!), BTCB2 0.0024; Neoxa BTC 0.0001, BTCB2 0.0001. Nunca sacar BTC de
> NonKYC; reequilibrar ≈ 11 000 sats, solo en cantidad. Estado 2026-10-22 18:30
> UTC: NonKYC ~100 % BTCB2, Neoxa ~0.6 %; se espera el cruce nx→nk con el bot
> armado.
> **Pendiente, aplazado por el usuario:** reequilibrio automático. Propuesta: v0.12
> con neta mínima menor (`rebalance_min_net_pct`) para dobles que acercan los dos
> venues al objetivo; el ajuste dentro de un exchange, más adelante y aparte.

> **Spec v0.10 (2026-10-22, D-44): resultado de las dobles.** Pedido por el
> usuario: ventana con la ganancia o pérdida realizada de cada doble (bot y
> manual) y gráfica. RF-25, CA-11, design.md §6.10, hito M8. Solo lee el journal.
> El usuario eligió "bot y manual"; fees reales del fill y filtro día/7d/todo son
> supuestos. **T-40 hecha** (D-45): tecla `r` o `results [dia|7d|todo]`. Los
> fills de Neoxa no traen orden y se asignan por compatibilidad. Con el journal
> real: 24 dobles cerradas, +10 517 sats, 1 colgada. 959 tests, `spec_trace` 53/58.
> En Neoxa se vio el book bloqueado (bid = ask, 0.00499331): viene así de su
> API; el usuario decidió dejar `bid >= ask` como cruzado.

> **Estado a 2026-10-22 (historia: lo vigente está en el bloque de arriba del todo).**
> 907 tests, ruff limpio, `spec_trace` 51/56. Todas las tareas `done` salvo:
> - **T-38** `doing`: 5 disparos reales con las dos piernas llenas (D-43); falta
>   ver la parada por tope en real para CA-10.
> - **T-20** `doing`: CA-01, CA-02 y CA-04 hechos (D-20, D-43). Faltan CA-03
>   (saldos en < 10 s tras una doble), CA-05 (cancelar todo), CA-06 (corte de red)
>   y repetir CA-07 con los journals de las órdenes reales.
> - **T-33** `todo`: comprobación en real de M6.
> - **T-32** `todo`: CI. El repo no tiene remoto; el usuario decidió dejarla así.
> Sin test: RNF-01 (stack), RNF-21 (T-32), CA-01, CA-02 y CA-10 (pruebas en real).
> Pregunta abierta: solo PA-08, cubierta por su supuesto (D-15).
> **Siguiente:** cerrar CA-10 (T-38), después el resto de T-20 y T-33. Todo
> necesita al usuario en `--live`.

Leer esto antes de tocar nada; después `specs/README.md` y la tarea siguiente en
`specs/mvp/tasks.md`.

> **Spec v0.8 (2026-10-21, D-31): fase 2, bot de arbitraje taker.** Pedido por el
> usuario. RF-18 a RF-23, RNF-22, CA-08 a CA-10, design.md §6.9 y hito **M7**
> (T-34 a T-38, todas `todo`). Límites agresivas con slippage máx. y cancelación
> del resto (sin market ni IOC), pierna colgada → `halted` sin cobertura, una
> doble a la vez. **M7 empieza cuando T-33 esté `done`**: el orden no cambia, la
> siguiente sigue siendo T-30. `spec_trace` 37/55 (los IDs nuevos sin tests).
> **T-30 hecha** (D-32): config y `.env` en `~/.config/btcb2t`, journal y lock en
> `~/.local/state/btcb2t` (flags `--config`, `--env`, `--data-dir`); `.env` con
> permisos de grupo/otros → live rechazado, resto avisa; journal `0600`;
> instancia única con `flock`. **Ojo:** el `config.toml`, `.env` y `logs/` locales
> del usuario siguen en la raíz del repo (y el `.env` en `664`): moverlos o
> arrancar con flags.
> **T-31 hecha** (D-33): `diag.log` rotativo en el directorio de datos, redactado,
> `0600`, nunca en pantalla; `[log] level` opcional.
> **M7 adelantado (D-34), T-34 a T-37 hechas (D-35 a D-37):** bot de arbitraje
> taker en `core/bot.py` (decisión pura) y `services/bot.py` (bucle sobre
> `OrderService`), `[bot]` en la config, `bot on|off|status`, tecla `p`, estado
> en el banner de la barra. Probado solo en dry-run con venues falsos.
> **Siguiente: T-38** (bot en real, necesita al usuario: `enabled = true`,
> `max_qty` y `max_daily_btc` mínimos) **y luego T-32** (CI) **y T-33**.
> 892 tests, `spec_trace` 50/55 (faltan CA-10 y las pruebas en real de M5/M6).
> **Config del usuario hecha con formulario (2026-10-21):** `config.toml`, `.env`
> (`0600`) y journal movidos a `~/.config/btcb2t` y `~/.local/state/btcb2t`.
> Bot: `enabled = false`, 0.01 BTCB2 por disparo, 0.001 BTC/día, neta 0.5 %,
> slippage 0.2 %, 30 s y 10/h, 3000 ms. **Spec v0.9 (D-38, T-39):**
> `fee_discount_pct = "50"` en Neoxa, solo para el panel cruzado. 899 tests.
> **Primeros disparos en real (2026-10-22 03:36 y 03:39 UTC):** 0.0037 y 0.01
> BTCB2, compra en Neoxa y venta en NonKYC, las dos piernas llenas; +0.0000054
> BTC (~0.39 USD) sin contar rebalanceo. La fee de Neoxa en BTC fue el 0.2 %
> completo (el descuento del token no se vio). **Fix D-40:** `bot off` durante un
> disparo lo dejaba rearmarse; arreglado. 902 tests.
> **Fix D-41 (04:07 UTC, `d-2185c6`):** falsa pierna colgada; una orden que se
> llenaba antes del siguiente refresco privado no se resolvía nunca. Ahora se
> sigue desde el envío. Hoy, 4 disparos reales, ~+0.0000298 BTC. 906 tests.

> **Spec v0.7 (2026-10-21, D-25): robustez para producción.** Revisión general de
> la app; el usuario eligió el alcance. RNF-12 a RNF-21 y hito **M6** (T-25 a
> T-33, todas `todo`): envíos inciertos y reconciliación, envíos atómicos por
> venue, fallos aislados, salida segura, reloj local y desfase, rutas XDG,
> permisos e instancia única, log de diagnóstico y CI. PA-09 (id de cliente en
> Neoxa) está abierta con un supuesto. Lo que quedó fuera está listado en D-25.
> **T-25 hecha** (D-26): un envío sin respuesta clara queda `Uncertain`
> (`order_unknown`) y se reconcilia cada 3 s con `recent_orders`; NonKYC por
> `userProvidedId`, Neoxa por lado/precio/cantidad a ±60 s (PA-09).
> **T-26 hecha** (D-27): lock por venue en `confirm`/`confirm_double`; las
> inciertas cuentan como abiertas en RiskCheck. Rama `t-25-envios-inciertos`.
> **T-27 hecha** (D-28): `services/faults.py`; cada venue supervisado con backoff,
> journal que falla tras enviar no rompe nada, handlers de la UI protegidos y
> `✖ N fallos` en la barra.
> **T-28 hecha** (D-29): envíos y cancelaciones con `asyncio.shield`, `drain` al
> desmontar; `q` en live con órdenes abiertas pide confirmación.
> **T-29 hecha** (D-30): frescura del book con la hora local de recepción; desfase
> medido con la cabecera `Date`; en live, `clock_skew` si pasa de
> `risk.max_clock_skew_s` (clave nueva y obligatoria, añadida al `config.toml`
> local). **Siguiente: T-30** (rutas XDG, permisos e instancia única).
> 772 tests, `spec_trace` 37/45.

> **Spec v0.2 (2026-10-20): el par es BTCB2/BTC en ambos venues.** Nada de USDT
> ni USDC (riesgo de congelamiento por el emisor de la stablecoin). RNF-10 +
> T-03b: la config rechaza al arrancar cualquier otro quote. Importes, topes y
> valoraciones en BTC. Ver D-08. **No proponer nunca pares contra stablecoins.**

## Estado a 2026-10-21 (historia)

La tabla de módulos solo llega a T-24; los módulos posteriores están en
design.md §2 y en el "Hecho" de cada tarea de tasks.md.

- 662 tests, ruff limpio, `spec_trace` 32/35 (faltan pruebas en real).
- **Velas 4h y 1d (T-24, D-23, spec v0.6):** teclas `4`/`5`, `tf 4h|1d`; NonKYC
  `resolution` 240/1440, Neoxa `interval` 4h/1d; días desde las 00:00 UTC.
- **Fix D-24:** las velas van a paso de columnas fijo, sin huecos sueltos; caben
  tantas como columnas. En 1d de NonKYC hay una mecha real hasta ~0.84 BTC que
  aplasta la escala: es un dato del exchange y no se recorta.
- **Fix D-22 (2026-10-21):** NonKYC devuelve `side` en mayúsculas en
  `/account/orders`, y eso tiraba la lectura de órdenes abiertas. Ya está
  arreglado, y los errores del canal privado y de saldos van al journal.
  **Hay una orden real abierta en NonKYC:** `6ad8e12b0c23ade2e155e876`, buy
  0.0103 @ 0.00581, del usuario. No se toca sin su permiso.
- Tareas `done`: T-01 a T-19 y T-21 a T-24 (incluidas T-03b, T-14a, T-14b y T-16b). **Solo queda T-20.**
- **Spec v0.3** (2026-10-21, D-18): RF-15 ayuda, RF-16 clic en el book, RNF-11 diálogo sin esperas.
- **Spec v0.5** (2026-10-21, D-21): RF-17, cantidad en % del saldo total del venue
  (`buy nx 25% 0.0058`; BTC con fee descontada al comprar, BTCB2 al vender;
  `core/sizing.py`). Pendiente: `test_el_dialogo_no_espera_a_la_red` (300 ms)
  falla ~1 de cada 4 pasadas de la suite completa, también en main: margen justo.
- **Keys reales del usuario en `.env`** (las dos parejas, verificadas el
  2026-10-20 con lecturas). Neoxa: la key empieza por `nex_` y el secret es el
  hash. Las keys de Neoxa no pueden retirar por diseño del exchange.
- **`uv run btcb2t` ya abre la terminal en solo lectura**, con books y velas
  reales de los dos venues. Hace falta `config.toml` (copia del ejemplo; ya
  existe uno local, ignorado por git). Ya hay panel cruzado, barra de estado,
  **órdenes simples en dry-run** con los saldos reales (órdenes simuladas) y, con `--live`, órdenes
  reales y lectura de la cuenta (canal privado en la barra), **orden doble**
  con panel de piernas, **panel de balances** con inventario neto, **lista de
  órdenes abiertas** (`o`) y **cancelar** una, por venue o todas (`x`), **clic
  en el book** para llevar el precio a la orden y **ayuda** con `?`.

| Módulo | Tarea | Qué hay |
| --- | --- | --- |
| `core/models.py` | T-02 | Modelos frozen; todo importe `Decimal` (float → TypeError); `D()`; `quantize_price/qty` |
| `config.py`, `core/secrets.py` | T-03, T-03b | config.toml estricto, solo pares contra BTC; `Secret`/`Redactor`; modos read-only/dry-run/live |
| `core/journal.py` | T-04 | JSONL append-only, rotación diaria UTC, redacción antes de codificar |
| `core/commands.py` | T-05, T-23 | Parser `buy/sell/dbl/orders/cancel/tf`; cantidad `N%` → `Percent` |
| `core/sizing.py` | T-23 | `size_from_percent`: cantidad desde el % del saldo, hacia abajo al lote |
| `core/risk.py` | T-06 | `check(req, ctx, limits)` → todas las violaciones; `RiskLimits` vive aquí |
| `core/ratelimit.py` | T-07 | `RateLimiter.acquire(kind)`, prioridad cancel > order > private > market |
| `core/book.py` | T-10 | `L2Book`: snapshot/deltas por seq; hueco → inválido y sin mid |
| `core/candles.py` | T-11 | `CandleBuilder` por timeframe, velas planas en huecos, `roll(now)` |
| `core/cross.py` | T-14a | `cross(a, b)`: stats por venue, diferencia de mids, rutas bruta/neta, `sats()` |
| `adapters/base.py` | T-08 | Protocolos `MarketData` (público) y `Venue` (+privado); `VenueError`, `ReadOnlyError` |
| `adapters/socketio.py` | T-08 | Códec mínimo Engine.IO 4 / Socket.IO 5 (D-09), números → Decimal |
| `adapters/streaming.py` | T-08, T-09, T-12 | Base común `StreamingVenue`: supervisor (backoff, REST tras 3 fallos, latencia), dedup de trades |
| `adapters/neoxa.py` | T-08 | `NeoxaVenue`: market, book, trades, velas por REST; WS Socket.IO |
| `adapters/nonkyc.py` | T-09 | `NonKYCVenue`: igual; WS JSON-RPC con `sequence` y reordenación de deltas; 1m desde trades |
| `services/market.py` | T-13 | `MarketHub`: histórico + stream → `L2Book`, `CandleBuilder`, `ConnState`; avisa `(venue, qué)` |
| `ui/app.py`, `ui/panels.py` | T-13, T-14b | `BTCB2App` (grid, aviso < 160×45, atajos 1/2/3/q), `BookPanel`, `ChartPanel`, `CrossPanel`, `CommandLine` |
| `ui/status.py` | T-15 | `StatusBar`: conexión y latencia por venue, canal privado, modo (banner rojo en live) |
| `adapters/dryrun.py` | T-16, T-16b | `DryRunVenue`: órdenes simuladas en memoria sobre los saldos reales del venue (D-16) |
| `services/orders.py` | T-16 | `OrderService`: `prepare` (RiskCheck) → `Pending` → `confirm` (revalida) → journal |
| `adapters/private.py` | T-17 | `OrderTracker` (diff de órdenes y fills), `poll_private`, `fan_in` |
| `adapters/neoxa.py`, `adapters/nonkyc.py` | T-17 | Parte privada: balances, órdenes, place/cancel, `private_stream` (D-14) |
| `services/account.py` | T-17 | `AccountService`: canal privado, órdenes abiertas, fills al journal, balances ≤ 10 s |
| `services/faults.py` | T-27 | `Faults` (registro común de fallos → journal, aviso y barra) y `supervised` (reintento con backoff) |
| `core/inventory.py` | T-19 | `inventory()`: BTC/BTCB2 por venue y total, valorados al mid |
| `core/double.py` | T-18 | `DoubleState`/`Leg`, `on_order`, `hanging` (D-15) |
| `__main__.py` | T-13, T-16, T-17, T-18 | `btcb2t`: config → venues (con keys) → hub → venues de trading (simulados o reales) → órdenes y cuenta → app |

## Hecho en T-08 (Neoxa, datos públicos)

Todo verificado contra la API real el 2026-10-20 (ver D-09 y design.md §4):

- REST: `/exchange/pairs`, `/exchange/orderbook/{sym}?depth=`,
  `/exchange/trades/{sym}?limit=` (máx. 100), `/exchange/candles/{sym}?interval=&limit=`.
  Errores como HTTP 400 `{"error": ...}`; cabeceras `RateLimit-*`; 600/60 s por IP.
- WS: Socket.IO en `/exchange`. Cada `exchange:orderbook` es el book entero (50
  niveles, sin `seq`), reemitido al cambiar. El WS privado no acepta API key.
- Fixtures reales en `tests/fixtures/neoxa/`. **El frame `exchange:trade` en vivo
  es sintético** (formato `tradeId`/`time` sacado del cliente web de Neoxa): en
  10 min grabando no hubo ningún trade. Confirmarlo cuando se vea uno real.
- 15 mutaciones a mano del adaptador y el códec: todas detectadas por los tests.

## Hecho en T-09 (NonKYC, datos públicos) — ver D-10

- **PA-02 resuelta:** NonKYC no publica límites (se mantiene 240/min efectivo) y
  su WS privado **sí** acepta login con API key (`login` HS256; firma por
  verificar en T-17 con keys reales).
- WS JSON-RPC en `wss://ws.nonkyc.io`. Los `updateOrderbook` llegan
  **desordenados** (3 de 17 grabados): el adaptador los reordena con una espera
  de hasta 1 s; si falta uno de verdad, se vuelve a suscribir y llega snapshot nuevo.
- Sin velas de 1m en la API: se construyen con los últimos 500 trades (~4 h).
- Fixtures reales en `tests/fixtures/nonkyc/` (17 deltas del book reales).
- La lógica común de los dos venues pasó a `adapters/streaming.py`.

## Hecho en T-12 (supervisor de conexión) — ver D-11

- En `StreamingVenue.market_stream`, común a los dos venues. Las sesiones WS
  de cada venue solo entregan datos; los `ConnState` los emite el supervisor.
- Backoff `min(30, 0.5 × 2^(n-1)) ± 20 %`. Al 3er fallo seguido arranca el
  polling REST en paralelo, y se para con el primer book del WS. Un fallo es
  una sesión que termina; el contador vuelve a 0 con el primer book.
- Latencia: EMA del RTT de ping WS (ping al conectar y keepalive cada 5 s) o de
  la duración REST. El estado se reemite cada 5 s con la latencia.
- Probado en real (2026-10-20): ~165-250 ms por WS en los dos venues; con el WS
  cortado, REST al tercer fallo (~2 s) y book cada ~1.2 s.
- CA-06 automático: `tests/adapters/test_ca06.py` (servidor real que se cae y vuelve).

## Hecho en T-13 (pantalla en solo lectura) — ver D-12

- Book en **escalera horizontal** (compras | ventas) para que quepan 15 niveles
  a 160×45; filas de 77 columnas. AMM marcado con ` A`.
- Throttle: books cada 100 ms (test RNF-06 < 500 ms), velas cada 1 s.
- Probado con los venues reales: capturas en headless y `btcb2t` arrancado en
  un pty de 160×45, saliendo con `q` (código 0).
- `tests/fakes.py`: `FakeFeed`, un venue falso para tests de servicios y UI.

## Hecho en T-14b (panel cruzado en pantalla)

- `CrossPanel` en la barra inferior, en lugar del aviso. Usa los % que ya calcula
  `core/cross.py` y el `taker_fee` de cada `MarketSpec`. Lo que falta sale "—".
- Probado con datos reales el 2026-10-20: ese día la ruta nx→nk daba +1682 sats
  brutos pero -1100 netos, y sale en rojo, como debe.

## Hecho en T-15 (barra de estado)

- Una línea arriba: por venue `ws`/`respaldo`, `●`/`◌`/`✖`, latencia y canal
  privado ("sin keys" o "—" hasta T-17); modo con banner rojo `LIVE`.
- El aviso "sin noticias" solo se muestra con estado "up". Reconectando no hay
  latidos (D-11), así que avisaría siempre sin aportar nada.
- El peor caso cabe en 160 columnas: hay un test.
- `MarketHub` guarda `conn_at`, la hora de llegada del último estado.

## Hecho en T-16 (órdenes simples en dry-run) — ver D-13

- `buy|sell <venue> <qty> <precio>` en la línea de comandos (atajos `b`/`s`).
  - Las reglas de RF-10 se evalúan al preparar y otra vez al confirmar.
  - El diálogo muestra el nocional y la fee, y por defecto no envía
    (`n`/`Esc`/`Enter` cancelan; solo `y` confirma).
  - Journal en `logs/`: `reject_local`, `order_sent`, `order_ack` y `order_error`.
- Balances simulados de `[dryrun.balances]` (D-13). **Sustituido por D-16 (T-16b):**
  el dry-run usa ya los saldos reales.
- **Frescura de `stale_data`:** cuenta el último latido de una conexión "up",
  porque el book de NonKYC pasa hasta 352 s sin cambiar (medido). Sin esto se
  rechazaría casi toda orden en NonKYC.
- Arreglados los `:.2f` heredados de USDT en los mensajes de `core/risk.py`
  (mostraban 0.00 BTC).
- `AUTO_FOCUS = None`: si no, la línea de comandos se queda el foco al arrancar
  y se come los atajos.
- Probado de punta a punta con mercado real y keys falsas: compra al mid en
  Neoxa confirmada (`dry-1`), rechazo por desviación y journal sin secretos.

## Hecho en T-17 (adaptadores privados) — ver D-14

- Verificado con las keys reales, **solo lecturas** (ninguna orden enviada):
  saldos, órdenes abiertas y trades propios en los dos venues, y login real en
  el WS privado de NonKYC (`result: true`, `activeOrders`).
- Seguimiento común (`adapters/private.py`): refresco REST de órdenes activas y
  trades propios → `Order`/`Fill` por diferencia; las órdenes que desaparecen se
  consultan hasta saber cómo acabaron. Neoxa cada 3 s; NonKYC lo despierta cada
  `report` del WS (formato sin documentar) y, sin WS, cada 3 s.
- Neoxa no dice de qué orden es cada trade propio: `Fill.order_id` None.
- `AccountService` y canal privado en la barra (`● ws` / `● rest` / `✖ caído`;
  "simulado" en dry-run). Live conectado: órdenes y cuenta con los adaptadores
  reales y un único journal. Probado headless en live contra los venues reales
  sin teclear nada: NonKYC `privado ● ws`, Neoxa `privado ● rest`, saldos y las
  4 órdenes abiertas del usuario en Neoxa; ninguna key en la captura.
- Journal: Decimal en notación fija (`6E-8` → `0.00000006`).
- Mutation testing: 26 mutaciones a mano, todas detectadas (3 tests añadidos).

## Hecho en T-18 (orden doble) — ver D-15

- `dbl <cant> <venue_compra> <precio> <venue_venta> <precio>`: las dos piernas
  pasan RiskCheck al preparar y al confirmar (si una falla, no sale ninguna),
  una confirmación con spread bruto y neto, envío en paralelo.
- Pierna colgada: cantidades llenas distintas y la de detrás rechazada/cancelada,
  o el desequilibrio dura más de 30 s. `hanging_leg` una vez, panel en rojo.
- `LegsPanel` de 5 filas, oculto hasta la primera orden doble. Captura revisada:
  velas y books siguen cabiendo a 160×45.
- **Dry-run ahora monta `AccountService`** sobre los `DryRunVenue` (saldos
  reales desde T-16b, órdenes simuladas); en live, sobre los reales.
- `DryRunVenue.simulate_fill` solo para tests.
- Mutation testing: 20 mutaciones, todas detectadas o eliminadas (una era
  equivalente: se quitó la comprobación redundante); 1 test añadido.

## Hecho en T-16b (dry-run con saldos reales) — ver D-16

- `DryRunVenue` = saldos reales de cada consulta + lo que bloquean sus órdenes
  simuladas. Probado con la cuenta real: compra simulada en NonKYC rechazada
  (0 BTC real) y en Neoxa aceptada, con el BTC bloqueado encima del saldo real.
- Si leer saldos u órdenes falla al validar, rechazo `venue_error` en vez de
  excepción (afectaba también a live desde T-17).

## Hecho en T-19 (paneles de cuenta y cancelar) — ver D-17

- `BalancesPanel` (4 filas): por venue `disponible + en órdenes` de BTC y BTCB2,
  valor en BTC al mid y antigüedad; inventario neto total en el título.
  Verificado con la cuenta real en dry-run (NonKYC BTCB2 0.09040708, Neoxa BTC
  0.00622282; total 0.00683196 BTC).
- Lista de órdenes abiertas (modal, `o` / `orders [venue]`), se actualiza sola;
  `c` cancela la seleccionada, `x` todas. `cancel <id>|<venue>|all` y `x` en la
  pantalla principal. Siempre con confirmación; después se cuentan las que
  quedan (CA-05). Ojo: cancelar un venue o todo cancela **todas las órdenes del
  par en la cuenta**, también las que no son de la terminal (el diálogo lo dice).
- Arreglado de paso: el diálogo de confirmación no mostraba `[y]` desde T-16
  (Rich lo tomaba por markup); diálogos y avisos van ya sin markup.
- Ids del dry-run con el venue (`dry-nk-1`).
- Mutation testing: 22 mutaciones, todas detectadas (2 tests añadidos).

## Hecho en T-21 (órdenes más fáciles y ayuda) — ver D-18

- Clic en un nivel del book → con la línea vacía, la orden entera (`buy` si es
  un ask, `sell` si es un bid, con la cantidad del nivel o la acumulada si el
  clic es en esa columna); con texto, pega solo el precio en el cursor. El
  precio va también al portapapeles por OSC 52 (GNOME Terminal no lo admite).
- Validación con la caché de `AccountService` (≤ 30 s) más las órdenes recién
  enviadas; `DryRunVenue` usa el spec del hub y la última lectura de saldos.
  Medido en real (dry-run): Enter → diálogo 98 ms, `y` → enviada 85 ms.
- Ayuda con `?` / `help` (`HelpScreen`).
- Arreglado: la línea de comandos seleccionaba todo al recibir el foco, y la
  primera tecla tras `b`/`s` borraba el prefijo (desde T-16).
- Mutation testing: 21 mutaciones, todas detectadas o simplificadas (3 tests añadidos).

## T-20 en curso — parte 1 hecha (D-20)

- CA-01 (30 min en vivo), CA-04 (cada regla en dry-run real) y CA-07 (sin keys
  en logs ni capturas, hasta ahora): superadas.
- Arreglado: los saldos se refrescaban cada 10 s + lo que tarda la lectura.
- Pendiente y **con permiso del usuario para cada una**: CA-06 (corte de red
  real) y CA-02, CA-03, CA-05 (órdenes reales mínimas en `--live`); al final,
  repetir CA-07 con los journals de esas órdenes.

## T-20 — pruebas de aceptación en real (plan original)

Checklist de `tasks.md` T-20 (CA-01 … CA-07), con el resultado en `decisions.md`.
**Necesita al usuario y dinero real**, así que no se hace sin su permiso
explícito para cada prueba con órdenes:
- **PA-03 resuelta (D-19):** por orden, como mucho el 25 % del inventario total
  de los dos venues; exposición por venue, 0.01 BTC fijo (confirmado por el
  usuario). Ya no bloquea T-20.
- Sin órdenes, se pueden hacer ya: CA-01 (30 min en vivo), CA-04 (dry-run),
  CA-06 (corte de red; `nmcli networking off` corta la red de todo el equipo,
  pedir permiso), CA-07 (grep de keys en `logs/` y capturas).
- Con órdenes reales mínimas (`--live`): CA-02 (colocar y cancelar en cada
  venue; confirma las respuestas de enviar/cancelar, que hoy salen de la
  documentación, y el primer `report` real del WS de NonKYC), CA-03 (orden
  doble con fills; hace falta saldo en los dos venues: hoy NonKYC tiene 0 BTC y
  Neoxa 0 BTCB2, así que una compra en NonKYC o una venta en Neoxa no pasan) y
  CA-05 (cancelar todo).

**Queda pendiente (no olvidar):**
- ~~Decisión del usuario sobre los saldos del dry-run~~ → **resuelta: saldos
  reales** (D-16, T-16b). `[dryrun.balances]` ya no existe: una config que la
  traiga da error al arrancar. Se quitó del `config.toml` local del usuario
  (copia de seguridad en el scratchpad de la sesión).
- PA-08 sigue abierta formalmente: T-18 usa su supuesto, precisado en D-15.
- El dry-run sigue exigiendo keys en `.env` (design.md §8): valorar con el
  usuario si puede arrancar sin keys, ya que no las usa.
- **CA-02 en T-20:** las respuestas de enviar y cancelar (los dos venues) salen
  de la documentación; confirmarlas con una orden real mínima. También ver el
  primer `report` real del WS de NonKYC.
- El 2026-10-20 el usuario tenía 4 órdenes de compra abiertas en Neoxa; más
  tarde ya no estaban (0 BTC en órdenes). Saldos al cerrar: NonKYC 0 BTC y
  0.09040708 BTCB2; Neoxa 0.00622282 BTC y 0 BTCB2.
- Fees de NonKYC: 0.2 % / 0.2 % de la config. **Taker confirmada con fills
  reales (D-42, PA-04 cerrada el 2026-10-22)**: fee = qty × precio × 0.002
  exacto. La maker sigue sin verificar (no se ha llenado ninguna orden maker). Las de Neoxa salen de la API; las de Neoxa en config.toml
  no se usan.

Orden restante del plan: T-20.

Precios de referencia del 2026-10-20 (solo una foto, no un patrón): NonKYC
0.00727748 / 0.00730299, Neoxa 0.00735714 / 0.00742999. Son los que usan los
tests de `test_cross.py`.


## Retirado del HANDOFF el 2026-10-29

### Estado (2026-10-27, 22:30 UTC)

- Spec en **v0.28** (changelog en `specs/archive/changelog.md`). T-77
  fusionada en `main` e instalada en el servidor (`960f727`, con
  `lead_venue = "nx"` en su config), **pero el daemon sigue con la versión
  anterior hasta que el usuario lo reinicie**. Hechas T-61 a T-65 y T-67 a T-77. 1378
  tests, ruff limpio, `spec_trace` 67/72 (sin test: RNF-01, RNF-21/T-32, CA-01, CA-02 y
  CA-10, pasados en real: D-20, D-43, D-57).
1. **Terminar de desplegar T-77** (D-75): ya fusionada, instalada y con
   `lead_venue = "nx"` en la config del servidor (copia previa en
   `~/.config/xbt/config.toml.bak-pre-t77`). Falta que el usuario haga
   `sudo systemctl restart xbt` y rearmar con `bot on` (la confirmación dice
   "envía primero la pierna de Neoxa"). Después mirar `bot_miss` (cuántos y
   cuándo), `hanging_leg` (deberían quedar solo las de NonKYC) y que no frene
   nada más. Ojo: `bot_miss` no para el bot (elegido por el usuario); si
   salen muchos seguidos, es que el book de Neoxa que ve no es el real.
   `max_move_pct` se queda en 1.0: no distingue las colgadas de Neoxa;
   revisarlo con los datos de después de T-77. Consulta rápida:
   `grep -h '"bot_fire"' ~/.local/state/xbt/journal-2026102[67].jsonl | grep -o '"double":"[^"]*"\|"move":{[^}]*}'`
   y cruzarla con `hanging_leg` del mismo journal.

## Retirado del HANDOFF el 2026-10-29 (despliegue)

- Hasta el 2026-10-29 el clon del servidor no llegaba a GitHub: el código se le
  subía desde local con `git push servidor:xbt-terminal main:refs/heads/incoming`
  y allí `git merge --ff-only incoming && git branch -d incoming`. Lo sustituye
  `git pull` desde GitHub con deploy key.
