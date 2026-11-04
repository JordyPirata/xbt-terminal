"""xbt-terminal: manual trading of BTCB2 against BTC on NonKYC and Neoxa. See specs/mvp/."""

import logging

__version__ = "0.1.0"

# Without this, a warning before the diagnostic log is set up (or in tests) would
# land on stderr through `logging.lastResort`, over the TUI (RNF-19).
logging.getLogger(__name__).addHandler(logging.NullHandler())
