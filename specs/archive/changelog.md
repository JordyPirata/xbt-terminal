# Historial de versiones de requirements.md

Cambios de cada versión, sacados de la cabecera de `specs/mvp/requirements.md`.
Es historia: la spec vigente es la de `requirements.md`.

> **Cambio v0.1 → v0.2:** el par pasa a ser **BTCB2/BTC en ambos venues**. No se
> opera ni se guarda saldo en USDT ni USDC, para no exponer fondos a un
> congelamiento del emisor de la stablecoin. Ver D-08 y RNF-10.
>
> **Cambio v0.2 → v0.3 (usabilidad, D-18):** ayuda dentro de la app (RF-15), clic
> en el book para llevar el precio a la orden (RF-16) y diálogo de confirmación
> sin esperas de red (RNF-11). Pedido por el usuario: colocar órdenes era
> difícil y lento.
>
> **Cambio v0.3 → v0.4 (D-19):** el tamaño máximo por orden pasa a ser un
> porcentaje del inventario total (25 %), en lugar de topes fijos de cantidad y
> nocional.
>
> **Cambio v0.4 → v0.5 (D-21):** cantidad como porcentaje del saldo del venue
> en `buy`/`sell` (RF-17). Pedido por el usuario.
>
> **Cambio v0.5 → v0.6 (D-23):** velas de 4h y diarias (RF-03). Pedido por el
> usuario.
>
> **Cambio v0.6 → v0.7 (D-25):** robustez para producción (RNF-12 a RNF-21):
> envíos con resultado incierto, órdenes concurrentes, fallos aislados, salida
> segura, desfase de reloj, instancia única, permisos, log de diagnóstico, rutas
> fijas y CI. Sale de la revisión general de la app, con el alcance elegido por el
> usuario.
>
> **Cambio v0.7 → v0.8 (D-31): fase 2, bot de arbitraje taker (RF-18 a RF-23,
> RNF-22, CA-08 a CA-10, hito M7).** Pedido por el usuario. El bot dispara la
> orden doble solo cuando el cruce neto supera un umbral, con órdenes límite
> agresivas y sin cobertura automática: si una pierna queda colgada, se para.
> Empieza a construirse cuando M6 esté cerrado.
>
> **Cambio v0.8 → v0.9 (D-38): descuento de fee por venue (RF-24).** Pedido por
> el usuario: paga las fees de Neoxa con su token (50 % menos), y la API no lo
> refleja.
>
> **Cambio v0.9 → v0.10 (D-44): resultado de las dobles (RF-25, CA-11).** Pedido
> por el usuario: una ventana con la ganancia o pérdida realizada de cada orden
> doble, del bot y manual, y su gráfica. No es un PnL contable: no valora
> inventario ni hace reportes.
>
> **Cambio v0.10 → v0.11 (D-46): ratio de inventario por venue (RF-26, RF-27,
> CA-12).** Pedido por el usuario: acumular BTC y BTCB2 a largo plazo con un 25 %
> del valor en BTCB2 (banda ±10 puntos) en cada venue, para que las dos rutas del
> bot sigan abiertas. El bot no compra para llegar al ratio: solo deja de
> disparar cuando empeoraría un venue fuera de la banda. Reequilibrar sigue
> siendo manual.
>
> **Cambio v0.11 → v0.12 (D-50): direcciones de depósito (RF-28, CA-13).** Pedido
> por el usuario: ver en una ventana aparte las direcciones de depósito de BTC y
> BTCB2 de cada venue. Solo lectura: no se retira, no se transfiere ni se lee el
> historial de depósitos.
>
> **Cambio v0.12 → v0.13 (D-52): copiar de verdad y QR (RF-28, CA-13).** En
> GNOME Terminal copiar no llegaba al portapapeles (la terminal ignora OSC 52) y
> el usuario no podía pagar a la dirección sin abrir el exchange. Pedido por el
> usuario: copiar también con la herramienta del sistema y mostrar el QR de la
> dirección seleccionada.
>
> **Cambio v0.13 → v0.14 (D-53): plan de reequilibrio (RF-29, CA-14).** Pedido
> por el usuario: el flujo del bot vacía un venue de BTCB2 y el otro de BTC. Una
> ventana calcula la transferencia que deja los dos venues con el mismo % de
> BTCB2, con las comisiones de retiro reales de los venues y su coste en sats.
> Solo calcula: la terminal no retira ni transfiere; el usuario lo hace en la
> web del exchange.
>
> **Cambio v0.14 → v0.15 (D-54): auditoría de la spec.** Sin requisitos nuevos.
> PA-06 en BTC (quedaba en USDT), PA-08 cerrada por D-15, nueva PA-10 (cómo
> cobra NonKYC la comisión de retiro, sacada de RF-29), criterios de aceptación
> de las ampliaciones y referencias a D-10 en RF-09 y RNF-05.
>
> **Cambio v0.15 → v0.16 (D-60): el bot recorre el book (RF-20).** El pool AMM
> de NonKYC publica escalones de ~0.0018 BTCB2, menos que el mínimo del venue,
> así que el escalón al mejor precio no basta. Ahora, con `bot.max_walk_pct`, el
> precio límite de cada pierna sale del escalón que cubre la cantidad. Siguen
> sin órdenes market.
>
> **Cambio v0.16 → v0.17 (D-61): daemon y TUI conectada (RF-30, RF-31,
> RNF-17, CA-15).** Pedido por el usuario: dejar el bot corriendo en un server
> sin pantalla y entrar por SSH a mirarlo sin cambiar su estado. Un solo
> binario: `btcb2t daemon` corre todo sin TUI y `btcb2t attach` abre la TUI
> como observadora, que solo puede armar y parar el bot. Cómo se supervisa el
> daemon (systemd u otro) queda fuera del binario.
>
> **Cambio v0.17 → v0.18 (D-63): el bot con la fee pagada en token (RF-19,
> RF-24, RF-25).** Pedido por el usuario: paga las fees de NonKYC en NKYC (25 %
> menos) y quiere que el bot cuente con ello. Solo mientras tenga saldo del
> token; sin él, la fee completa como hasta ahora.
>
> **Cambio v0.18 → v0.19 (D-64): fee corregida de un fill (RF-11, RF-25).**
> NonKYC publica un trade con la fee en BTC y poco después lo rehace con la fee
> en NKYC; la terminal se quedaba con la primera. Pedido por el usuario tras la
> primera doble del bot con NKYC.
>
> **Cambio v0.19 → v0.20 (D-65): fees en token valoradas en resultados (RF-24,
> RF-25).** Pedido por el usuario: la ventana de resultados daba un total más
> alto con NKYC porque no restaba esa fee, y parecía que el token restaba
> profit en vez de sumarlo.
>
> **Cambio v0.20 → v0.21 (D-66): el daemon es el servidor y la TUI, su cliente
> (RF-30, RF-31, RNF-11, RNF-17, CA-15, CA-16).** Pedido por el usuario: pasar
> toda la lógica de la terminal al daemon y separar cliente de servidor. `btcb2t`
> pasa a ser el cliente (sin daemon no abre) y opera todo por el socket:
> órdenes, doble, cancelar, bot, resultados, depósitos y reequilibrio. Books y
> velas los reenvía el daemon. Desaparece la terminal todo en uno. Sigue sin TCP.

