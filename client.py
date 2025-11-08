import requests

BASE_URL = "http://127.0.0.1:8080"
TIMEOUT = 60


def create_deal(buyer="Alice-1", seller="Bob-1", item="Phone", price=200.0):
    url = f"{BASE_URL}/create_deal"
    payload = {
        "buyer": buyer,
        "seller": seller,
        "item": item,
        "price": price,
    }
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    print("== create_deal ==")
    print("status:", r.status_code)
    try:
        print("response:", r.json())
    except Exception:
        print("raw:", r.text)
    print()
    return r


def deals_for(party):
    url = f"{BASE_URL}/deals/{party}"
    r = requests.get(url, timeout=TIMEOUT)
    print(f"== deals_for({party}) ==")
    print("status:", r.status_code)
    print(r.json())
    print()
    return r


def buyer_confirm(buyer="Alice-1"):
    url = f"{BASE_URL}/buyer_confirm"
    payload = {"buyer": buyer}
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    print("== buyer_confirm ==")
    print("status:", r.status_code)
    print("response:", r.json())
    print()
    return r


def seller_confirm(seller="Bob-1"):
    url = f"{BASE_URL}/seller_confirm"
    payload = {"seller": seller}
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    print("== seller_confirm ==")
    print("status:", r.status_code)
    print("response:", r.json())
    print()
    return r


def release(agent="Escrow-1"):
    url = f"{BASE_URL}/release"
    payload = {"agent": agent}
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    print("== release ==")
    print("status:", r.status_code)
    print("response:", r.json())
    print()
    return r


def refund(agent="Escrow-1"):
    url = f"{BASE_URL}/refund"
    payload = {"agent": agent}
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    print("== refund ==")
    print("status:", r.status_code)
    print("response:", r.json())
    print()
    return r


def cash(party):
    url = f"{BASE_URL}/cash/{party}"
    r = requests.get(url, timeout=TIMEOUT)
    print(f"== cash({party}) ==")
    print("status:", r.status_code)
    print("response:", r.json())
    print()
    return r


def escrow_for(party):
    url = f"{BASE_URL}/escrow/{party}"
    r = requests.get(url, timeout=TIMEOUT)
    print(f"== escrow({party}) ==")
    print("status:", r.status_code)
    print("response:", r.json())
    print()
    return r


def deal_summary():
    url = f"{BASE_URL}/deal_summary"
    r = requests.get(url, timeout=TIMEOUT)
    print("== deal_summary ==")
    print("status:", r.status_code)
    try:
        print("response:", r.json())
    except Exception:
        print("raw:", r.text)
    print()
    return r


def main():
    # 1. Check API status
    print("== status ==")
    s = requests.get(f"{BASE_URL}/status", timeout=5)
    print("status:", s.status_code, "body:", s.json())
    print()

    # 2. Create a new deal between buyer and seller
    create_deal(buyer="Alice-1", seller="Bob-1", item="Phone", price=200.0)

    # 3. View current Escrow state from buyer's perspective
    escrow_for("Alice-1")

    # 4. Buyer confirms the deal
    buyer_confirm("Alice-1")

    # 5. Seller confirms the deal
    seller_confirm("Bob-1")

    # 6. Escrow agent releases the funds
    release("Escrow-1")

    # 7. Display updated cash balances
    cash("Alice-1")
    cash("Bob-1")
    cash("Escrow-1")

    # 8. Display final deal summary
    deal_summary()

    # Optional: list all deals for each participant
    deals_for("Alice-1")
    deals_for("Bob-1")


if __name__ == "__main__":
    main()
