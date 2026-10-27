"""xbt-terminal: operación manual de BTCB2 contra BTC en NonKYC y Neoxa. Ver specs/mvp/."""

import logging

__version__ = "0.1.0"

# Sin esto, un aviso antes de configurar el log de diagnóstico (o en los tests)
# acabaría en stderr por `logging.lastResort`, encima de la TUI (RNF-19).
logging.getLogger(__name__).addHandler(logging.NullHandler())
