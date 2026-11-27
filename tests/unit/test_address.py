"""Address validation for the watched addresses (T-107, RF-37, CA-22, D-85): the
published BIP173, BIP350 and base58check vectors, and what must be refused."""

import pytest

from xbt_terminal.core.address import AddressError, validate_address

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32, _BECH32M = 1, 0x2BC830A3


def _polymod(values):
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for i, generator in enumerate((0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)):
            if (top >> i) & 1:
                checksum ^= generator
    return checksum


def _segwit(version, program, constant):
    """Encode with the given checksum constant, to build the wrong-variant vectors."""
    bits = "".join(f"{byte:08b}" for byte in program)
    bits += "0" * (-len(bits) % 5)
    data = [version] + [int(bits[i : i + 5], 2) for i in range(0, len(bits), 5)]
    expanded = [ord(char) >> 5 for char in "bc"] + [0] + [ord(char) & 31 for char in "bc"]
    polymod = _polymod(expanded + data + [0] * 6) ^ constant
    data += [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return "bc1" + "".join(_CHARSET[group] for group in data)


VALID = [
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",  # BIP173, P2WPKH
    "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3",  # BIP173, P2WSH
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0",  # BIP350, taproot
    "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",  # P2PKH
    "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",  # P2SH
]

INVALID = {
    "bech32 checksum": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5",
    "testnet": "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx",
    "mixed case": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kV8f3t4",
    "v0 with bech32m": _segwit(0, bytes(20), _BECH32M),
    "v1 with bech32": _segwit(1, bytes(32), _BECH32),
    "base58 checksum": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN3",
    "base58 testnet": "mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn",
    "p2sh checksum": "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLz",
    "base58 character outside the alphabet": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN0",
    "bech32 character outside the alphabet": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3tb",
    "v0 program of 21 bytes": _segwit(0, bytes(21), _BECH32),
    "witness version 17": _segwit(17, bytes(20), _BECH32M),
    "empty": "",
    "free text": "not an address",
}


@pytest.mark.spec("RF-37", "CA-22")
@pytest.mark.parametrize("text", VALID)
def test_valid_mainnet_addresses_are_accepted(text):
    assert validate_address(text) == text


@pytest.mark.spec("RF-37", "CA-22")
def test_uppercase_bech32_is_normalised_to_lowercase():
    upper = "BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4"
    assert validate_address(upper) == upper.lower()


@pytest.mark.spec("RF-37", "CA-22")
def test_surrounding_whitespace_is_ignored():
    assert validate_address("  " + VALID[3] + "\n") == VALID[3]


@pytest.mark.spec("RF-37", "CA-22")
@pytest.mark.parametrize("text", INVALID.values(), ids=INVALID.keys())
def test_malformed_or_foreign_addresses_are_refused(text):
    with pytest.raises(AddressError):
        validate_address(text)


@pytest.mark.spec("RF-37", "RNF-25")
def test_error_message_does_not_carry_the_address():
    bad = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5"
    with pytest.raises(AddressError) as caught:
        validate_address(bad)
    assert bad not in str(caught.value)
