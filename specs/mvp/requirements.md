# xbt-terminal — Requerimientos MVP

Versión 0.29 · 2026-10-29 · @JordyPirata · Estado: **aprobado para diseño**

> Historial de versiones (qué cambió en cada una y por qué): `specs/archive/changelog.md`.

## Objetivo

Una terminal de consola (TUI) para operar **manualmente** BTCB2 contra **BTC** en
NonKYC y Neoxa desde una sola pantalla: dos gráficos, dos libros de órdenes y
ejecución en ambos venues.

Es la base de un market maker cross-exchange posterior, pero el MVP no
automatiza ninguna decisión de trading. Todo lo que no esté listado en Alcance
queda fuera.

**Fase 2 (v0.8):** la única decisión automática permitida es la del bot de
arbitraje taker (RF-18 a RF-23): tomar las dos puntas cuando el cruce entre
venues deja ganancia neta, con los mismos adaptadores, validaciones y orden
doble que la operación manual.

## Alcance

El MVP cubre dos venues, un par en cada uno, visualización en vivo y órdenes
límite manuales.

| Elemento | Incluido en el MVP |
| --- | --- |
| Venues | NonKYC y Neoxa, nada más |
| Pares | `BTCB2/BTC` en NonKYC y `BTCB2_BTC` en Neoxa; configurables, pero siempre contra BTC (RNF-10) |
| Market data | Order book L2, trades recientes y velas por venue |
| Pantalla | 2 gráficos, 2 books, panel de spread cruzado, balances e inventario neto |
| Tipos de orden | Solo límite, incluida la límite agresiva que cruza el book |
| Ejecución | Orden simple por venue y orden doble (una pierna en cada venue) |
| Gestión | Ver órdenes abiertas, cancelar una, por venue o todas |
| Registro | Log local de órdenes, respuestas, fills y errores |
| Modos | Solo lectura (sin keys) y dry-run (simula envíos); el modo es el del daemon (RF-30) |
| Bot (fase 2) | Arbitraje taker entre los dos venues, apagado por defecto (RF-18 a RF-23) |
| Resultados (v0.10) | Ganancia o pérdida realizada de cada orden doble, con gráfica (RF-25) |
| Ratio de inventario (v0.11) | % del valor en BTCB2 por venue, con banda objetivo que respeta el bot (RF-26, RF-27) |
| Depósitos (v0.12) | Ver las direcciones de depósito de BTC y BTCB2 de cada venue (RF-28) |
| Plan de reequilibrio (v0.14) | Calcular la transferencia entre venues que iguala su % de BTCB2 y lo que cuesta (RF-29) |
| Daemon y cliente (v0.17, v0.21) | Toda la lógica (mercado, cuenta, órdenes, bot) corre en un daemon sin pantalla; la TUI es un cliente que opera por un socket local (RF-30, RF-31) |
| Valor de la cuenta (v0.22) | Serie del valor total en BTC, muestreada por el daemon, en una pestaña de resultados (RF-32) |

## Propuesta de diseño

Una sola pantalla dividida en cuatro cuadrantes por venue y una barra inferior
compartida para estado y comandos.

```
┌─ NonKYC BTCB2/BTC ─────────┬─ Neoxa BTCB2/BTC ──────────┐
│  gráfico (velas, plotext)  │  gráfico                   │
├────────────────────────────┼────────────────────────────┤
│  order book L2 en vivo     │  order book L2             │
├────────────────────────────┴────────────────────────────┤
│ spread cruzado · balances ambos venues · inventario neto│
│ > buy 0.01 nk / sell 0.01 nx      [b] [s] [x]cancel-all │
└─────────────────────────────────────────────────────────┘
```

- Columna izquierda: NonKYC (`nk`); columna derecha: Neoxa (`nx`).
- Fila superior: velas (RF-03). Fila media: book L2 (RF-02).
- Barra inferior: panel cruzado y balances (RF-04, RF-05) y línea de comandos
  con atajos para comprar, vender y cancelar todo (RF-06 a RF-08).

## Fuera de alcance

Cualquier punto de esta lista requiere una nueva versión del documento antes de
construirse.

- Estrategias automáticas distintas del bot taker de RF-18 a RF-23 (con el
  filtro de inventario de RF-26): cotización
  continua o market making, cobertura o reintento automático de piernas, más de
  una orden doble del bot a la vez.
- Órdenes market, stop, OCO, IOC/FOK nativas u otros tipos distintos a límite.
  El bot emula la toma de liquidez con límite agresiva y cancelación (RF-20).
- Otros venues u otros pares. En particular, **cualquier par contra stablecoins
  (USDT, USDC…)**: ni se opera, ni se guarda saldo, ni se valora en ellas.
- Depósitos (salvo **ver** las direcciones de depósito, RF-28), historial de
  depósitos, retiros, transferencias o rebalanceo entre venues hechos por la
  terminal (el plan de RF-29 solo calcula y muestra), swaps y coin splitting.
