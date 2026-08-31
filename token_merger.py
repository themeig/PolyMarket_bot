"""
ON-CHAIN POSITION MERGER FOR POLYMARKET (YES + NO -> USDC)
Uses exact conditionId grouping and robust Polygon RPC connection.
"""

import os
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
        Executes on-chain mergePositions transaction.
        """
        if not self._ensure_web3() or not self._account:
            log.warning("Cannot execute merge without Web3 and Private Key configured.")
            return None

        try:
            amount_raw = int(amount_shares * 1e6)
            if amount_raw <= 0:
                return None

            log.info(f"[MERGE] Attempting to merge {amount_shares:.2f} pairs for condition {condition_id[:10]}...")
            
            contract_addr = NEG_RISK_ADAPTER_ADDRESS if is_neg_risk else CONDITIONAL_TOKENS_ADDRESS
            contract_abi = _NEG_RISK_ABI if is_neg_risk else _CTF_ABI
            contract = self._w3.eth.contract(address=self._w3.to_checksum_address(contract_addr), abi=contract_abi)
            
            clean_cid = bytes.fromhex(condition_id.replace("0x", ""))
            
            if is_neg_risk:
                tx_func = contract.functions.mergePositions(clean_cid, amount_raw)
            else:
                tx_func = contract.functions.mergePositions(
                    self._w3.to_checksum_address(USDC_POLYGON_ADDRESS),
                    b'\x00' * 32,
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
            log.info(f"[MERGE] Merge transaction broadcasted successfully! Tx: {tx_hex}")
            return tx_hex
        except Exception as e:
            log.error(f"[MERGE] Merge execution failed: {e}")
            return None
