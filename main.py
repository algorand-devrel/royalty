from algosdk import *
from sandbox import get_accounts
from deploy import create_app
from contract import get_approval, get_clear



client = algod.AlgodClient("a" * 64, "http://localhost:4001")


def main():
    # Get accounts
    accts = get_accounts()
    addr, pk = accts[0]

    # Create application
    app_id = create_app(client, addr, pk, get_approval, get_clear)
    print("Created app: {}".format(app_id))

    # Create NFT
    # Create data structure

    pass


if __name__ == "__main__":
    main()