- Nodos propios (Knots o Core) y monitoreo de hashrate o reorgs.
- PnL contable o de cartera (valorar inventario, posiciones abiertas o
  rebalanceos), reportes contables o fiscales. Solo se permiten el resultado
  realizado por orden doble de RF-25 y la serie del valor de la cuenta de
  RF-32, que no ajusta depósitos ni retiros ni atribuye resultados.
- Órdenes automáticas para alcanzar el ratio de inventario de RF-26: el bot solo
  deja de disparar; comprar o vender para reequilibrar es manual.
- Indicadores técnicos, dibujo sobre gráficos o backtesting.
- Interfaz web, móvil o multiusuario; operar fondos de terceros. El cliente
  de RF-31 no lo es: habla con el daemon por un socket Unix del mismo usuario
  (en la misma máquina, por SSH o con el socket reenviado por SSH). Siguen
  fuera: control por red propio (TCP, HTTP), otros usuarios del sistema y
  autenticación propia.
- Integración con gestores de servicios (unidades de systemd, `sd_notify`,
  fork a segundo plano, pidfile): el daemon corre en primer plano y quien lo
  supervise lo configura el usuario fuera del repo.

## Requerimientos funcionales

| ID | Requerimiento |
| --- | --- |
| RF-01 | Suscribirse por WebSocket al book y a los trades de cada venue, con respaldo por polling REST si el stream cae. |
| RF-02 | Mostrar el book L2 de cada venue con N niveles configurables (default 15), mejor bid/ask y tamaño acumulado. |
| RF-03 | Mostrar velas por venue en 1m, 5m, 1h, 4h y 1d: histórico desde REST y vela actual actualizada con trades. |
| RF-04 | Panel cruzado: mid y spread interno de cada venue, y diferencia entre venues en precio y en %, bruta y neta de fees configuradas. |
| RF-05 | Mostrar balances BTC y BTCB2 por venue y el inventario neto total; refresco cada 10 s como máximo y tras cada fill. |
| RF-06 | Enviar órdenes límite por comando (`buy/sell <venue> <cantidad> <precio>`) con confirmación que muestre el nocional. |
| RF-07 | Orden doble: compra en un venue y venta en el otro por la misma cantidad, enviadas en paralelo; mostrar el estado de cada pierna y alertar de "pierna colgada" si una falla o no se llena. Sin cobertura automática. La doble del bot con `bot.lead_venue` envía las piernas una tras otra (RF-20). |
| RF-08 | Listar órdenes abiertas por venue y cancelar una, todas las de un venue o todas (tecla dedicada). |
| RF-09 | Detectar fills: en NonKYC por stream privado (su WS privado acepta API key, D-10; cada aviso despierta un refresco REST, D-14); en Neoxa por polling de órdenes y trades propios cada 3 s (configurable). |
| RF-10 | Validaciones locales antes de enviar: tamaño máximo por orden como porcentaje del inventario total de los dos venues (en BTC), desviación máxima contra el mid, exposición máxima por venue, y bloqueo de órdenes que crucen una orden propia en el mismo venue. |
| RF-11 | Log append-only en JSONL con timestamp de cada orden enviada, respuesta, fill y error. Si el venue cambia la fee de un fill ya apuntado, se apunta la corrección (`fill_fee`) sin tocar la línea del fill. |
| RF-12 | Leer credenciales desde `.env` y pares, fees y límites desde un archivo de configuración; las keys nunca aparecen en pantalla ni en logs. |
| RF-13 | Modo solo lectura (sin keys) y modo dry-run que simula envíos y los registra en el log. |
| RF-14 | Indicador de conexión y latencia por venue, con reconexión automática. |
| RF-15 | Ayuda dentro de la app (tecla `?` y comando `help`): modo actual, teclas, comandos con ejemplos, clic en el book y cómo leer la pantalla. |
| RF-16 | Un clic en un nivel del book lleva su precio a la línea de comandos: con la línea vacía escribe la orden completa, compra (clic en ventas) o venta (clic en compras) en ese venue con la cantidad del nivel (o la acumulada si el clic es en la columna de acumulado); con texto, inserta solo el precio en el cursor. Además se intenta copiar el precio al portapapeles. |
| RF-17 | En `buy` y `sell` la cantidad puede ser un porcentaje (`25%`, de más de 0 a 100) del saldo total del venue de la orden: en la compra, del BTC (descontando la fee taker); en la venta, del BTCB2. Se redondea hacia abajo al lote y el diálogo de confirmación muestra de qué saldo sale. Las validaciones de RF-10 se aplican igual. |
| RF-18 | Bot de arbitraje taker, apagado al arrancar. Se arma con `bot on` y una confirmación que muestra los parámetros, y se para con `bot off` o una tecla dedicada. En live solo se puede armar si `bot.enabled = true` en la config; en dry-run dispara contra los venues simulados. Estados: `off`, `armed`, `firing`, `cooldown`, `halted`. |
| RF-19 | Disparo: una ruta del panel cruzado (RF-04) cuya ganancia neta, calculada con los precios límite de RF-20 y la fee taker de cada venue (con el descuento de RF-24 solo si se cumple su condición de token), sea al menos `bot.min_net_pct`. Además: books de ambos venues frescos (RNF-16), sin desfase de reloj, sin fallos activos del venue, sin órdenes inciertas y sin pierna colgada. Con `bot.max_move_pct` (opcional), tampoco dispara si el mid de algún venue se ha movido más de ese % (máximo menos mínimo sobre el mínimo) en los últimos `bot.move_window_ms` (2000 por defecto): tras un movimiento brusco, otros bots suelen llegar antes y la pierna queda colgada. El movimiento de cada venue se apunta en cada disparo, con filtro o sin él, para poder ajustar el umbral. |
| RF-20 | Ejecución: una orden doble (RF-07) con límite agresiva en cada venue. El precio de referencia de cada pierna es un nivel del book a no más de `bot.max_walk_pct` del mejor precio de ese venue (0 o ausente: el mejor ask o bid). Se compra a `referencia × (1 + bot.max_slippage_pct)` y se vende a `referencia × (1 − bot.max_slippage_pct)`, redondeados al tick hacia el lado seguro. Cantidad = mínimo de la profundidad del book hasta ese precio en ambos lados, el saldo disponible (BTC con fee taker al comprar, BTCB2 al vender), `bot.max_qty` y el tope de RF-10; al lote, hacia abajo. De los pares de referencias que pasan RF-19 y los mínimos del venue, se dispara el de mayor neta en BTC. Pasa por las validaciones de RF-10 como cualquier orden. Lo que siga abierto a los `bot.fill_timeout_ms` se cancela. Con `bot.lead_venue` (opcional, un venue), las dos piernas se validan juntas, pero se envía primero solo la de ese venue: si vuelve llena, se envía la otra por la cantidad llena, al precio calculado al disparar; si vuelve llena en parte, se cancela el resto al momento y la otra va por lo lleno, al lote hacia abajo; si vuelve abierta sin nada lleno, se cancela al momento y la otra no se envía (disparo fallido, RF-21). Sin la clave, las dos en paralelo. |
| RF-21 | Pierna colgada, envío incierto, rechazo o error en una disparada: el bot cancela lo que quede abierto de esa doble, pasa a `halted`, avisa en rojo y apunta el motivo en el journal. No cubre ni reintenta: el desbalance se resuelve a mano y el bot solo se rearma con `bot on`. Con `bot.lead_venue`, una primera pierna que se cancela sin nada lleno no deja posición: no es una anomalía ni un fallo de RF-22; el bot pasa a `cooldown` y lo apunta. Si la cancelación falla porque ya llenó, sigue como si hubiera vuelto llena. Lo lleno en parte que no llega al mínimo del otro venue es pierna colgada. |
| RF-22 | Límites del bot: `bot.cooldown_s` entre disparos, `bot.max_trades_per_hour`, `bot.max_daily_btc` (nocional comprado en el día UTC) y `bot.max_consecutive_failures`. Al llegar a uno de los dos últimos, `halted`. |
| RF-23 | Visibilidad: estado del bot en la barra y, en el journal, `bot_state` en cada cambio de estado y `bot_fire` en cada disparo con la ruta, precios, cantidad, neta esperada y el `double_id` de sus piernas; con `bot.lead_venue`, el venue de la primera pierna en `bot_fire` y `bot_miss` cuando esa pierna no llena. |
| RF-24 | Descuento de fee opcional por venue en la config (`fee_discount_pct`, p. ej. Neoxa pagando con su token). Lo usa el panel cruzado (RF-04), que lo indica. Con `fee_token` (ticker del token con que se pagan las fees, p. ej. `NKYC`) y `fee_token_min` (los dos o ninguno), el bot también lo usa en la neta de RF-19 y RF-20, pero solo mientras el disponible del token en ese venue sea al menos `fee_token_min`; si no, o sin saldo leído, usa la fee completa. `bot status` y la confirmación de `bot on` dicen qué fee usa cada venue. Las validaciones, el tamaño por %, la cantidad del bot y RF-26 siguen con la fee completa. El token no se opera (RNF-10): se lee su saldo y, en resultados (RF-25), su precio para valorar las fees pagadas con él. |
| RF-25 | Ventana de resultados (comando `results`, tecla `r`): por cada orden doble del journal en live, del bot o manual, el resultado realizado en BTC y sats = lo cobrado en la venta − lo pagado en la compra − las fees de los fills, sobre la cantidad casada (el mínimo de lo llenado en cada pierna, a precio medio). Las fees son las reales del fill; una fee en BTCB2 se valora al precio de ese fill; si el venue corrige la fee de un fill (RF-11), cuenta la última; una fee en el token de fee de un venue (`fee_token`, RF-24) se valora en BTC al mid actual de `<token>/BTC` en ese venue, pedido al abrir la ventana o cambiar de rango (a lo sumo una vez por minuto), y se resta del resultado; la fila la muestra en sats con la cantidad del token y el resumen dice el precio usado. Si el precio no llega, o la fee es de otro activo, se muestra aparte con su ticker, no se resta y el resumen avisa. Lo llenado de más en una pierna se muestra como descuadre en BTCB2, sin valorar. Una doble con pierna colgada o sin fills en las dos piernas no tiene resultado y no suma. Muestra una gráfica del resultado acumulado y por doble, un resumen (dobles, total, media, mejor, peor) y la lista con origen (bot o manual), ruta, cantidad, neta esperada del bot frente a la real y descuadre. Filtro: día UTC, 7 días o todo. Solo lee el journal; la única petición a un venue es el precio público del token de fee. |
| RF-26 | Ratio de inventario: sección opcional `[inventory]` con `target_base_pct` (p. ej. 25) y `band_pct` (p. ej. 10). El % de BTCB2 de un venue es el valor de su BTCB2 total (disponible + en órdenes) al mid del venue sobre su valor total en BTC. Antes de disparar, el bot calcula el % de cada venue tras la doble (compra: +cantidad de BTCB2 y −cantidad × precio límite × (1 + fee taker) de BTC; venta: al revés con (1 − fee taker)). Si algún venue quedaría fuera de la banda **y** más lejos del objetivo que antes, esa ruta no dispara (`inventory_band`) y `bot status` lo dice. Sin `[inventory]`, no hay filtro. |
| RF-27 | El panel de balances muestra el % de BTCB2 de cada venue y del total, en verde dentro de la banda de RF-26 y en amarillo fuera; sin `[inventory]`, sin color. |
| RF-28 | Ventana de direcciones de depósito (comando `deposit`, tecla `w`): para cada venue con keys, la dirección de depósito de BTC (red principal de Bitcoin) y de BTCB2, con la red que dice el venue y, si el venue los da, el depósito mínimo y las confirmaciones. Se piden al venue cada vez que se abre la ventana, sin caché ni disco. Cada fila dice el ticker del venue (BTCB2) además del nombre mostrado (D-48), para no mandar un activo a la dirección de otro. Una dirección que no llega, viene vacía, es de otro activo o tiene la cadena cerrada se muestra como error en su fila, nunca como dirección. Copiar la dirección seleccionada al portapapeles con Enter o `c`: por la terminal (OSC 52) y, si hay, con la herramienta del sistema (`wl-copy`, `xclip` o `xsel`); el mensaje dice si se copió con una de ellas o solo se mandó a la terminal, que puede ignorarlo. La dirección de la fila seleccionada se muestra también como QR (sin prefijo `bitcoin:`), para escanearla con el móvil; una fila con error no tiene QR. Solo lectura: nada se envía al venue salvo la consulta. |
| RF-29 | Plan de reequilibrio (comando `rebalance`, tecla `e`): con los saldos de RF-05 y el mid de cada venue, muestra el % de BTCB2 de cada venue y del total (como RF-26) y dos alternativas de **una sola transferencia** que dejan los dos venues con el mismo % (el del total): (a) BTCB2 del venue con más % al otro, (b) BTC del venue con menos % al otro. Para cada una: lo que se pide en el formulario de retiro del venue, la comisión de retiro, lo que sale del origen, lo que llega, el coste de la comisión en BTC y sats (la de BTCB2 al mid del origen) y el % de cada venue después. La comisión se cobra según el venue (D-53): **aparte** en Neoxa (se pide X, llega X, salen X + comisión; visto en su web) y **descontada** en NonKYC (se pide X, sale X, llega X − comisión; supuesto de PA-10). Si la comisión aparte es mayor que lo que hay que mover, no hay cantidad que iguale y lo dice. La cantidad va hacia abajo a 8 decimales. Una alternativa no es viable si pasa del disponible del origen, queda bajo el retiro mínimo del venue, la comisión se lo come, los retiros están cerrados o la comisión no llegó; lo dice en su fila. Recomienda la viable más barata. Con `[bot]`, cuántas dobles de `max_qty` a la neta mínima pagan la comisión; con `[inventory]`, si el % total cae fuera de la banda, avisa de que una transferencia no lo cambia. Las comisiones (y mínimos y si el retiro está abierto) se piden al venue cada vez que se abre la ventana, sin caché ni disco. Solo calcula: nada se envía al venue salvo las consultas. |
| RF-30 | Daemon (`xbt daemon [--live]`, con `--config`, `--env` y `--data-dir`): es el único proceso que habla con los venues, tiene las keys, toma el lock y escribe el journal. Corre market data, cuenta, órdenes, detección de piernas colgadas, reconciliación de inciertas, bot, resultados, depósitos y plan de reequilibrio, sin pantalla y en primer plano. El bot arranca `off` (RF-18). Sin keys arranca en solo lectura (RF-13): solo mercado. Los avisos van al log de diagnóstico y a los clientes conectados, y los de error también al journal (`notice`). SIGTERM o SIGINT: para el bot, deja terminar los envíos y cancelaciones en vuelo y los apunta en el journal (RNF-15); si quedan órdenes abiertas en live, lo apunta (no pregunta: no hay a quién). Un error de arranque (config, `.env`, lock) sale con código distinto de 0 y lo dice por stderr; al quedar listo escribe una línea en stderr. |
| RF-31 | Cliente (`xbt`, alias `xbt attach`; `--data-dir` o `--socket`): la TUI se conecta al daemon por un socket Unix `0600` y muestra y opera todo lo de la terminal: books, trades y velas (que reenvía el daemon), saldos e inventario, órdenes simples y dobles con su confirmación, cancelar, piernas, inciertas, fallos, bot (armar, parar, estado), resultados (RF-25), depósitos (RF-28) y plan de reequilibrio (RF-29). No abre conexiones a los venues ni lee keys, config de venues ni journal. Las validaciones de RF-10 y el envío los hace el daemon: el cliente confirma una propuesta del daemon por su referencia, no manda precios ya validados. Salir solo desconecta: el daemon, el bot y las órdenes siguen. Si al arrancar no hay daemon, no abre la pantalla y lo dice; si el daemon cae después, lo dice en la barra y reintenta. No lleva `--live`: el modo es el del daemon. Puede haber varios clientes a la vez. |
| RF-32 | Valor de la cuenta en el tiempo: el daemon apunta en el journal un evento `value` cada `value_every_s` (`[polling]`, opcional, 300 s por defecto, mínimo 60), en múltiplos del reloj UTC, con el valor total en BTC (BTC + BTCB2 totales × mid de cada venue, como RF-05) y, por venue, BTC, BTCB2, mid y valor. Si a un venue con keys le falta el saldo o el mid, esa muestra no se apunta. La ventana de resultados (RF-25) tiene una pestaña aparte, "Valor de la cuenta", con el mismo filtro (día UTC, 7 días, todo): una gráfica del total en BTC contra la hora, otra con el valor de cada venue, y un resumen (primera y última muestra, cambio en BTC y en %, máximo, mínimo, nº de muestras). Los huecos de más de dos intervalos (daemon parado) no se interpolan. Mientras la ventana de resultados está abierta, se vuelve a leer cada minuto (las dos pestañas), sin mover la fila seleccionada de la tabla de dobles. Con `z`, las dos gráficas se ajustan al último tramo: las muestras desde el último salto de más del 5 % entre dos seguidas, en el total o en un venue (una transferencia en tránsito), y el resumen añade el cambio de ese tramo en sats; otra `z` vuelve al rango entero. Avisa de que depósitos y retiros salen como saltos: no es PnL. El token de fee no cuenta (RNF-10). |

