"""Native segwit derivation from an account xpub (T-101, RF-37, CA-22, D-84): the
published BIP32, BIP84 and BIP173 vectors, and the keys that must not load."""

import hashlib

import pytest

from xbt_terminal.core.wallet import (
    CHANGE,
    RECEIVE,
    WalletError,
    address,
    hash160,
    p2wpkh_address,
    parse_account_key,
    ripemd160,
)

# BIP84 test vector: mnemonic "abandon ... about", account m/84'/0'/0'
BIP84_ZPUB = (
    "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868Rv"
    "UUkgDKf31mGDtKsAYz2oz2AGutZYs"
)
BIP84_RECEIVE_0 = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
BIP84_RECEIVE_1 = "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g"
BIP84_CHANGE_0 = "bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el"
BIP84_RECEIVE_0_PUBLIC_KEY = "0330d54fd0dd420a6e5f8d3624f5f3482cae350f79d5f0753bf5beef9c2d91af3c"

# BIP32 test vector 1: m/0' and its non-hardened child m/0'/1
BIP32_PARENT = (
    "xpub68Gmy5EdvgibQVfPdqkBBCHxA5htiqg55crXYuXoQRKfDBFA1WEjWgP6LHhwBZeNK1VTsfTFUHCdrf"
    "p1bgwQ9xv5ski8PX9rL2dZXvgGDnw"
)
BIP32_CHILD_1 = (
    "xpub6ASuArnXKPbfEwhqN6e3mwBcDTgzisQN1wXN9BJcM47sSikHjJf3UFHKkNAWbWMiGj7Wf5uMash7Sy"
    "Yq527Hqck2AxYysAA7xmALppuCkwQ"
)

# BIP173 example: P2WPKH of the generator point's compressed public key
BIP173_PUBLIC_KEY = "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"
BIP173_ADDRESS = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"

BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def base58check(payload: bytes) -> str:
    data = payload + hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    number = int.from_bytes(data, "big")
    text = ""
    while number:
        number, digit = divmod(number, 58)
        text = BASE58[digit] + text
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + text


def base58_payload(text: str) -> bytes:
    number = 0
    for char in text:
        number = number * 58 + BASE58.index(char)
    return number.to_bytes(82, "big")[:-4]


def with_version(key: str, version: int) -> str:
    """The same extended key under another version prefix (ypub, Zpub, ...)."""
    return base58check(version.to_bytes(4, "big") + base58_payload(key)[4:])


@pytest.mark.spec("RF-37", "CA-22")
class TestBip84Vector:
    def test_receive_and_change_addresses(self):
        key = parse_account_key(BIP84_ZPUB)
        assert address(key, RECEIVE, 0) == BIP84_RECEIVE_0
        assert address(key, RECEIVE, 1) == BIP84_RECEIVE_1
        assert address(key, CHANGE, 0) == BIP84_CHANGE_0

    def test_child_public_key(self):
        child = parse_account_key(BIP84_ZPUB).child(RECEIVE).child(0)
        assert child.public_key.hex() == BIP84_RECEIVE_0_PUBLIC_KEY

    def test_same_account_as_xpub_gives_the_same_native_segwit_addresses(self):
        as_xpub = with_version(BIP84_ZPUB, 0x0488B21E)
        assert as_xpub.startswith("xpub")
        assert address(parse_account_key(as_xpub), RECEIVE, 0) == BIP84_RECEIVE_0

    def test_every_address_is_native_segwit(self):
        key = parse_account_key(BIP84_ZPUB)
        for branch in (RECEIVE, CHANGE):
            for i in range(5):
                derived = address(key, branch, i)
                assert derived.startswith("bc1q") and len(derived) == 42


@pytest.mark.spec("RF-37", "CA-22")
class TestBip32Vector:
    def test_public_child_derivation(self):
        child = parse_account_key(BIP32_PARENT).child(1)
        expected = parse_account_key(BIP32_CHILD_1)
        assert child.public_key == expected.public_key
        assert child.chain_code == expected.chain_code

    def test_hardened_child_is_refused(self):
        with pytest.raises(WalletError):
            parse_account_key(BIP32_PARENT).child(0x80000000)

    def test_negative_index_is_refused(self):
        with pytest.raises(WalletError):
            parse_account_key(BIP32_PARENT).child(-1)


