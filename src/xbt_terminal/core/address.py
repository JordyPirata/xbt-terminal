"""Validation of a Bitcoin mainnet address typed by the user (RF-37).

Checks the checksum of bech32 / bech32m (BIP173, BIP350) and base58check addresses,
any type, so a typo never reaches the explorer. The checksums are computed by
`bip-utils` (D-88); what stays here is the mainnet policy: the human-readable part,
the base58 version bytes, and the messages shown to the user. The balance is read per
address, so nothing is derived (D-85).

Spec: RF-37, RNF-25, CA-22 · design.md §6.5 · D-85 · D-88 · Task: T-111
"""

from __future__ import annotations

from bip_utils import Base58ChecksumError, Base58Decoder, Bech32ChecksumError, SegwitBech32Decoder

_HRP = "bc"
_BASE58_MAINNET_VERSIONS = (0x00, 0x05)  # P2PKH `1…`, P2SH `3…`
_BASE58_PAYLOAD_LEN = 21  # version byte + 20-byte hash, without the checksum
_BECH32_MAX_LEN = 90  # BIP173


class AddressError(ValueError):
    """Not a valid mainnet address; the message never carries the address (RNF-25)."""


def validate_address(text: str) -> str:
    """The address in its canonical form (bech32 in lowercase), or `AddressError`."""
    text = text.strip()
    if not text:
        raise AddressError("dirección vacía")
    if text.lower().startswith(_HRP + "1"):
        return _validate_segwit(text)
    if text[0] in "13":
        return _validate_base58(text)
    raise AddressError("no es una dirección de Bitcoin (mainnet)")


def _validate_segwit(text: str) -> str:
    if text != text.lower() and text != text.upper():
        raise AddressError("mezcla mayúsculas y minúsculas")
    text = text.lower()
    if len(text) > _BECH32_MAX_LEN:
        raise AddressError("bech32 mal formada")
    try:
        # Version, program length and the bech32 or bech32m checksum of the version
        SegwitBech32Decoder.Decode(_HRP, text)
    except Bech32ChecksumError:
        raise AddressError("checksum incorrecto (dirección mal copiada)") from None
    except ValueError:
        raise AddressError("bech32 mal formada") from None
    return text


def _validate_base58(text: str) -> str:
    try:
        payload = Base58Decoder.CheckDecode(text)
    except Base58ChecksumError:
        raise AddressError("checksum incorrecto (dirección mal copiada)") from None
    except ValueError:
        raise AddressError("base58 mal formada") from None
    if len(payload) != _BASE58_PAYLOAD_LEN:
        raise AddressError("longitud no válida")
    if payload[0] not in _BASE58_MAINNET_VERSIONS:
        raise AddressError("no es de la red principal de Bitcoin")
    return text
