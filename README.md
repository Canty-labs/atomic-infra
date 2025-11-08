# Canty – Infrastructure Built for Atomic Precision

This repository contains the **Canty Validator Infrastructure** —  
a full end-to-end escrow workflow running on **Canton**, designed for **atomic precision** and **synchronized reliability** across distributed systems.  
Optionally, it supports on-chain settlement mirroring to **Ethereum (Sepolia)**.

---

## 🏗️ Overview
The system demonstrates how multi-party agreements and asset transfers  
can be coordinated atomically using **Canton smart contracts** and external systems.

### It includes:
- 💠 **DAML Contracts** — Escrow, Token, and Parties templates  
- 🧠 **Flask Backend** — REST API layer for validator orchestration  
- ⚙️ **Canton Configuration** — `canton.conf` for local participant + domain setup  
- 🧪 **Demo Client** — test runner for end-to-end workflow validation  

---

## 🔧 Quick Start

```bash
# 1. Build the DAML package
daml build

# 2. Start Canton
canton -c canton.conf

# 3. Run the Flask validator service
python app.py

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
├── app.py              # Flask + logic
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