## Requerimientos no funcionales

La restricción que más condiciona el diseño es el límite de Neoxa, que se cuenta
por IP y comparte presupuesto entre polling y órdenes.

| ID | Requerimiento |
| --- | --- |
| RNF-01 | Stack: Python 3.11+, Textual para la TUI, plotext para gráficos, httpx y websockets. |
| RNF-02 | Corre en terminal Linux o macOS de al menos 160×45 caracteres. |
| RNF-03 | Neoxa: limitador local con margen del 20% sobre 600 req/min por IP, 10 órdenes y 20 cancelaciones cada 10 s. Polling y órdenes comparten el mismo presupuesto. |
| RNF-04 | Neoxa: el WebSocket privado no acepta API key, así que fills y órdenes propias se obtienen por polling REST. |
| RNF-05 | NonKYC: REST en api.nonkyc.io y WebSocket en ws.nonkyc.io. No publica sus límites: se supone 300 req/min con el mismo margen del 20 % (D-10, PA-02). |
| RNF-06 | El book en pantalla se actualiza en menos de 500 ms tras recibir el mensaje. |
| RNF-07 | Precios y cantidades en `Decimal`, respetando el tick size y el lote mínimo de cada mercado. |
| RNF-08 | Las API keys se crean sin permiso de retiro y solo viven en la máquina local. |
| RNF-09 | Cada exchange vive en un adaptador con interfaz común, para reutilizarlo en el bot de fase 2. |
| RNF-10 | Solo pares BTCB2 contra BTC. Nocionales, topes de riesgo y valoraciones se expresan en BTC. La configuración rechaza al arrancar cualquier par con otro quote. |
| RNF-11 | El diálogo de confirmación aparece sin esperar a la red de los venues (< 300 ms; la ida y vuelta al daemon por el socket local cuenta dentro): se valida con los saldos y órdenes que el daemon ya tiene, de como mucho 30 s; si son más viejos, se piden al venue. |
| RNF-12 | Un envío de orden sin respuesta clara (timeout, error 5xx, respuesta que no se puede leer) no se da por fallido: queda **incierto**, se apunta en el journal como tal, se avisa en rojo y se reconcilia con las órdenes del venue hasta saber si existe. Solo un rechazo explícito del venue cuenta como fallo. Vale también para cada pierna de una orden doble. |
| RNF-13 | Validar y enviar una orden es atómico por venue: mientras haya un envío en vuelo en un venue, otra orden de ese venue no se valida sin contar la primera. Los topes de RF-10 no se pueden pasar con órdenes confirmadas a la vez. |
| RNF-14 | Un fallo inesperado (un bug, el disco lleno) no cierra la terminal: se aísla al venue o al servicio donde ocurre, se escribe con su traceback (redactado) y se muestra en pantalla. Un venue caído no tumba al otro. |
| RNF-15 | Salir no deja envíos a medias: un envío o una cancelación en vuelo terminan y se apuntan en el journal antes de cerrar. En live, salir con órdenes abiertas pide confirmación y dice cuántas hay. |
| RNF-16 | La frescura del book (RF-10) se mide con el reloj local de recepción, no con la hora del exchange. Al arrancar se mide el desfase con cada venue; si pasa de `max_clock_skew_s`, se avisa y no se permite el live. Un book quieto solo cuenta como fresco mientras lo confirme el contraste de RNF-24; la conexión viva sola ya no basta. |
| RNF-17 | Solo puede correr un daemon a la vez por directorio de datos: el segundo no arranca y dice por qué. El cliente (RF-31) no cuenta: no toma el lock ni escribe el journal. |
| RNF-18 | `.env` no puede ser legible por el grupo ni por otros: en live no se arranca y en el resto se avisa. El journal y los logs se crean solo legibles por el usuario (`0600`, directorio `0700`). |
| RNF-19 | Hay un log de diagnóstico rotativo, separado del journal, con los errores de red, carga y reconexión de cada venue. Pasa por el `Redactor` y nunca escribe en la pantalla. |
| RNF-20 | Las rutas de config, `.env`, journal y logs no dependen del directorio desde el que se arranca: por defecto siguen XDG (`~/.config/xbt`, `~/.local/state/xbt`) y se pueden cambiar por flag. |
| RNF-21 | Cada cambio pasa por CI: instalación con el lockfile (`uv sync --frozen`), pytest, ruff y `spec_trace`. |
| RNF-22 | La decisión del bot (disparar o no, con qué precios y cantidad, y por qué no) es una función pura en `core/`, sin I/O ni reloj propio, probada con books sintéticos. El bucle del bot no hace más peticiones que la operación manual: los envíos comparten el limitador de Neoxa (RNF-03). |
| RNF-23 | Latencia del bot: `decide` con 50 niveles por lado y `max_walk_pct` en los dos venues tarda menos de 5 ms (mediana); de un cambio de book que cruza a la llamada a `prepare_double` del bot, menos de 50 ms (mediana, venues simulados); `top(50)` de un book de 1000 niveles por lado, menos de 2 ms. Tests de latencia en la suite. |
| RNF-24 | El daemon contrasta cada `risk.book_check_s` (5 s por defecto) el book local de cada venue con su book REST: mejor bid y mejor ask, precio y cantidad. Si coinciden, ese momento cuenta como frescura del book (RNF-16). Si no, el book local deja de ser fresco hasta que llega uno nuevo del venue: se vuelve a suscribir (NonKYC) o a conectar (Neoxa), y se apunta `book_resync` en el journal con los dos topes y en `diag.log`. Un contraste en el que llega un mensaje del book mientras se espera el REST no cuenta, ni para un lado ni para el otro. Un REST fallido no confirma: si fallan todos durante `stale_after_s`, el book queda viejo. |

