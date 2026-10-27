# Despliegue en un servidor

Cómo dejar el daemon corriendo en una máquina que no apagas y conectarte a él
desde otra. Antes, la instalación y la primera puesta en marcha del
[README](../README.md).

## La máquina

- Linux con `systemd` (o cualquier otro gestor: el daemon corre en primer plano,
  no hace fork y sale con código distinto de 0 si no puede arrancar).
- Reloj con NTP: `timedatectl set-ntp true` y comprobar que `timedatectl` dice
  `System clock synchronized: yes`. Un desfase de más de `max_clock_skew_s` con
  un venue bloquea el live.
- Un usuario normal, no root. Todo vive en su `~/.config/xbt` y
  `~/.local/state/xbt`.
- Una sola IP para un solo daemon: Neoxa limita por IP (ver el README).

## Servicio con `systemd --user`

`~/.config/systemd/user/xbt.service`:

```ini
[Unit]
Description=xbt-terminal daemon

[Service]
ExecStart=%h/.local/bin/xbt daemon --live
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now xbt
loginctl enable-linger "$USER"     # que siga corriendo sin sesión abierta
journalctl --user -u xbt -f        # la línea de arranque y los errores de arranque
```

- Quita `--live` para dejarlo en dry-run.
- Reiniciar es seguro para el bot: arranca **apagado** y hay que armarlo otra
  vez desde la pantalla con `bot on`. Las órdenes abiertas siguen en el
  exchange.
- `systemctl --user stop xbt` manda SIGTERM: el daemon para el bot, espera a los
  envíos en vuelo y los apunta en el journal antes de salir.
- Solo puede correr un daemon por directorio de datos (lock): si arrancas otro a
  mano con el servicio activo, sale con error.

## Conectarse desde otra máquina

```sh
ssh -t servidor xbt
```

El `-t` hace falta: es una TUI. Si `xbt` no está en el PATH de una sesión no
interactiva, usa la ruta completa: `ssh -t servidor ~/.local/bin/xbt`.

También puedes correr la pantalla en tu máquina (con xbt-terminal instalado
también ahí) y reenviar el socket:

```sh
ssh -N -L /tmp/xbt.sock:/home/USUARIO/.local/state/xbt/xbt.sock servidor
xbt --socket /tmp/xbt.sock
```

La ruta remota tiene que ser absoluta (`~` no se expande). Puede haber varias
pantallas conectadas a la vez; `q` solo desconecta la tuya.

## Actualizar

El daemon y la pantalla tienen que ser de la misma versión: el protocolo del
socket cambia entre versiones y uno viejo no se entiende con uno nuevo.

```sh
cd ~/xbt-terminal
systemctl --user stop xbt
git pull
uv tool install --reinstall .
systemctl --user start xbt
```

Si te conectas desde otra máquina con su propia instalación, actualízala
también. Después, `bot on` si lo tenías armado.

## Migrar desde `btcb2t`

Hasta la spec v0.26 el comando era `btcb2t` y las carpetas
`~/.config/btcb2t` y `~/.local/state/btcb2t`. Para conservar config, keys y el
journal (de él salen los resultados y el valor de la cuenta), con el daemon
viejo parado:

```sh
mv ~/.config/btcb2t ~/.config/xbt
mv ~/.local/state/btcb2t ~/.local/state/xbt
rm -f ~/.local/state/xbt/btcb2t.lock ~/.local/state/xbt/btcb2t.sock
```

Si lo tenías instalado con `uv tool`, `uv tool uninstall xbt-terminal` antes de
instalar la versión nueva, y cambia `btcb2t` por `xbt` en tu unidad de
`systemd` o en tus scripts.

## Datos y copias de seguridad

En `~/.local/state/xbt/`:

| Fichero | Qué es |
| --- | --- |
| `journal-YYYYMMDD.jsonl` | Un evento por línea, un fichero por día UTC: órdenes, fills, bot, valor de la cuenta. De aquí salen los resultados (`r`). |
| `diag.log` | Log de diagnóstico del daemon (nivel en `[log]` de la config). |
| `client.log` | Log de la pantalla. |
| `xbt.lock`, `xbt.sock` | Lock de instancia única y socket de la pantalla. No hace falta guardarlos. |

Haz copia de los journals y de `~/.config/xbt/config.toml`. El `.env` tiene las
keys: si lo copias, que sea a un sitio cifrado y solo tuyo. Los journals no
llevan keys.

## Problemas comunes

| Síntoma | Causa y arreglo |
| --- | --- |
| `xbt: command not found` | `~/.local/bin` no está en el PATH: `uv tool update-shell` y abre otra sesión. |
| La pantalla dice que no hay daemon | Arráncalo (`systemctl --user start xbt` o `xbt daemon`), o pasa el mismo `--data-dir`/`--socket` que el daemon. |
| El daemon no arranca en live por el `.env` | `chmod 600 ~/.config/xbt/.env`. |
| El daemon sale diciendo que ya hay otro | Hay otro daemon con el mismo directorio de datos (el servicio, o uno a mano). |
| No deja operar en live por el reloj | El desfase con un venue pasa de `max_clock_skew_s`: activa NTP. |
| Error de ruta de socket demasiado larga | El límite de los sockets Unix es 107 bytes: con un `--data-dir` largo, pasa un `--socket` corto a los dos comandos. |
| La pantalla se ve cortada | La terminal mide menos de 160×45. |
| Copiar una dirección no hace nada | La terminal ignora OSC 52: instala `xclip` (X11) o `wl-copy` (Wayland). Por SSH solo sirve OSC 52. |
| Por SSH, el fondo de las ventanas sale azul marino | `sshd` no pasa `COLORTERM` y Textual baja a 256 colores: `ssh -t servidor env COLORTERM=truecolor xbt`. |
| Tras actualizar, la pantalla no conecta o da errores raros | Daemon y pantalla de versiones distintas: actualiza los dos y reinicia el daemon. |