@pytest.mark.spec("RF-37")
class TestAddressEncoding:
    def test_bip173_p2wpkh(self):
        assert p2wpkh_address(bytes.fromhex(BIP173_PUBLIC_KEY)) == BIP173_ADDRESS

    def test_other_branches_are_refused(self):
        with pytest.raises(WalletError):
            address(parse_account_key(BIP84_ZPUB), 2, 0)


@pytest.mark.spec("RF-37")
class TestRipemd160:
    @pytest.mark.parametrize(
        ("message", "digest"),
        [
            (b"", "9c1185a5c5e9fc54612808977ee8f548b2258d31"),
            (b"abc", "8eb208f7e05d987a9b044a8e98c6b087f15a0bfc"),
            (b"message digest", "5d0689ef49d2fae572b881b123a85ffa21595f36"),
            (b"a" * 1_000, None),
        ],
    )
    def test_own_implementation(self, message, digest):
        if digest is not None:
            assert ripemd160(message).hex() == digest
        try:
            reference = hashlib.new("ripemd160", message).digest()
        except ValueError:  # OpenSSL 3 without the legacy provider
            return
        assert ripemd160(message) == reference

    def test_hash160_of_the_bip173_key(self):
        assert hash160(bytes.fromhex(BIP173_PUBLIC_KEY)).hex() == (
            "751e76e8199196d454941c45d1b3a323f1433bd6"
        )


@pytest.mark.spec("RF-37", "CA-22")
class TestRefusedKeys:
    @pytest.mark.parametrize(
        "version",
        [
            0x049D7CB2,  # ypub: nested segwit
            0x0295B43F,  # Ypub: nested segwit multisig
            0x02AA7ED3,  # Zpub: native segwit multisig
            0x043587CF,  # tpub: testnet
            0x045F1CF6,  # vpub: testnet
        ],
    )
    def test_other_versions(self, version):
        with pytest.raises(WalletError):
            parse_account_key(with_version(BIP84_ZPUB, version))

    @pytest.mark.parametrize("version", [0x0488ADE4, 0x04B2430C])  # xprv, zprv
    def test_private_keys(self, version):
        payload = base58_payload(BIP84_ZPUB)
        private = version.to_bytes(4, "big") + payload[4:45] + b"\0" + payload[46:]
        with pytest.raises(WalletError):
            parse_account_key(base58check(private))

    def test_bad_checksum(self):
        broken = BIP84_ZPUB[:-1] + ("a" if BIP84_ZPUB[-1] != "a" else "b")
        with pytest.raises(WalletError):
            parse_account_key(broken)

    @pytest.mark.parametrize("text", ["", "zpub", "not a key 0OIl", BIP84_RECEIVE_0])
    def test_malformed(self, text):
        with pytest.raises(WalletError):
            parse_account_key(text)

    def test_point_not_on_the_curve(self):
        payload = base58_payload(BIP84_ZPUB)
        off_curve = payload[:45] + b"\x02" + (5).to_bytes(32, "big")
        with pytest.raises(WalletError):
            parse_account_key(base58check(off_curve))

    def test_error_never_shows_the_key(self):
        broken = BIP84_ZPUB[:-1] + ("a" if BIP84_ZPUB[-1] != "a" else "b")
        with pytest.raises(WalletError) as raised:
            parse_account_key(broken)
        assert broken not in str(raised.value) and broken[:12] not in str(raised.value)

    @pytest.mark.spec("RNF-25")
    def test_key_does_not_print(self):
        key = parse_account_key(BIP84_ZPUB)
        rendered = repr(key) + str(key) + f"{key}"
        assert key.public_key.hex() not in rendered and repr(key.public_key) not in rendered

    def test_surrounding_spaces_are_ignored(self):
        assert parse_account_key(f"  {BIP84_ZPUB}\n") == parse_account_key(BIP84_ZPUB)
