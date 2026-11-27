# Prompt: bot de compra horaria en oro (XAUUSD M1, "vela de la 1:34")

> Cópialo entero en una conversación nueva abierta en la carpeta `tradingbot`.

---

Quiero añadir una **segunda estrategia** a mi proyecto `tradingbot` (el de este directorio), sin romper la que ya funciona con AAPL (`bot.py`, cruce de medias 20/50, cuenta **paper** de Alpaca, lanzada por launchd). Sigue el mismo estilo: Python, `alpaca-py`, `.env` con las claves, `paper=True` siempre, configuración en un fichero aparte, log en `logs/`, modo `--dry-run` y modo `--scheduled`.

## La estrategia (sacada de un vídeo de YouTube)

- **Activo original:** oro (XAUUSD) en gráfico de 1 minuto.
- **Solo compras.** No hay análisis ni filtro: se compra todos los días que toca.
- **Días:** de lunes a jueves. **El viernes no se opera.**
- **Hora:** la vela de señal es la que abre a las **02:34 hora de Nueva York** (01:34 en Lima con horario de verano de EE. UU., 07:34 en Londres). La entrada se hace **al cierre de esa vela, es decir, en la apertura de la vela de las 02:35 NY**. El vídeo dice que con el cambio de hora "se corre una hora", así que la hora se fija en NY y no en UTC. Calcula siempre con `ZoneInfo("America/New_York")` y no escribas nunca a mano la hora de Madrid (casi todo el año sale a las 08:35 en Madrid, pero no en las semanas en que Europa y EE. UU. cambian de hora en fechas distintas).
- **Stop loss fijo:** 6,80 $ por debajo de la entrada, medidos en precio del oro (el vídeo lo llama "680 ticks" o "68 pips").
- **Take profit fijo:** 5,10 $ por encima de la entrada (relación riesgo/beneficio 1 : 0,75).
- **Riesgo:** 1 % de la cuenta por operación (gana +0,75 %, pierde −1 %).
- **Salida:** solo por SL o TP. El vídeo no habla de salida por tiempo.
- **Resultados que dice el autor** (unos 2 años, sin verificar): 66–67 % de acierto, factor de beneficio de 1,5, drawdown máximo del 15 %, como mucho 4 pérdidas seguidas. Con R:R 1:0,75 hace falta más de un **57,1 %** de acierto solo para no perder dinero, así que el margen es pequeño.

## Paso 1: backtest antes de programar nada para operar

1. Descarga datos **XAUUSD M1** gratis de Dukascopy (por ejemplo con `npx dukascopy-node` o una librería de Python) desde 2024-01-01 hasta hoy. Guárdalos en `data/` y añade esa carpeta a `.gitignore`.
2. Simula las reglas tal cual: entrada en el *open* de la vela de las 02:35 NY de lunes a jueves, SL −6,80 y TP +5,10. Si en una misma vela se tocan el SL y el TP, cuenta la operación como **pérdida** (supuesto conservador). Añade un spread o coste configurable (por defecto 0,30 $).
3. Saca estas métricas: nº de operaciones, % de acierto, factor de beneficio, expectativa en R, drawdown máximo arriesgando el 1 %, racha máxima de pérdidas, y resultados por año y por mes.
4. Compara estas variantes:
   - hora fijada en Nueva York frente a hora fija en UTC (06:34 UTC);
   - SL/TP fijos en dólares frente a SL/TP en **porcentaje** (el oro ha pasado de unos 2.000 $ a mucho más, y 6,80 $ no significa lo mismo con un precio que con otro);
   - con viernes y sin viernes.
5. Enséñame los resultados y **para**. No sigas al paso 2 si la expectativa no es claramente positiva después de costes.

## Paso 2: con qué activo se ejecuta en Alpaca

Alpaca no tiene XAUUSD al contado ni futuros de oro. Ya he comprobado lo siguiente en la API:
- **GLD** (ETF de oro): `tradable`, `fractionable`, con el atributo **`overnight_tradable`**. Es la opción preferida: no paga comisión y se mueve 1:1 con el oro en porcentaje.
- **PAXG/USD** (oro tokenizado, cripto 24/7): `tradable`, no se puede poner en corto. Es la alternativa, pero sus comisiones de cripto (unas décimas de % por lado) se comerían un TP de alrededor del 0,1 %. Solo vale para pruebas en paper.

Antes de programar, consulta la documentación de Alpaca ("24/5 Trading" y "Crypto Spot Trading Fees") y confírmame:
- si la sesión nocturna funciona en **paper**, qué tipos de orden admite (probablemente solo limit con `time_in_force` específico), si permite fracciones y si permite bracket/OCO;
- el spread real de GLD a las 02:35 NY: descarga barras/quotes de 1 minuto de las últimas semanas en esa franja.

Convierte el SL/TP a GLD **en porcentaje**: `sl_pct = 6.80 / precio_oro`, `tp_pct = 5.10 / precio_oro` (o directamente la variante en % que salga mejor en el backtest). El precio del oro lo puedes sacar de `GC=F` en yfinance, o calcularlo con el ratio GLD/oro.

## Paso 3: el bot (`gold_bot.py` + `gold_config.py`)

- `--scheduled`: launchd lo lanza unos minutos antes. El script espera hasta las 02:35:00 NY, comprueba que es de lunes a jueves y que no es festivo (calendario de Alpaca), y no hace nada si ya hay una posición abierta en GLD.
- **Tamaño:** `qty = (equity * RISK_PCT) / (precio_entrada * sl_pct)`, limitado al buying power disponible.
- **Entrada:** si la sesión nocturna solo admite limit, pon una orden limit agresiva (ask + un pequeño margen configurable) y cancélala si no se ha ejecutado en N segundos. Registra el precio de ejecución real.
- **Salida:** si no hay bracket, el propio proceso vigila el precio cada pocos segundos y cierra al tocar el SL o el TP. El TP puede ir como orden limit ya colocada; el SL lo vigila el bot. Pon una salida de seguridad configurable (por ejemplo, cerrar a las 15:55 NY si no se ha tocado ninguno) y explícame que esa regla **no viene del vídeo**.
- Mantén el Mac despierto mientras el proceso vigila la operación (`caffeinate -i`) y dime qué comando `pmset` tengo que ejecutar yo (necesita sudo) para que el Mac se despierte antes de la hora de entrada.
- Escribe un log propio (`logs/gold_bot.log`) y un CSV de operaciones (`logs/gold_trades.csv`) con fecha, entrada, SL, TP, salida, motivo, R y P&L.
- Crea un plist de launchd separado, con la misma idea de cubrir el cambio de hora que el de AAPL.
- `--dry-run`: hace todo menos enviar órdenes.

## Paso 4: verificación

- Ejecuta `--dry-run` y muéstrame el log.
- Haz una prueba real en **paper** solo cuando yo te lo confirme.
- Al final, dame un resumen con lo que se ha creado, cómo se activa y se desactiva el plist, y en qué se diferencia lo que se ejecuta en Alpaca de lo que hace el vídeo (activo, horario, spread y salida de seguridad).

Reglas: solo cuenta **paper**, nunca dinero real. No toques `bot.py` ni su plist. Pregúntame antes de cargar cualquier plist nuevo en launchd.