> **Cambio v0.21 → v0.22 (D-69): valor de la cuenta en el tiempo (RF-32,
> CA-17).** Pedido por el usuario: una pestaña aparte en resultados con el valor
> total de la cuenta en BTC a lo largo del tiempo. El daemon lo muestrea cada
> 5 min en el journal; §Fuera de alcance lo exceptúa del "PnL de cartera"
> porque no ajusta depósitos ni retiros.

> **Cambio v0.22 → v0.23 (D-69, adenda): la ventana de resultados se refresca
> sola (RF-32, CA-17).** Pedido por el usuario: con la pestaña abierta, la
> gráfica se quedaba en lo que había al abrirla aunque el daemon siguiera
> apuntando muestras. Ahora se vuelve a leer cada minuto mientras está abierta.

> **Cambio v0.23 → v0.24 (D-69, adenda): `z` ajusta el valor de la cuenta al
> último tramo (RF-32, CA-17).** Pedido por el usuario, que quería escala
> logarítmica para distinguir las variaciones pequeñas. Las caídas que las
> aplastaban eran transferencias en tránsito entre venues (rango de solo ~3×,
> donde el log apenas cambia nada); se eligió ajustar el eje a las muestras
> desde el último salto.

> **Cambio v0.24 → v0.25 (D-70): presupuesto de latencia del bot (RNF-23).**
> Pedido por el usuario: bajar la complejidad del bot sin cambiar su
> comportamiento, con tests de latencia. Con `max_walk_pct` y 50 niveles,
> `decide` tardaba ~50 ms (O(L³) por ruta) en cada cambio de book.

> **Cambio v0.25 → v0.26 (D-71): el bot no dispara tras un movimiento brusco
> (RF-19, CA-08).** Pedido por el usuario: hay más bots en el mercado y cambios
> muy repentinos. d-e988bd quedó colgada porque otro comprador se llevó en 15 ms
> el ask barato que dejó un barrido de −2.8 % en Neoxa. `max_move_pct` y
> `move_window_ms` en `[bot]`, opcionales; el movimiento se apunta en `bot_fire`.

> **Cambio v0.26 → v0.27 (D-73): el comando y las rutas pasan a `xbt`
> (RF-30, RF-31, RNF-20).** Pedido por el usuario: el proyecto ya se llama
> `xbt-terminal`. `xbt daemon`, `xbt`, `~/.config/xbt`, `~/.local/state/xbt`.
> Los símbolos y tickers de los venues no cambian.

> **Cambio v0.27 → v0.28 (D-75): el bot envía primero la pierna de un venue
> (RF-07, RF-20, RF-21, RF-23, CA-08).** Pedido por el usuario tras dos piernas
> colgadas en Neoxa el 2026-10-27: 14 de las 15 piernas del bot que Neoxa no
> llenó al recibirlas quedaron colgadas. Con `bot.lead_venue`, la otra pierna
> sale solo por lo que llenó la primera; si no llena, no hay doble (`bot_miss`).
