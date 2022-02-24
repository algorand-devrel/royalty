from webbrowser import get
from algosdk import *
from algosdk.v2client.algod import *
from algosdk.future.transaction import *
from algosdk.atomic_transaction_composer import *
from algosdk.abi import *
from sandbox import get_accounts
from deploy import create_app
from contract import get_approval, get_clear

client = AlgodClient("a" * 64, "http://localhost:4001")


def get_method(i: Interface, name: str) -> Method:
    for m in i.methods:
        if m.name == name:
            return m
    raise Exception("No method with the name {}".format(name))


def main():
    # Get accounts
    accts = get_accounts()
    addr, pk = accts[0]

    # Create application
    app_id = create_app(client, addr, pk, get_approval, get_clear)
    app_addr = logic.get_application_address(app_id)
    print("Created app: {} ({})".format(app_id, app_addr))

    # Create NFT
    sp = client.suggested_params()
    pay_txn = PaymentTxn(addr, sp, app_addr, int(1e8))
    app_txn = ApplicationCallTxn(
        addr, sp, app_id, OnComplete.NoOpOC, app_args=["create"]
    )

    signed = [txn.sign(pk) for txn in assign_group_id([pay_txn, app_txn])]
    txids = [tx.get_txid() for tx in signed]

    client.send_transactions(signed)
    results = [wait_for_confirmation(client, txid, 4) for txid in txids]

    created_nft_id = results[1]["inner-txns"][0]["asset-index"]

    print("Created nft {}".format(created_nft_id))

    # Read in ABI description
    with open("royalty.json") as f:
        iface = Interface.from_json(f.read())

    policy = [
        ("H3ZG43FTOAIYKCL263DSF3V2ZQCFHXGIAWKIGFCNGL2WBBG67YE6FK3MTI", 10),
        ("PJZ7WTR5XLK6XGSWA7TPFKNS4RA2NASZT6SPCUNBXUZCFSXBV2XVOOAMBE", 10),
    ]

    atc = AtomicTransactionComposer()
    signer = AccountTransactionSigner(pk)
    atc.add_method_call(
        app_id,
        get_method(iface, "set_policy"),
        addr,
        sp,
        signer,
        method_args=[created_nft_id, policy, []],
    )
    result = atc.execute(client, 2)
    print(result)


if __name__ == "__main__":
    main()
