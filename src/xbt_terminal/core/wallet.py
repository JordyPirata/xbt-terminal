"""Native segwit addresses of a self-custody wallet, from its account xpub (RF-37).

Pure and public-key only: decodes an account-level `xpub` or `zpub`, derives its
non-hardened children (BIP32) and encodes each as P2WPKH bech32 (BIP84, BIP173).
Nested segwit, taproot and legacy addresses are never derived (D-84).

    parse_account_key(text) ─▶ AccountKey ─▶ address(key, RECEIVE | CHANGE, i)

Standard library only (D-84): the daemon holds trading keys, so no crypto
dependency is added for what is public-key arithmetic checked against the
published vectors.

Spec: RF-37, RNF-25, CA-22 · design.md §6.5 · D-84 · Task: T-101
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

RECEIVE, CHANGE = 0, 1
HARDENED = 0x80000000
BITCOIN_HRP = "bc"

XPUB_VERSION = 0x0488B21E
ZPUB_VERSION = 0x04B24746
_REFUSED_VERSIONS = {
    0x049D7CB2: "es un ypub (nested segwit); solo se admite native segwit",
    0x0295B43F: "es un Ypub (multifirma nested segwit); solo se admite native segwit",
    0x02AA7ED3: "es un Zpub (multifirma); solo se admite una firma",
    0x0488ADE4: "es una clave privada (xprv); solo se admiten claves públicas",
    0x04B2430C: "es una clave privada (zprv); solo se admiten claves públicas",
    0x043587CF: "es de testnet (tpub)",
    0x045F1CF6: "es de testnet (vpub)",
}
_EXTENDED_KEY_BYTES = 78

_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

# secp256k1
_FIELD = 2**256 - 2**32 - 977
_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_GENERATOR = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)
_Point = tuple[int, int] | None  # None is the point at infinity


class WalletError(ValueError):
    """An extended key that cannot be used. The message never carries the key."""


@dataclass(frozen=True, slots=True, repr=False)
class AccountKey:
    """An extended public key: enough to derive every address below it."""

    chain_code: bytes
    public_key: bytes  # compressed, 33 bytes

    def __repr__(self) -> str:
        return "AccountKey(***)"  # it is the xpub in other words (RNF-25)

    def child(self, index: int) -> AccountKey:
        """Non-hardened child `index` (BIP32 public derivation)."""
        if not 0 <= index < HARDENED:
            raise WalletError("índice de derivación fuera de rango (solo no endurecidos)")
        digest = hmac.new(
            self.chain_code, self.public_key + index.to_bytes(4, "big"), hashlib.sha512
        ).digest()
        tweak = int.from_bytes(digest[:32], "big")
        point = _add(_multiply(tweak, _GENERATOR), _decompress(self.public_key))
        if tweak >= _ORDER or point is None:
            # probability below 2^-127; BIP32 says to skip to the next index, and
            # skipping silently would shift every later address
            raise WalletError("índice de derivación sin clave válida")
        return AccountKey(digest[32:], _compress(point))


def parse_account_key(text: str) -> AccountKey:
    """Decode an `xpub` or `zpub`; anything else is a `WalletError`."""
    payload = _base58check_decode(text.strip())
    if len(payload) != _EXTENDED_KEY_BYTES:
        raise WalletError("no es una clave extendida (longitud incorrecta)")
    version = int.from_bytes(payload[:4], "big")
    if version in _REFUSED_VERSIONS:
        raise WalletError(_REFUSED_VERSIONS[version])
    if version not in (XPUB_VERSION, ZPUB_VERSION):
        raise WalletError("versión de clave extendida desconocida (se admite xpub o zpub)")
    chain_code, public_key = payload[13:45], payload[45:78]
    _decompress(public_key)
    return AccountKey(chain_code, public_key)


def address(key: AccountKey, branch: int, index: int, hrp: str = BITCOIN_HRP) -> str:
    """The P2WPKH address at `<key>/<branch>/<index>`."""
    if branch not in (RECEIVE, CHANGE):
        raise WalletError("rama de derivación desconocida (0 recepción, 1 cambio)")
    return p2wpkh_address(key.child(branch).child(index).public_key, hrp)


def p2wpkh_address(public_key: bytes, hrp: str = BITCOIN_HRP) -> str:
    """Bech32 witness v0 address of a compressed public key (BIP173)."""
    data = [0, *_regroup(hash160(public_key), 8, 5)]
    checksum = _bech32_checksum(hrp, data)
    return hrp + "1" + "".join(_BECH32[value] for value in data + checksum)


def hash160(data: bytes) -> bytes:
    return ripemd160(hashlib.sha256(data).digest())


def _base58check_decode(text: str) -> bytes:
    if not text:
        raise WalletError("clave vacía")
    number = 0
    for char in text:
        digit = _BASE58.find(char)
        if digit < 0:
            raise WalletError("no es base58 (carácter no válido)")
        number = number * 58 + digit
    zeros = len(text) - len(text.lstrip("1"))
    raw = b"\0" * zeros + number.to_bytes((number.bit_length() + 7) // 8, "big")
    payload, checksum = raw[:-4], raw[-4:]
    if len(raw) < 5 or hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        raise WalletError("checksum incorrecto (clave mal copiada)")
    return payload


def _decompress(public_key: bytes) -> tuple[int, int]:
    if len(public_key) != 33 or public_key[0] not in (2, 3):
        raise WalletError("clave pública mal formada")
    abscissa = int.from_bytes(public_key[1:], "big")
    if abscissa >= _FIELD:
        raise WalletError("clave pública fuera de la curva")
    square = (pow(abscissa, 3, _FIELD) + 7) % _FIELD
    ordinate = pow(square, (_FIELD + 1) // 4, _FIELD)
    if ordinate * ordinate % _FIELD != square:
        raise WalletError("clave pública fuera de la curva")
    if ordinate % 2 != public_key[0] % 2:
        ordinate = _FIELD - ordinate
    return abscissa, ordinate


def _compress(point: tuple[int, int]) -> bytes:
    return bytes([2 + point[1] % 2]) + point[0].to_bytes(32, "big")


def _add(first: _Point, second: _Point) -> _Point:
    if first is None:
        return second
    if second is None:
        return first
    if first[0] == second[0]:
        if (first[1] + second[1]) % _FIELD == 0:
            return None
        slope = 3 * first[0] * first[0] * pow(2 * first[1], -1, _FIELD)
    else:
        slope = (second[1] - first[1]) * pow(second[0] - first[0], -1, _FIELD)
    abscissa = (slope * slope - first[0] - second[0]) % _FIELD
    return abscissa, (slope * (first[0] - abscissa) - first[1]) % _FIELD


def _multiply(scalar: int, point: tuple[int, int]) -> _Point:
    """`scalar × point`, in Jacobian coordinates: one modular inverse at the end instead
    of one per step, which is what makes a derivation take ~1 ms and not ~15."""
    jacobian: tuple[int, int, int] | None = None
    for bit in bin(scalar)[2:]:
        if jacobian is not None:
            jacobian = _jacobian_double(jacobian)
        if bit == "1":
            jacobian = (*point, 1) if jacobian is None else _jacobian_add(jacobian, point)
    if jacobian is None:
        return None
    inverse = pow(jacobian[2], -1, _FIELD)
    return (
        jacobian[0] * inverse * inverse % _FIELD,
        jacobian[1] * inverse * inverse * inverse % _FIELD,
    )


def _jacobian_double(point: tuple[int, int, int]) -> tuple[int, int, int] | None:
    if point[1] == 0:
        return None
    ordinate_square = point[1] * point[1] % _FIELD
    spread = 4 * point[0] * ordinate_square % _FIELD
    slope = 3 * point[0] * point[0] % _FIELD
    abscissa = (slope * slope - 2 * spread) % _FIELD
    return (
        abscissa,
        (slope * (spread - abscissa) - 8 * ordinate_square * ordinate_square) % _FIELD,
        2 * point[1] * point[2] % _FIELD,
    )


def _jacobian_add(
    point: tuple[int, int, int], affine: tuple[int, int]
) -> tuple[int, int, int] | None:
    depth_square = point[2] * point[2] % _FIELD
    run = (affine[0] * depth_square - point[0]) % _FIELD
    rise = (affine[1] * point[2] * depth_square - point[1]) % _FIELD
    if run == 0:
        return _jacobian_double(point) if rise == 0 else None
    run_square = run * run % _FIELD
    run_cube = run_square * run % _FIELD
    shifted = point[0] * run_square % _FIELD
    abscissa = (rise * rise - run_cube - 2 * shifted) % _FIELD
    return (
        abscissa,
        (rise * (shifted - abscissa) - point[1] * run_cube) % _FIELD,
        point[2] * run % _FIELD,
    )


def _regroup(data: bytes, from_bits: int, to_bits: int) -> list[int]:
    """Repack a byte string into `to_bits` groups, padding the last one with zeros."""
    accumulator = bits = 0
    groups = []
    for value in data:
        accumulator = (accumulator << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            groups.append((accumulator >> bits) & ((1 << to_bits) - 1))
    if bits:
        groups.append((accumulator << (to_bits - bits)) & ((1 << to_bits) - 1))
    return groups


def _bech32_polymod(values: list[int]) -> int:
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for i, generator in enumerate(
            (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
        ):
            if (top >> i) & 1:
                checksum ^= generator
    return checksum


def _bech32_checksum(hrp: str, data: list[int]) -> list[int]:
    expanded = [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]
    polymod = _bech32_polymod(expanded + data + [0] * 6) ^ 1
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


# RIPEMD-160: OpenSSL 3 ships it in the legacy provider, which `hashlib` may not load,
# so an address must not depend on it being there.
_RMD_LEFT_ORDER = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15),
    (7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8),
    (3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12),
    (1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2),
    (4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13),
)
_RMD_RIGHT_ORDER = (
    (5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12),
    (6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2),
    (15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13),
    (8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14),
    (12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11),
)
_RMD_LEFT_SHIFT = (
    (11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8),
    (7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12),
    (11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5),
    (11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12),
    (9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6),
)
_RMD_RIGHT_SHIFT = (
    (8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6),
    (9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11),
    (9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5),
    (15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8),
    (8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11),
)
_RMD_LEFT_CONSTANT = (0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E)
_RMD_RIGHT_CONSTANT = (0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000)
_RMD_INITIAL = (0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0)
_MASK32 = 0xFFFFFFFF


def _rotate(value: int, count: int) -> int:
    return ((value << count) | (value >> (32 - count))) & _MASK32


def _rmd_mix(round_index: int, first: int, second: int, third: int) -> int:
    if round_index == 0:
        return first ^ second ^ third
    if round_index == 1:
        return (first & second) | (~first & _MASK32 & third)
    if round_index == 2:
        return (first | (~second & _MASK32)) ^ third
    if round_index == 3:
        return (first & third) | (second & ~third & _MASK32)
    return first ^ (second | (~third & _MASK32))


def _rmd_line(
    state: tuple[int, ...],
    words: tuple[int, ...],
    orders: tuple[tuple[int, ...], ...],
    shifts: tuple[tuple[int, ...], ...],
    constants: tuple[int, ...],
    mirrored: bool,
) -> tuple[int, ...]:
    """One of the two parallel lines of the compression function."""
    head, second, third, fourth, tail = state
    for round_index in range(5):
        mix_round = 4 - round_index if mirrored else round_index
        for step in range(16):
            total = (
                head
                + _rmd_mix(mix_round, second, third, fourth)
                + words[orders[round_index][step]]
                + constants[round_index]
            ) & _MASK32
            rotated = (_rotate(total, shifts[round_index][step]) + tail) & _MASK32
            head, second, third, fourth, tail = (
                tail, rotated, second, _rotate(third, 10), fourth,
            )
    return head, second, third, fourth, tail


def ripemd160(data: bytes) -> bytes:
    padded = data + b"\x80" + b"\0" * ((55 - len(data)) % 64)
    padded += (8 * len(data)).to_bytes(8, "little")
    state = _RMD_INITIAL
    for offset in range(0, len(padded), 64):
        words = tuple(
            int.from_bytes(padded[offset + 4 * i : offset + 4 * i + 4], "little")
            for i in range(16)
        )
        left = _rmd_line(
            state, words, _RMD_LEFT_ORDER, _RMD_LEFT_SHIFT, _RMD_LEFT_CONSTANT, False
        )
        right = _rmd_line(
            state, words, _RMD_RIGHT_ORDER, _RMD_RIGHT_SHIFT, _RMD_RIGHT_CONSTANT, True
        )
        state = (
            (state[1] + left[2] + right[3]) & _MASK32,
            (state[2] + left[3] + right[4]) & _MASK32,
            (state[3] + left[4] + right[0]) & _MASK32,
            (state[4] + left[0] + right[1]) & _MASK32,
            (state[0] + left[1] + right[2]) & _MASK32,
        )
    return b"".join(word.to_bytes(4, "little") for word in state)
