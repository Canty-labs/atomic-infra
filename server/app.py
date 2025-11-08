import os
import json
import base64
import subprocess
from pathlib import Path
from web3 import Web3
import threading
import time

import requests
from flask import Flask, request, jsonify, render_template

# =====================================
# CONFIG
# =====================================

# Canton JSON API (participant1)
API_URL = os.environ.get("DAML_API_URL", "http://localhost:7576/v1")
BASE_URL = API_URL.replace("/v1", "")

APPLICATION_ID = os.environ.get("DAML_APP_ID", "flask-app")
LEDGER_ID = os.environ.get("DAML_LEDGER_ID", "participant1")  # Logical ledger id

# Participant1 Ledger gRPC endpoint (used by daml CLI list-parties)
LEDGER_HOST = os.environ.get("LEDGER_HOST", "localhost")
LEDGER_PORT = int(os.environ.get("LEDGER_PORT", "5011"))

# Location of the daml CLI
DAML_CMD = os.environ.get("DAML_CMD")
if not DAML_CMD:
    appdata = os.environ.get("APPDATA", "")
    cand1 = os.path.join(appdata, "daml", "bin", "daml.exe")
    cand2 = os.path.join(appdata, "daml", "bin", "daml.cmd")
    if os.path.exists(cand1):
        DAML_CMD = cand1
    elif os.path.exists(cand2):
        DAML_CMD = cand2
    else:
        DAML_CMD = "daml"
print("[i] Using daml command:", DAML_CMD)

app = Flask(__name__)

# Cache for Party identifiers by alias ("Alice-1", "Bob-1", etc.)
_party_cache: dict[str, str] = {}

# Cache for main packageId of the DAR
_pkg_cache: str | None = None
_proj_root_cache: Path | None = None

# Mapping between Ethereum dealId and Canton Escrow contractId
# dealId_hex -> escrow_cid
deal_map: dict[str, str] = {}

# =====================================
# ETHEREUM / SEPOLIA CONFIG (Bridge side)
# =====================================

# Sepolia RPC URL (should be overridden via ENV in real deployments)
ETH_RPC_URL = os.environ.get(
    "ETH_RPC_URL",
    "https://sepolia.infura.io/v3/2adae55a65ab40019b6cb85dfab18d73",  # demo placeholder
)

# Addresses of the deployed contracts (from Remix)
SEPOLIA_ESCROW_ADDRESS = os.environ.get(
    "SEPOLIA_ESCROW_ADDRESS",
    "0x2d0DD2a577b5a204824702908D1B891584576946",  # StablecoinEscrow
)
SEPOLIA_TOKEN_ADDRESS = os.environ.get(
    "SEPOLIA_TOKEN_ADDRESS", "0x337188FF8fE6BcC7316cC5c4f8202196777a4E64"  # MockUSDT
)

# Token decimals for the demo stablecoin
TOKEN_DECIMALS = 6  # mUSDT

# Initialize Web3
w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))

try:
    SEPOLIA_ESCROW_ADDRESS = Web3.to_checksum_address(SEPOLIA_ESCROW_ADDRESS)
    SEPOLIA_TOKEN_ADDRESS = Web3.to_checksum_address(SEPOLIA_TOKEN_ADDRESS)
except Exception as e:
    print("[eth] bad address format:", e)

ETH_BROKER_PRIVATE_KEY = os.environ.get("ETH_BROKER_PRIVATE_KEY")

eth_broker_account = None
if ETH_BROKER_PRIVATE_KEY:
    try:
        eth_broker_account = w3.eth.account.from_key(ETH_BROKER_PRIVATE_KEY)
        print("[eth] broker address:", eth_broker_account.address)
    except Exception as e:
        print("[eth] failed to load broker account:", e)
else:
    print("[eth] no ETH_BROKER_PRIVATE_KEY env var found")


