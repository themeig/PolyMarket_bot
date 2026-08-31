"""
ON-CHAIN & GASLESS POSITION MERGER FOR POLYMARKET (YES + NO -> USDC)
Supports Gnosis CTF, NegRisk Adapter, and Polymarket Builder Relayer.
"""

import os
import time
import logging
from typing import Optional, Dict, Any, List
from web3 import Web3

log = logging.getLogger("token_merger")

CONDITIONAL_TOKENS_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER_ADDRESS = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_POLYGON_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

_CTF_ABI = [
    {
        "name": "mergePositions",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    }
]

_NEG_RISK_ABI = [
    {
        "name": "mergePositions",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    }
]


class TokenMerger:
    def __init__(self, private_key: str = "", proxy_wallet: str = "", rpc_url: str = ""):
        self.private_key = private_key or os.getenv("PRIVATE_KEY", "")
        self.proxy_wallet = proxy_wallet or os.getenv("POLY_PROXY_ADDRESS", "0x117dA19a541bA6d89AE52043A67Dbc22572D1de8")
        self.rpc_url = rpc_url or os.getenv("RPC_URL", "https://polygon.drpc.org")
        self.builder_key = os.getenv("POLY_BUILDER_KEY", "01a05867-2dfd-7dda-80f2-bdc604cf4e09")
        self.builder_secret = os.getenv("POLY_BUILDER_SECRET", "pdmxSbY36Da_hORbMaT9qobifWs4YWYuyNFLJbL5Apg=")
        self.builder_passphrase = os.getenv("POLY_BUILDER_PASSPHRASE", "802d71a6bdc6ccee3ff7d5a90930dc870c5c221e88a623919016cae2333f9ee9")
        self.relayer_url = os.getenv("POLY_RELAYER_URL", "https://relayer-v2.polymarket.com")
        self._w3 = None
        self._account = None

    def _ensure_web3(self) -> bool:
        if self._w3 is not None:
            return True
        try:
            from eth_account import Account
            self._w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            if self.private_key:
                pk = self.private_key
                if not pk.startswith("0x") and len(pk) == 64:
                    pk = "0x" + pk
                self._account = Account.from_key(pk)
            return self._w3.is_connected()
        except Exception as e:
            log.warning(f"Web3 initialization error: {e}")
            return False

    def find_mergeable_pairs(self, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Scans positions grouping by exact conditionId to detect matching YES and NO pairs.
        """
        cond_map = {}
        for p in positions:
            cid = p.get("conditionId")
            if not cid:
                continue
            outcome = str(p.get("outcome", "")).upper()
            size = float(p.get("size", 0) or 0)
            if size < 0.1:
                continue
            
            if cid not in cond_map:
                cond_map[cid] = {
                    "yes": 0.0, 
                    "no": 0.0, 
                    "title": p.get("title", f"Mercato {cid[:10]}..."), 
                    "neg_risk": p.get("negativeRisk", True),
                    "tokens": []
                }
            
            if outcome == "YES":
                cond_map[cid]["yes"] += size
            elif outcome == "NO":
                cond_map[cid]["no"] += size
            cond_map[cid]["tokens"].append(p)

        mergeable = []
        for cid, data in cond_map.items():
            common_sets = min(data["yes"], data["no"])
            if common_sets >= 0.5:
                mergeable.append({
                    "condition_id": cid,
                    "market": data["title"],
                    "mergeable_shares": round(common_sets, 2),
                    "expected_usdc": round(common_sets * 1.00, 2),
                    "is_neg_risk": data["neg_risk"],
                    "tokens": data["tokens"]
                })
        return mergeable

    def execute_merge(self, condition_id: str, amount_shares: float, is_neg_risk: bool = True) -> Optional[str]:
        """
        Executes on-chain or gasless relayer merge transaction.
        """
        amount_raw = int(amount_shares * 1e6)
        if amount_raw <= 0:
            return None

        log.info(f"[MERGE] Attempting automated merge of {amount_shares:.2f} pairs for condition {condition_id[:10]}...")

        # 1. Attempt via Builder Relayer (Gasless)
        try:
            from py_builder_relayer_client.client import RelayClient
            from py_builder_relayer_client.models import DepositWalletCall
            from py_builder_signing_sdk.config import BuilderConfig
            from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

            creds = BuilderApiKeyCreds(key=self.builder_key, secret=self.builder_secret, passphrase=self.builder_passphrase)
            client = RelayClient(self.relayer_url, 137, private_key=self.private_key, builder_config=BuilderConfig(local_builder_creds=creds))
            
            w3 = Web3()
            target_addr = NEG_RISK_ADAPTER_ADDRESS if is_neg_risk else CONDITIONAL_TOKENS_ADDRESS
            target_abi = _NEG_RISK_ABI if is_neg_risk else _CTF_ABI
            contract = w3.eth.contract(address=Web3.to_checksum_address(target_addr), abi=target_abi)
            clean_cid = bytes.fromhex(condition_id.replace("0x", ""))
            
            if is_neg_risk:
                calldata = contract.encode_abi('mergePositions', [clean_cid, amount_raw])
            else:
                calldata = contract.encode_abi('mergePositions', [Web3.to_checksum_address(USDC_POLYGON_ADDRESS), bytes(32), clean_cid, [1, 2], amount_raw])

            call = DepositWalletCall(target=Web3.to_checksum_address(target_addr), value="0", data=calldata)
            
            from eth_account import Account
            signer = Account.from_key(self.private_key).address
            nonce_resp = client.get_nonce(signer, "WALLET")
            nonce = nonce_resp.get("nonce") if isinstance(nonce_resp, dict) else getattr(nonce_resp, "nonce", 0)
            deadline = str(int(time.time()) + 3600)

            resp = client.execute_deposit_wallet_batch([call], Web3.to_checksum_address(self.proxy_wallet), str(nonce), deadline)
            tx_hash = getattr(resp, "transaction_hash", None) or getattr(resp, "hash", None) or str(resp)
            log.info(f"[MERGE] Gasless Merge successfully broadcasted via Relayer! Tx: {tx_hash}")
            return tx_hash
        except Exception as re_err:
            log.warning(f"[MERGE] Relayer execution attempt info: {re_err}")

        # 2. Fallback via Direct Contract Call
        if not self._ensure_web3() or not self._account:
            return None

        try:
            contract_addr = NEG_RISK_ADAPTER_ADDRESS if is_neg_risk else CONDITIONAL_TOKENS_ADDRESS
            contract_abi = _NEG_RISK_ABI if is_neg_risk else _CTF_ABI
            contract = self._w3.eth.contract(address=self._w3.to_checksum_address(contract_addr), abi=contract_abi)
            clean_cid = bytes.fromhex(condition_id.replace("0x", ""))
            
            if is_neg_risk:
                tx_func = contract.functions.mergePositions(clean_cid, amount_raw)
            else:
                tx_func = contract.functions.mergePositions(
                    self._w3.to_checksum_address(USDC_POLYGON_ADDRESS),
                    bytes(32),
                    clean_cid,
                    [1, 2],
                    amount_raw
                )

            tx = tx_func.build_transaction({
                'from': self._account.address,
                'nonce': self._w3.eth.get_transaction_count(self._account.address),
                'gas': 350000,
                'gasPrice': self._w3.eth.gas_price
            })

            signed_tx = self._w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self._w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_hex = self._w3.to_hex(tx_hash)
            log.info(f"[MERGE] Direct Merge transaction broadcasted! Tx: {tx_hex}")
            return tx_hex
        except Exception as direct_err:
            log.error(f"[MERGE] Direct merge execution failed: {direct_err}")
            return None
