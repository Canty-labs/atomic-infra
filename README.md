# Canty - Infrastructure Built for Atomic Precision

This repository contains the **Canty Validator** setup —  
a full end-to-end escrow workflow running on **Canton**, bridged optionally to **Ethereum (Sepolia)** for on-chain settlement mirroring.

## 🏗️ Overview
The system demonstrates how cross-ledger deals can be coordinated between **Canton smart contracts** and **Ethereum contracts** (StablecoinEscrow + MockUSDT).

It includes:
- 💠 **DAML Contracts** — Escrow, Token, Parties templates
- 🧠 **Flask Backend** — REST API bridge between Canton and Ethereum
- ⚙️ **Canton Configuration** — `canton.conf` for local participant + domain setup
- 🧪 **Demo Client** — simple test runner for end-to-end flow validation

---

## 🔧 Quick Start

```bash
# 1. Build the DAML package
daml build

# 2. Start Canton
canton -c canton.conf

# 3. Run the Flask bridge
python app.py
Optional (if using Ethereum bridge):

export ETH_RPC_URL="https://sepolia.infura.io/v3/..."
export ETH_BROKER_PRIVATE_KEY="your_private_key"
## Project Structure
bash
Copy code
├── daml/
│   ├── Escrow.daml
│   ├── Token.daml
│   ├── Parties.daml
│   └── Demo.daml
├── app.py              # Flask + bridge logic
├── client.py           # Test client for API endpoints
├── canton.conf         # Canton participant/domain config
├── requirements.txt
└── README.md
## API Endpoints
Some useful routes exposed by the Flask app:

Method	Endpoint	Description
GET	/status	JSON API health check
POST	/create_deal	Create a new escrow deal
POST	/buyer_confirm	Buyer confirms the deal
POST	/seller_confirm	Seller confirms the deal
POST	/release	Agent releases funds
GET	/deals/<party>	Query all active deals for a party

🛠️ Tech Stack
Canton (Digital Asset)

DAML smart contracts

Flask + Python 3.10+

Web3.py (Ethereum integration)

Sepolia Testnet

📜 License
Private / Internal – © 2025 Canty Labs.