Fuente de los límites de Neoxa: [documentación de su API](https://neoxa.exchange/api-docs).

## Supuestos y riesgos

El MVP asume inventario ya depositado en ambos venues; si un supuesto falla, el
alcance se revisa.

**Supuestos**

- Ambos mercados cotizan contra BTC, así que el spread se compara sin conversión
  (verificado en las APIs públicas el 2026-10-20, ver D-08).
- El activo en ambos venues es el mismo (la cadena BLAKE2b de Knots), verificado
  por red y asset ID, no por ticker.
- Hay saldo BTC y BTCB2 pre-depositado en ambos venues antes de operar.

**Riesgos**

| Riesgo | Efecto | Mitigación en el MVP |
| --- | --- | --- |
| Books muy delgados | Slippage alto en órdenes agresivas | Solo órdenes límite y validación de desviación contra el mid (RF-10) |
| Venues pequeños y custodiales | Congelamiento de retiros o cuentas | Tope de exposición por venue (RF-10) |
| Pausa de depósitos por inestabilidad de la cadena | Inventario atrapado de un lado | Fuera del MVP; el bot no rebalancea, solo se para cuando falta saldo |
| El bot dispara con datos malos o en bucle | Pérdidas repetidas o inventario desbalanceado | Umbral neto con precios límite, frescura y desfase, una doble a la vez, cooldown, topes por hora y por día, `halted` ante cualquier anomalía (RF-19 a RF-22) |
| Una pierna del bot llena y la otra no | Posición abierta en un venue | Se para y avisa; el usuario decide (RF-21) |
| Cambios de API sin aviso | Fallos de conexión o de órdenes | Adaptadores aislados (RNF-09) e indicador de estado (RF-14) |
| Cruzar órdenes propias | Se interpreta como wash trading | Bloqueo local (RF-10) |
| Congelamiento de stablecoins por su emisor | Fondos bloqueados aunque el venue funcione | No se usan: todo contra BTC (RNF-10) |
| Envío que se corta a mitad | Orden viva en el exchange que la terminal da por fallida | Estado incierto y reconciliación (RNF-12) |
| Neoxa manda el secret en claro en una cabecera de cada petición privada | Quien lea el tráfico o un log de proxy tiene la key | Keys sin retiro por diseño del exchange (RNF-08), TLS y nunca un proxy que registre cabeceras |

## Criterios de aceptación

El MVP está terminado cuando pasa CA-01 a CA-07 con tamaños mínimos de orden; la
fase 2 (bot), cuando pasa además CA-08 a CA-10. Cada ampliación posterior se da
por terminada con su criterio: resultados CA-11 (v0.10), ratio de inventario
CA-12 (v0.11), direcciones de depósito CA-13 (v0.12 y v0.13) y plan de
reequilibrio CA-14 (v0.14), daemon CA-15 (v0.17) y cliente CA-16 (v0.21).

| ID | Criterio | Cómo se verifica |
| --- | --- | --- |
| CA-01 | Books y gráficos de ambos venues se actualizan en vivo durante 30 minutos sin intervención. | Manual (soak), registrado en `decisions.md` |
| CA-02 | Se coloca y cancela una orden límite en cada venue desde la terminal, y ambas quedan en el log. | Manual en live + test con venue simulado |
| CA-03 | Una orden doble se ejecuta, muestra el estado de cada pierna y los balances reflejan los fills en menos de 10 s. | Manual en live + test con venue simulado |
| CA-04 | En dry-run, las validaciones de RF-10 rechazan cada caso fuera de límite, incluido el cruce de orden propia. | Automático |
| CA-05 | Cancelar todo deja cero órdenes abiertas en ambos venues. | Manual en live + test con venue simulado |
| CA-06 | Tras un corte de red, ambas conexiones se recuperan solas sin reiniciar la terminal. | Automático (servidor WS falso) + manual |
| CA-07 | Ninguna API key aparece en pantalla ni en el log. | Automático (canario en log y en render) |
| CA-08 | En dry-run, con books sintéticos que cruzan por encima del umbral, el bot dispara una sola doble con los precios y la cantidad de RF-20; por debajo del umbral, con un book viejo o, con `max_move_pct`, tras un salto del mid mayor que ese % dentro de la ventana, no dispara. Con `lead_venue`, la otra pierna sale solo tras llenar la primera y por lo que llenó; si la primera no llena, la otra no sale y el bot no pasa a `halted`. | Automático |
| CA-09 | Una disparada con una pierna rechazada o incierta deja el bot en `halted`, sin órdenes del bot abiertas y con el motivo en el journal. | Automático (venue simulado) |
| CA-10 | En live con `bot.max_qty` mínimo, el bot dispara al menos un cruce real, ambas piernas quedan en el journal y los topes de RF-22 lo paran. | Manual en live, registrado en `decisions.md` |
| CA-11 | Con un journal sintético de dobles (llenas, parciales, con fee en BTC y en BTCB2, colgadas, manuales y del bot, en varios días), la ventana de resultados da el resultado de RF-25 de cada una al satoshi, el total y la gráfica cuadran, y las colgadas no suman. | Automático |
| CA-12 | Con books y saldos sintéticos, el bot no dispara la ruta que dejaría un venue fuera de la banda y más lejos del objetivo, sí la que lo acerca aunque siga fuera, y sí cualquiera sin `[inventory]`; el panel muestra los % con su color. | Automático |
| CA-13 | Con respuestas sintéticas de los dos venues (dirección buena, vacía, de otro ticker, cadena cerrada y error HTTP), la ventana muestra cada dirección con su venue, ticker y red, y los fallos como error sin dirección; se vuelve a pedir al reabrirla y copiar pone la dirección exacta en el portapapeles, por la terminal y por la herramienta del sistema, y el mensaje dice cuál funcionó; el QR mostrado codifica la dirección exacta de la fila seleccionada y cambia con ella. | Automático |
| CA-14 | Con saldos, mids y comisiones sintéticos, el plan da para cada alternativa la cantidad que iguala el % de los dos venues (comprobado al satoshi con la comisión aparte y descontada), su coste en sats y los % después; marca no viable la que pasa del disponible, queda bajo el mínimo, tiene el retiro cerrado o sin comisión, y recomienda la viable más barata; la ventana lo muestra y vuelve a pedir las comisiones al reabrirla. | Automático |
| CA-15 | Con venues simulados, un daemon en dry-run arranca con el bot `off`; un cliente lo arma, se cierra, y el bot sigue armado y dispara; otro cliente ve el mismo estado; SIGTERM deja los envíos en vuelo en el journal. En live en el server: el bot sigue armado tras cerrar el cliente y la sesión SSH. | Automático + manual en live |
| CA-16 | Con venues simulados y un daemon en dry-run, un cliente coloca y confirma una orden simple y una doble, y cancela; otro cliente ve las mismas órdenes y piernas; tras cerrar el cliente las órdenes siguen en el daemon; una propuesta caducada o ya usada se rechaza; el cliente no abre conexiones a los venues. En live en el server: una orden mínima colocada y cancelada desde el cliente por SSH. | Automático + manual en live |
| CA-17 | Con un journal con muestras `value` (y un hueco), la pestaña "Valor de la cuenta" muestra solo las del rango, corta la línea en el hueco y da primero, último, cambio, máximo y mínimo correctos; sin muestras, lo dice. Con la ventana abierta, una muestra nueva aparece sin cerrarla, y la selección de la tabla no se mueve. Con `z`, las gráficas muestran solo las muestras posteriores al último salto. Con venues simulados, el daemon apunta una muestra en cada múltiplo del intervalo y se la salta si falta un mid. | Automático |
| CA-18 | Con venues simulados y un daemon en dry-run, un WS que sigue conectado pero deja de mandar cambios mientras el book REST cambia: el bot no dispara y las órdenes se rechazan por book viejo a partir del primer contraste que no coincide, se apunta `book_resync` y, con el book nuevo, vuelve a disparar. Un book quieto que coincide con el REST sigue fresco más allá de `stale_after_s`; con el REST fallando, deja de serlo a los `stale_after_s`. | Automático |

## Preguntas abiertas

Cada pregunta lleva el supuesto con el que se trabaja mientras no se responda.
Al resolverse, la respuesta va a `decisions.md` y aquí se marca como resuelta.

| ID | Pregunta | Supuesto por defecto | Bloquea |
| --- | --- | --- | --- |
| PA-01 | ~~¿El par en Neoxa será XBT/USDT, XBT/BTC u otro?~~ **Resuelta: D-08**, `BTCB2_BTC` en Neoxa y `BTCB2/BTC` en NonKYC. | — | — |
| PA-02 | ~~¿Cuáles son los límites de rate de NonKYC y su stream privado acepta API key?~~ **Resuelta: D-10.** Límites no publicados: se mantiene 300 req/min (240 efectivos); el WS privado acepta login con API key. | — | — |
| PA-03 | ~~¿Qué tamaño típico de orden y qué topes de exposición iniciales se usan en RF-10?~~ **Resuelta: D-19** | Por orden, 25 % del inventario total; exposición por venue, 0.01 BTC (confirmado por el usuario el 2026-10-21). | — |
| PA-04 | ~~¿Cuáles son las fees maker y taker vigentes en cada venue?~~ **Resuelta: D-42.** Neoxa las publica por par en `/exchange/pairs` (BTCB2_BTC: maker 0.1%, taker 0.2%, 2026-10-20); NonKYC no las expone; sus fills reales confirman 0.2% taker (la maker sigue sin verificar). | NonKYC 0.2% / 0.2%, configurable. | — |
| PA-05 | ~~¿El fork de Hummingbot en el GitHub de NonKYC trae un conector usable para la fase 2?~~ **Resuelta: D-31**, no se usa: el bot reutiliza los adaptadores propios (RNF-09). | — | — |
| PA-06 | ~~"Exposición máxima por venue" (RF-10): ¿se mide sobre el valor total custodiado o sobre el nocional en órdenes abiertas?~~ **Resuelta: D-07** | Valor custodiado en BTC tras la orden (BTC total + BTCB2 total × mid; en USDT hasta la v0.2, D-08), que es lo que protege del riesgo custodial. | — |
| PA-07 | ~~Sintaxis del comando de orden doble (RF-07).~~ **Resuelta: D-06** | `dbl <cantidad> <venue_compra> <precio_compra> <venue_venta> <precio_venta>` | T-05 |
| PA-08 | ~~¿Qué es "no se llena" para alertar de pierna colgada (RF-07)?~~ **Resuelta: D-15** (cerrada en D-54): cantidades llenas distintas y la pierna de detrás rechazada o cancelada, o el desequilibrio dura más de `hanging_leg_after_s` (30 s) desde que las cantidades dejan de coincidir. | Una pierna llena (total o parcial) y la otra abierta más de `hanging_leg_after_s` (default 30 s) o rechazada. | — |
| PA-09 | ¿Acepta Neoxa un id de cliente al crear una orden, y lo devuelve en `/exchange/orders`? | No. Una orden incierta en Neoxa se reconcilia por lado, par, precio y cantidad exactos, creada a menos de 60 s de `order_sent` (en los dos sentidos, por el desfase de reloj). Si hay más de una candidata, queda incierta y se avisa. | T-25 |
| PA-10 | ¿NonKYC descuenta la comisión de retiro de lo enviado (se pide X, llega X − comisión) o la cobra aparte, como Neoxa (D-53)? Se confirma viendo su formulario de retiro. | Descontada: `WithdrawalFee.on_top = False` en NonKYC (RF-29). | — |