# Minimal ABI for StablecoinEscrow: only the parts we actually use
ESCROW_ABI = [
    # events
    {
        "anonymous": False,
        "inputs": [
            {
                "indexed": True,
                "internalType": "bytes32",
                "name": "dealId",
                "type": "bytes32",
            },
            {
                "indexed": True,
                "internalType": "address",
                "name": "buyer",
                "type": "address",
            },
            {
                "indexed": True,
                "internalType": "address",
                "name": "seller",
                "type": "address",
            },
            {
                "indexed": False,
                "internalType": "uint256",
                "name": "amount",
                "type": "uint256",
            },
        ],
        "name": "DealCreated",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {
                "indexed": True,
                "internalType": "bytes32",
                "name": "dealId",
                "type": "bytes32",
            },
            {
                "indexed": True,
                "internalType": "address",
                "name": "buyer",
                "type": "address",
            },
            {
                "indexed": False,
                "internalType": "uint256",
                "name": "amount",
                "type": "uint256",
            },
        ],
        "name": "Deposited",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {
                "indexed": True,
                "internalType": "bytes32",
                "name": "dealId",
                "type": "bytes32",
            },
            {
                "indexed": True,
                "internalType": "address",
                "name": "seller",
                "type": "address",
            },
            {
                "indexed": False,
                "internalType": "uint256",
                "name": "amount",
                "type": "uint256",
            },
        ],
        "name": "Released",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {
                "indexed": True,
                "internalType": "bytes32",
                "name": "dealId",
                "type": "bytes32",
            },
            {
                "indexed": True,
                "internalType": "address",
                "name": "buyer",
                "type": "address",
            },
            {
                "indexed": False,
                "internalType": "uint256",
                "name": "amount",
                "type": "uint256",
            },
        ],
        "name": "Refunded",
        "type": "event",
    },
    # functions – signatures must match the on-chain contract
    {
        "inputs": [
            {"internalType": "bytes32", "name": "dealId", "type": "bytes32"},
            {"internalType": "address", "name": "buyer", "type": "address"},
            {"internalType": "address", "name": "seller", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "createDeal",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "dealId", "type": "bytes32"},
        ],
        "name": "deposit",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "dealId", "type": "bytes32"},
        ],
        "name": "release",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "dealId", "type": "bytes32"},
        ],
        "name": "refund",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "dealId", "type": "bytes32"},
        ],
        "name": "deals",
        "outputs": [
            {"internalType": "address", "name": "buyer", "type": "address"},
            {"internalType": "address", "name": "seller", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
            {"internalType": "bool", "name": "deposited", "type": "bool"},
            {"internalType": "bool", "name": "releasedOrRefunded", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

# Minimal ERC20 ABI (MockUSDT)
TOKEN_ABI = [
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# Contract instances
escrow_contract = w3.eth.contract(address=SEPOLIA_ESCROW_ADDRESS, abi=ESCROW_ABI)
token_contract = w3.eth.contract(address=SEPOLIA_TOKEN_ADDRESS, abi=TOKEN_ABI)


def bridge_create_eth_deal_from_canton(
    escrow_cid: str, buyer_eth: str, seller_eth: str, price: float
):
    """
    Create a StablecoinEscrow deal on Ethereum for a given Canton Escrow.

    escrow_cid  - Canton Escrow contractId
    buyer_eth   - buyer Ethereum address
    seller_eth  - seller Ethereum address
    price       - human-readable price (e.g. 1.5), converted to token units
    """
    if not w3.is_connected():
        raise RuntimeError("web3 not connected")
    if not eth_broker_account:
        raise RuntimeError("ETH_BROKER_PRIVATE_KEY not configured")

    # Use a hash of the Canton contractId as a deterministic dealId
    deal_id_bytes = Web3.keccak(text=escrow_cid)
    deal_id_hex = "0x" + deal_id_bytes.hex()

    buyer = Web3.to_checksum_address(buyer_eth)
    seller = Web3.to_checksum_address(seller_eth)

    # Convert to token units (decimals=6)
    amount = int(float(price) * (10**TOKEN_DECIMALS))

    fn = escrow_contract.functions.createDeal(
        deal_id_bytes,
        buyer,
        seller,
        amount,
    )

    tx_hash = send_tx(fn)  # returns hex string (without 0x)

    # Store mapping between Ethereum dealId and Canton Escrow
    if deal_id_hex and escrow_cid:
        deal_map[deal_id_hex] = escrow_cid

    return {
        "dealId": deal_id_hex,
        "buyer": buyer,
        "seller": seller,
        "amount": amount,
        "tx_hash": (
            f"0x{tx_hash}" if tx_hash and not tx_hash.startswith("0x") else tx_hash
        ),
    }


SEPOLIA_CHAIN_ID = 11155111  # Sepolia chain id


def send_tx(fn):
    """
    Send a signed Ethereum transaction with basic nonce/gas handling.
    Returns the transaction hash as a hex string.
    """
    if not eth_broker_account:
        raise RuntimeError("ETH_BROKER_PRIVATE_KEY not configured")

    from_addr = eth_broker_account.address

    # Use pending nonce to avoid collisions
    nonce = w3.eth.get_transaction_count(from_addr, "pending")

    # Slightly bump gas price (20%) over the current network value
    base_gas_price = w3.eth.gas_price
    gas_price = int(base_gas_price * 1.2)

    tx = fn.build_transaction(
        {
            "from": from_addr,
            "nonce": nonce,
            "gas": 400000,
            "gasPrice": gas_price,
            "chainId": SEPOLIA_CHAIN_ID,
        }
    )

    signed = w3.eth.account.sign_transaction(tx, private_key=ETH_BROKER_PRIVATE_KEY)

    try:
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hash_hex = tx_hash.hex()
        print(f"[eth] sent: {tx_hash_hex}")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        print(f"[eth] mined in block {receipt.blockNumber}")

        return tx_hash_hex

    except Exception as e:
        print(f"[eth] send_tx error: {e}")
        return None


def bridge_release_eth_from_canton(escrow_cid: str):
    """
    After the Escrow is released on Canton, trigger the corresponding
    release(dealId) on the Ethereum StablecoinEscrow contract.
    """
    if not w3 or not w3.is_connected():
        print("[bridge] web3 not connected, skip eth release")
        return {"error": "web3 not connected"}

    if not eth_broker_account:
        print("[bridge] no ETH_BROKER_PRIVATE_KEY, skip eth release")
        return {"error": "no broker key"}

    # Same deterministic dealId derivation as in bridge_create_eth_deal_from_canton
    deal_id_bytes = Web3.keccak(text=escrow_cid)
    deal_id_hex = "0x" + deal_id_bytes.hex()

    # Check current state of the deal
    try:
        buyer, seller, amount, deposited, done = escrow_contract.functions.deals(
            deal_id_bytes
        ).call()

        if not deposited:
            print(f"[bridge] deal {deal_id_hex} not deposited yet, skip eth release")
            return {"dealId": deal_id_hex, "skipped": "not deposited"}

        if done:
            print(
                f"[bridge] deal {deal_id_hex} already released/refunded, skip eth release"
            )
            return {"dealId": deal_id_hex, "skipped": "already done"}

    except Exception as e:
        print(f"[bridge] deals({deal_id_hex}) call failed: {e}")

    # If we reach here we can attempt release(dealId)
    try:
        fn = escrow_contract.functions.release(deal_id_bytes)
        tx_hash = send_tx(fn)
        print(f"[bridge] eth release sent for {deal_id_hex}: {tx_hash}")
        return {"dealId": deal_id_hex, "tx_hash": tx_hash}
    except Exception as e:
        print(f"[bridge] eth release failed for {deal_id_hex}: {e}")
        return {"dealId": deal_id_hex, "error": str(e)}


# =====================================
# Helpers: project root + packageId
# =====================================


def find_project_root() -> Path:
    """Locate the directory that contains daml.yaml."""
    global _proj_root_cache
    if _proj_root_cache:
        return _proj_root_cache

    for start in [Path.cwd(), Path(__file__).resolve().parent]:
        p = start
        seen = set()
        while p not in seen:
            seen.add(p)
            if (p / "daml.yaml").exists():
                _proj_root_cache = p
                return p
            if p.parent == p:
                break
            p = p.parent

    _proj_root_cache = Path.cwd()
    return _proj_root_cache


def latest_dar_path() -> Path:
    """Return the most recently built DAR under .daml/dist, building if needed."""
    root = find_project_root()
    dist = root / ".daml" / "dist"
    dist.mkdir(parents=True, exist_ok=True)

    dars = sorted(dist.glob("*.dar"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not dars:
        subprocess.run("daml build", cwd=str(root), shell=True, check=True)
        dars = sorted(dist.glob("*.dar"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not dars:
        raise RuntimeError(f"No DAR found under {dist}. Run `daml build`.")
    return dars[0]


def get_package_id() -> str:
    """Use damlc inspect-dar to obtain the main packageId of the DAR."""
    global _pkg_cache
    if _pkg_cache:
        return _pkg_cache

    dar = latest_dar_path()
    cmd = [DAML_CMD, "damlc", "inspect-dar", str(dar), "--json"]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    info = json.loads(res.stdout)
    pkg = info.get("mainPackageId") or info.get("main_package_id")
    if not pkg:
        raise RuntimeError("Failed to extract main package id from inspect-dar.")
    _pkg_cache = pkg
    print(f"[i] Using packageId: {pkg} ({dar.name})")
    return pkg


def tid(module_entity: str) -> str:
    """Turn 'Module:Entity' into a fully-qualified TemplateId <pkg>:Module:Entity."""
    if ":" not in module_entity:
        raise ValueError("module_entity must be 'Module:Entity'")
    pkg = get_package_id()
    mod, ent = module_entity.split(":", 1)
    return f"{pkg}:{mod}:{ent}"


# =====================================
# JWT + HTTP helper
# =====================================


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_jwt(act_as=None, read_as=None) -> str:
    """Create a JWT (alg=none) with optional actAs/readAs claims."""
    header = {"alg": "none"}
    payload = {
        "sub": "demo",
        "https://daml.com/ledger-api": {
            "ledgerId": LEDGER_ID,
            "applicationId": APPLICATION_ID,
        },
    }
    la = payload["https://daml.com/ledger-api"]
    if act_as:
        la["actAs"] = act_as
    if read_as:
        la["readAs"] = read_as

    return (
        f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(payload).encode())}."
    )


def http_post(path: str, payload: dict, token: str | None = None):
    url = f"{API_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(url, json=payload, headers=headers, timeout=20)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    return r.status_code, data


# =====================================
# Party cache (populated from /v1/parties)
# =====================================


def refresh_party_cache_from_ledger():
    """
    Fetch the party list from the JSON API (/v1/parties) and build a mapping:
      "Alice-1"                -> "Alice-1::1220..."
      "Alice-1::1220...2393"   -> "Alice-1::1220...2393"
    """
    global _party_cache
    try:
        url = f"{API_URL}/parties"
        token = make_jwt()  # JWT without actAs/readAs
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        body = r.json()

        result = body.get("result", [])
        cache: dict[str, str] = {}

        for row in result:
            ident = row.get("identifier") or row.get("party")
            if not ident:
                continue

            # Always map the full identifier
            cache[ident] = ident

            # Also map shortened alias without namespace, e.g. "Alice-1"
            short = ident.split("::", 1)[0]
            cache[short] = ident

        _party_cache = cache
        print("[i] Party cache refreshed from JSON API:", _party_cache)

    except Exception as e:
        print("[i] Could not refresh party cache from JSON API:", e)


def get_party_id(name: str) -> str:
    """
    Resolve a human name (e.g. 'Alice-1') to a full Party identifier.

    If name is already a full Party id (contains '::'), it is returned as-is.
    If not found in the cache, we fall back to the original name (demo mode).
    """
    if "::" in name or name.startswith("party-"):
        return name

    if not _party_cache:
        refresh_party_cache_from_ledger()

    if name in _party_cache:
        return _party_cache[name]

    print(f"WARNING: party '{name}' not found in cache, using as-is")
    return name


# =====================================
# JSON API wrappers: query / create / exercise / fetch
# =====================================


def query(template_ids, read_as: str):
    """Call /v1/query with readAs permissions."""
    party = get_party_id(read_as)
    token = make_jwt(read_as=[party])
    payload = {"templateIds": template_ids, "query": {}}
    return http_post("/query", payload, token=token)


def create(template_id: str, payload: dict, act_as_party: str):
    party = get_party_id(act_as_party)
    token = make_jwt(act_as=[party])
    body = {"templateId": template_id, "payload": payload}
    return http_post("/create", body, token=token)


def exercise(
    template_id: str, contract_id: str, act_as_party: str, choice: str, argument=None
):
    party = get_party_id(act_as_party)
    token = make_jwt(act_as=[party])
    body = {
        "templateId": template_id,
        "contractId": contract_id,
        "choice": choice,
        "argument": argument or {},
    }
    return http_post("/exercise", body, token=token)


def fetch(template_id: str, contract_id: str, read_as: str):
    """Call /v1/fetch to retrieve a single contract by contractId."""
    party = get_party_id(read_as)
    token = make_jwt(read_as=[party])
    body = {"templateId": template_id, "contractId": contract_id}
    return http_post("/fetch", body, token=token)


def settle_canton_escrow(escrow_cid: str):
    """
    Perform the full Canton Escrow settlement flow for a given contract:
    BuyerConfirm -> SellerConfirm -> ReleaseToSeller.
    """
    print(f"[canton] settling escrow {escrow_cid}")

    # 1) BuyerConfirm on the Escrow
    c1, d1 = exercise(tid("Escrow:Escrow"), escrow_cid, "Alice-1", "BuyerConfirm")
    if c1 != 200:
        print("[canton] BuyerConfirm failed:", d1)
        return

    # 2) Find the Pending contract and call SellerConfirm
    c2, d2 = query([tid("Escrow:Pending")], read_as="Bob-1")
    if c2 == 200 and d2.get("result"):
        pending_cid = d2["result"][0]["contractId"]
        c2x, d2x = exercise(
            tid("Escrow:Pending"), pending_cid, "Bob-1", "SellerConfirm"
        )
        if c2x != 200:
            print("[canton] SellerConfirm failed:", d2x)
            return
    else:
        print("[canton] no Pending found for escrow settlement")
        return

    # 3) Find Ready and call ReleaseToSeller, then bridge to Ethereum
    c3, d3 = query([tid("Escrow:Ready")], read_as="Escrow-1")
    if c3 == 200 and d3.get("result"):
        ready_cid = d3["result"][0]["contractId"]
        c3x, d3x = exercise(
            tid("Escrow:Ready"), ready_cid, "Escrow-1", "ReleaseToSeller"
        )
        if c3x == 200:
            print("[canton] escrow released successfully:", d3x)

            # Reverse direction: after Canton release, trigger Ethereum release
            try:
                eth_release = bridge_release_eth_from_canton(escrow_cid)
                print("[bridge] eth release result:", eth_release)
            except Exception as e:
                print("[bridge] eth release error:", e)

        else:
            print("[canton] ReleaseToSeller failed:", d3x)
    else:
        print("[canton] no Ready found for escrow settlement")


def eth_deposit_watcher():
    """
    Watch Deposited events on the StablecoinEscrow contract.
    For each matching dealId with a known mapping, trigger settle_canton_escrow.
    """
    if not w3 or not w3.is_connected():
        print("[eth-watch] web3 not connected, watcher not started")
        return

    try:
        # Event name must match the contract definition: Deposited
        event_klass = escrow_contract.events.Deposited
    except AttributeError:
        print("[eth-watch] Contract has no event 'Deposited'. Check ABI / event name.")
        return

    try:
        current_block = w3.eth.block_number
        deposit_filter = event_klass.create_filter(from_block=current_block)
    except Exception as e:
        print("[eth-watch] could not create filter:", e)
        return

    print(f"[eth-watch] watching Deposited events from block {current_block}...")

    while True:
        try:
            for ev in deposit_filter.get_new_entries():
                args = ev["args"]
                deal_id_bytes = args["dealId"]
                deal_id_hex = "0x" + deal_id_bytes.hex()
                buyer = args.get("buyer")
                amount = args.get("amount")

                print(
                    f"[eth-watch] Deposited: dealId={deal_id_hex}, buyer={buyer}, amount={amount}"
                )

                escrow_cid = deal_map.get(deal_id_hex)
                if not escrow_cid:
                    print("[eth-watch] no escrow_cid mapping for that dealId, skipping")
                    continue

                # Trigger the Canton settlement flow
                settle_canton_escrow(escrow_cid)

        except Exception as e:
            print("[eth-watch] error in loop:", e)

        time.sleep(5)


def start_eth_deposit_watcher():
    """Run the Ethereum watcher in a background daemon thread."""
    t = threading.Thread(target=eth_deposit_watcher, daemon=True)
    t.start()
    print("[eth-watch] watcher thread started")


# =====================================
# /status – basic health checks
# =====================================


@app.get("/status")
def status():
    try:
        r = requests.get(f"{BASE_URL}/readyz", timeout=5)
        ok = r.status_code == 200
        return {
            "ok": ok,
            "api": API_URL,
            "base_url": BASE_URL,
            "body": r.text.strip(),
        }, (200 if ok else 503)
    except Exception as e:
        return {"ok": False, "api": API_URL, "error": str(e)}, 503


@app.get("/eth/status")
def eth_status():
    ok = w3.is_connected()
    info = {
        "connected": ok,
        "rpc_url": ETH_RPC_URL,
        "escrow_address": SEPOLIA_ESCROW_ADDRESS,
        "token_address": SEPOLIA_TOKEN_ADDRESS,
    }
    if not ok:
        info["error"] = "Web3 not connected"
        return info, 503

    try:
        broker = escrow_contract.functions.broker().call()
        symbol = token_contract.functions.symbol().call()
        decimals = token_contract.functions.decimals().call()
    except Exception as e:
        info["error"] = f"contract call failed: {e}"
        return info, 500

    info.update(
        {
            "broker": broker,
            "token_symbol": symbol,
            "token_decimals": decimals,
        }
    )
    return info, 200


@app.get("/eth/test_create_deal")
def eth_test_create_deal():
    """
    Simple test: create a StablecoinEscrow deal on Ethereum.
    buyer & seller = the broker address (for demo only).
    amount = 1 mUSDT (1,000,000 units, decimals=6).
    """
    if not w3.is_connected():
        return {"error": "web3 not connected"}, 503
    if not eth_broker_account:
        return {"error": "ETH_BROKER_PRIVATE_KEY not set"}, 500

    deal_id_bytes = os.urandom(32)
    deal_id_hex = "0x" + deal_id_bytes.hex()

    buyer = eth_broker_account.address
    seller = eth_broker_account.address
    amount = 1_000_000  # 1 mUSDT

    try:
        fn = escrow_contract.functions.createDeal(
            deal_id_bytes,
            buyer,
            seller,
            amount,
        )
        tx_hash = send_tx(fn)
        return {
            "dealId": deal_id_hex,
            "buyer": buyer,
            "seller": seller,
            "amount": amount,
            "tx_hash": tx_hash,
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


# =====================================
# Simple read-only endpoints
# =====================================


@app.get("/cash/<party>")
def cash(party):
    code, data = query([tid("Token:Cash")], read_as=party)
    return jsonify(data), code


@app.get("/escrow/<party>")
def list_escrow(party):
    code, data = query([tid("Escrow:Escrow")], read_as=party)
    return jsonify(data), code


@app.get("/pending/<party>")
def list_pending(party):
    code, data = query([tid("Escrow:Pending")], read_as=party)
    return jsonify(data), code


@app.get("/ready/<party>")
def list_ready(party):
    code, data = query([tid("Escrow:Ready")], read_as=party)
    return jsonify(data), code


@app.get("/completed/<party>")
def list_completed(party):
    code, data = query([tid("Escrow:Completed")], read_as=party)
    return jsonify(data), code


# =====================================
# Example deal summary
# =====================================


@app.get("/deal_summary")
def deal_summary():
    code, data = query([tid("Escrow:Escrow")], read_as="Alice-1")
    if code != 200 or not data.get("result"):
        return jsonify({"error": "no deals"}), 404

    esc = data["result"][0]
    p = esc["payload"]
    return {
        "buyer": p["buyer"],
        "seller": p["seller"],
        "item": p["item"],
        "price": p["price"],
        "agent": p["agent"],
        "lockedCashCid": p["locked"],
        "contractId": esc["contractId"],
    }, 200


# =====================================
# Business flow endpoints
# =====================================


@app.post("/offer_create")
def offer_create():
    """
    Create a new Escrow:Offer on Canton – an open offer that is not
    yet materialized into an Escrow contract or an Ethereum deal.
    body: {
      "buyer": "Alice-1",
      "seller": "Bob-1",
      "cc_amount": 100,
      "unit_price": 0.16,
      "buyer_eth": "0x...",
      "seller_eth": "0x..."
    }
    """
    body = request.json or {}

    buyer_name = body.get("buyer", "Alice-1")
    seller_name = body.get("seller", "Bob-1")

    cc_amount = float(body.get("cc_amount", 100.0))
    unit_price = float(body.get("unit_price", 0.16))
    total_price = cc_amount * unit_price

    buyer_eth = body.get("buyer_eth")
    seller_eth = body.get("seller_eth")

    if not buyer_eth or not seller_eth:
        return {"error": "buyer_eth and seller_eth are required"}, 400

    agent = get_party_id("Escrow-1")
    buyer = get_party_id(buyer_name)
    seller = get_party_id(seller_name)

    offer_payload = {
        "agent": agent,
        "buyer": buyer,
        "seller": seller,
        "ccAmount": str(cc_amount),
        "unitPrice": str(unit_price),
        "totalPrice": str(total_price),
        "buyerEth": buyer_eth,
        "sellerEth": seller_eth,
    }

    c, r = create(tid("Escrow:Offer"), offer_payload, act_as_party="Escrow-1")
    print("[offer_create] status =", c)
    print("[offer_create] response =", r)
    return jsonify({"step": "offer_created", "offer": r}), c


@app.get("/offers/<seller>")
def list_offers_for_party(seller: str):
    """
    List all active Escrow:Offer contracts for a given seller.
    Uses /v1/query which returns only active (non-archived) contracts.
    """
    seller_pid = get_party_id(seller)

    code, data = query([tid("Escrow:Offer")], read_as="Escrow-1")
    if code != 200:
        return jsonify(data), code

    offers = []

    for c in data.get("result", []):
        pld = c["payload"]

        if pld.get("seller") != seller_pid:
            continue

        offers.append(
            {
                "contractId": c["contractId"],
                "buyer": pld.get("buyer"),
                "seller": pld.get("seller"),
                "ccAmount": pld.get("ccAmount"),
                "unitPrice": pld.get("unitPrice"),
                "totalPrice": pld.get("totalPrice"),
                "buyerEth": pld.get("buyerEth"),
                "sellerEth": pld.get("sellerEth"),
            }
        )

    return jsonify({"seller": seller, "sellerId": seller_pid, "offers": offers}), 200


@app.post("/offer_accept")
def offer_accept():
    """
    Seller accepts an Offer:
      1) Fetch Escrow:Offer from the ledger
      2) Archive it (treated as acceptance)
      3) Create Cash + Escrow on Canton
      4) Create a parallel StablecoinEscrow deal on Ethereum
    """
    body = request.json or {}
    offer_cid = body.get("offer_cid")
    if not offer_cid:
        return {"error": "offer_cid is required"}, 400

    # 1) fetch Offer
    c_fetch, r_fetch = fetch(tid("Escrow:Offer"), offer_cid, read_as="Escrow-1")
    if c_fetch != 200 or "result" not in r_fetch:
        return {
            "step": "fetch_offer",
            "response": r_fetch,
            "error": "Offer not found",
        }, 404

    offer = r_fetch["result"]
    pld = offer["payload"]

    agent = pld["agent"]
    buyer_pid = pld["buyer"]
    seller_pid = pld["seller"]
    cc_amount = float(pld["ccAmount"])
    unit_price = float(pld["unitPrice"])
    total_price = float(pld["totalPrice"])
    buyer_eth = pld["buyerEth"]
    seller_eth = pld["sellerEth"]

    # 2) Archive the offer using the built-in Archive choice, via Escrow-1 (signatory)
    c_accept, r_accept = exercise(
        tid("Escrow:Offer"),
        offer_cid,
        act_as_party="Escrow-1",
        choice="Archive",
    )
    if c_accept != 200:
        return {
            "step": "archive_offer",
            "offer": r_fetch,
            "archive": r_accept,
        }, c_accept

    # 3) Create the Cash + Escrow flow (similar to /create_deal)

    buyer = buyer_pid
    seller = seller_pid
    bank = get_party_id("Bank-1")
    agent_pid = get_party_id("Escrow-1")

    # 3a) Bank issues Cash for the buyer
    cash_payload = {
        "issuer": bank,
        "owner": buyer,
        "currency": "USD",
        "amount": total_price,
    }
    c1, r1 = create(tid("Token:Cash"), cash_payload, act_as_party="Bank-1")
    if c1 != 200:
        return (
            jsonify(
                {
                    "step": "create_cash",
                    "offer": r_fetch,
                    "accept": r_accept,
                    "response": r1,
                }
            ),
            c1,
        )

    cash_cid = r1["result"]["contractId"]

    # 3b) Buyer transfers funds to the escrow agent (Escrow-1)
    c2, r2 = exercise(
        tid("Token:Cash"),
        cash_cid,
        act_as_party=buyer,
        choice="Transfer",
        argument={"newOwner": agent_pid},
    )
    if c2 != 200:
        return (
            jsonify(
                {
                    "step": "lock_cash",
                    "offer": r_fetch,
                    "accept": r_accept,
                    "response": r2,
                }
            ),
            c2,
        )

    locked_cid = r2["result"]["exerciseResult"]

    # 3c) Agent creates the Escrow with locked Cash
    item_desc = f"CantonCoin (CC) x {cc_amount} @ {unit_price} USDT"
    escrow_payload = {
        "agent": agent_pid,
        "buyer": buyer,
        "seller": seller,
        "item": item_desc,
        "price": total_price,
        "locked": locked_cid,
    }
    c3, r3 = create(tid("Escrow:Escrow"), escrow_payload, act_as_party="Escrow-1")

    eth_bridge = None
    if c3 == 200 and buyer_eth and seller_eth:
        try:
            escrow_cid = r3["result"]["contractId"]
            eth_bridge = bridge_create_eth_deal_from_canton(
                escrow_cid, buyer_eth, seller_eth, total_price
            )
        except Exception as e:
            eth_bridge = {"error": str(e)}

    return (
        jsonify(
            {
                "step": "offer_accepted",
                "offer": r_fetch,
                "accept": r_accept,
                "create_cash": r1,
                "lock_cash": r2,
                "escrow": r3,
                "eth_bridge": eth_bridge,
            }
        ),
        c3,
    )


@app.post("/offer_reject")
def offer_reject():
    """
    Seller rejects an Offer:
    - simply archives the Escrow:Offer contract
    - no Escrow is created and no Ethereum interaction happens.
    """
    body = request.json or {}
    offer_cid = body.get("offer_cid")

    if not offer_cid:
        return {"error": "missing offer_cid"}, 400

    print("[offer_reject] cid =", offer_cid)

    code, data = exercise(
        tid("Escrow:Offer"),
        offer_cid,
        act_as_party="Escrow-1",
        choice="Archive",
    )

    print("[offer_reject] status =", code)
    print("[offer_reject] response =", data)

    # If the contract is already inactive, treat this as success for demo purposes
    if code == 404 and "CONTRACT_NOT_ACTIVE" in str(data):
        print("[offer_reject] contract already inactive, treating as success")
        return (
            jsonify(
                {
                    "step": "offer_rejected",
                    "archive": data,
                    "alreadyConsumed": True,
                }
            ),
            200,
        )

    if code != 200:
        return (
            jsonify(
                {
                    "step": "offer_rejected",
                    "archive": data,
                    "error": True,
                }
            ),
            code,
        )

    return (
        jsonify(
            {
                "step": "offer_rejected",
                "archive": data,
                "alreadyConsumed": False,
            }
        ),
        200,
    )


@app.post("/create_deal")
def create_deal():
    """
    Create a new Escrow deal on Canton,
    and optionally mirror it as a StablecoinEscrow deal on Ethereum.
    body: {
      "buyer": "Alice-1",
      "seller": "Bob-1",
      "item": "Laptop",
      "price": 100.0,
      "buyer_eth": "0x....",   # optional
      "seller_eth": "0x...."   # optional
    }
    """
    body = request.json or {}
    buyer_name = body.get("buyer", "Alice-1")
    seller_name = body.get("seller", "Bob-1")
    item = body.get("item", "Laptop")
    price = float(body.get("price", 100.0))

    buyer_eth = body.get("buyer_eth")
    seller_eth = body.get("seller_eth")

    buyer = get_party_id(buyer_name)
    seller = get_party_id(seller_name)
    bank = get_party_id("Bank-1")
    agent = get_party_id("Escrow-1")

    # 1) Bank issues Cash for the buyer
    cash_payload = {"issuer": bank, "owner": buyer, "currency": "USD", "amount": price}
    c1, r1 = create(tid("Token:Cash"), cash_payload, act_as_party="Bank-1")
    if c1 != 200:
        return jsonify({"step": "create cash", "response": r1}), c1

    cash_cid = r1["result"]["contractId"]

    # 2) Buyer transfers funds to the escrow agent (Escrow-1)
    c2, r2 = exercise(
        tid("Token:Cash"), cash_cid, buyer_name, "Transfer", {"newOwner": agent}
    )
    if c2 != 200:
        return jsonify({"step": "lock cash", "response": r2}), c2

    locked_cid = r2["result"]["exerciseResult"]

    # 3) Agent creates the Escrow with locked Cash
    escrow_payload = {
        "agent": agent,
        "buyer": buyer,
        "seller": seller,
        "item": item,
        "price": price,
        "locked": locked_cid,
    }
    c3, r3 = create(tid("Escrow:Escrow"), escrow_payload, act_as_party="Escrow-1")

    eth_bridge = None
    if c3 == 200 and buyer_eth and seller_eth:
        try:
            escrow_cid = r3["result"]["contractId"]
            eth_bridge = bridge_create_eth_deal_from_canton(
                escrow_cid,
                buyer_eth,
                seller_eth,
                price,
            )
        except Exception as e:
            eth_bridge = {"error": str(e)}

    return (
        jsonify(
            {
                "step": "escrow created",
                "create_cash": r1,
                "lock_cash": r2,
                "escrow": r3,
                "eth_bridge": eth_bridge,
            }
        ),
        c3,
    )


@app.post("/buyer_confirm")
def buyer_confirm():
    buyer = (request.json or {}).get("buyer", "Alice-1")
    c, d = query([tid("Escrow:Escrow")], read_as=buyer)
    if c != 200:
        return jsonify(d), c
    items = d.get("result", [])
    if not items:
        return {"error": "No Escrow contracts found for buyer"}, 404
    cid = items[0]["contractId"]
    c2, r2 = exercise(tid("Escrow:Escrow"), cid, buyer, "BuyerConfirm")
    return jsonify(r2), c2


@app.post("/seller_confirm")
def seller_confirm():
    seller = (request.json or {}).get("seller", "Bob-1")
    c, d = query([tid("Escrow:Pending")], read_as=seller)
    if c != 200:
        return jsonify(d), c
    items = d.get("result", [])
    if not items:
        return {"error": "No Pending contracts found for seller"}, 404
    cid = items[0]["contractId"]
    c2, r2 = exercise(tid("Escrow:Pending"), cid, seller, "SellerConfirm")
    return jsonify(r2), c2


@app.post("/release")
def release():
    agent = (request.json or {}).get("agent", "Escrow-1")
    c, d = query([tid("Escrow:Ready")], read_as=agent)
    if c != 200:
        return jsonify(d), c
    items = d.get("result", [])
    if not items:
        return {"error": "No Ready contracts found for agent"}, 404
    cid = items[0]["contractId"]
    c2, r2 = exercise(tid("Escrow:Ready"), cid, agent, "ReleaseToSeller")
    return jsonify(r2), c2


@app.post("/refund")
def refund():
    agent = (request.json or {}).get("agent", "Escrow-1")
    c, d = query([tid("Escrow:Ready")], read_as=agent)
    if c != 200:
        return jsonify(d), c
    items = d.get("result", [])
    if not items:
        return {"error": "No Ready contracts found for agent"}, 404
    cid = items[0]["contractId"]
    c2, r2 = exercise(tid("Escrow:Ready"), cid, agent, "RefundToBuyer")
    return jsonify(r2), c2


@app.post("/flow")
def flow():
    """
    Convenience endpoint that executes the full Escrow flow
    (BuyerConfirm -> SellerConfirm -> ReleaseToSeller)
    for the first matching contracts it finds.
    """
    out = {}

    c1, d1 = query([tid("Escrow:Escrow")], read_as="Alice-1")
    out["escrow_query"] = d1
    if c1 == 200 and d1.get("result"):
        cid = d1["result"][0]["contractId"]
        c1x, d1x = exercise(tid("Escrow:Escrow"), cid, "Alice-1", "BuyerConfirm")
        out["buyer_confirm"] = d1x

    c2, d2 = query([tid("Escrow:Pending")], read_as="Bob-1")
    out["pending_query"] = d2
    if c2 == 200 and d2.get("result"):
        cid = d2["result"][0]["contractId"]
        c2x, d2x = exercise(tid("Escrow:Pending"), cid, "Bob-1", "SellerConfirm")
        out["seller_confirm"] = d2x

    c3, d3 = query([tid("Escrow:Ready")], read_as="Escrow-1")
    out["ready_query"] = d3
    if c3 == 200 and d3.get("result"):
        cid = d3["result"][0]["contractId"]
        c3x, d3x = exercise(tid("Escrow:Ready"), cid, "Escrow-1", "ReleaseToSeller")
        out["release"] = d3x

    return jsonify(out), 200


@app.get("/deals/<party>")
def deals_for_party(party):
    """
    Return a unified list of deals for a given party,
    including the current status (Escrow / Pending / Ready)
    and the party's role (buyer / seller / agent).
    """
    party_id = get_party_id(party)

    esc_code, esc_data = query([tid("Escrow:Escrow")], read_as="Escrow-1")
    pend_code, pend_data = query([tid("Escrow:Pending")], read_as="Escrow-1")
    ready_code, ready_data = query([tid("Escrow:Ready")], read_as="Escrow-1")

    for code, data in [
        (esc_code, esc_data),
        (pend_code, pend_data),
        (ready_code, ready_data),
    ]:
        if code not in (200, 404):
            return jsonify({"error": "ledger query failed", "details": data}), code

    def mk_list(data, status):
        deals = []
        for c in data.get("result", []):
            pld = c["payload"]
            role = None
            if pld.get("buyer") == party_id:
                role = "buyer"
            elif pld.get("seller") == party_id:
                role = "seller"
            elif pld.get("agent") == party_id:
                role = "agent"
            if role is None:
                continue
            deals.append(
                {
                    "contractId": c["contractId"],
                    "status": status,
                    "role": role,
                    "item": pld.get("item"),
                    "price": pld.get("price"),
                    "buyer": pld.get("buyer"),
                    "seller": pld.get("seller"),
                    "agent": pld.get("agent"),
                }
            )
        return deals

    all_deals = []
    all_deals += mk_list(esc_data, "Escrow")
    all_deals += mk_list(pend_data, "Pending")
    all_deals += mk_list(ready_data, "Ready")

    return jsonify({"party": party, "partyId": party_id, "deals": all_deals}), 200


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    print(f"[i] JSON API: {API_URL}")
    print(f"[i] Project root: {find_project_root()}")
    print("[i] Refreshing Canton party map from ledger...")
    refresh_party_cache_from_ledger()
    print("[i] Party cache loaded.")

    # Start Ethereum deposit watcher if Web3 is available
    if w3 and w3.is_connected():
        print("[eth] Web3 connected, starting deposit watcher...")
        start_eth_deposit_watcher()
    else:
        print("[eth] Web3 NOT connected, watcher will not start.")

    print("[i] Starting Flask...")
    app.run(host="0.0.0.0", port=8080, debug=True)
