from webbrowser import get
from algosdk import *
from algosdk.v2client.algod import *
from algosdk.future.transaction import *
from algosdk.atomic_transaction_composer import *
from algosdk.abi import *
from sandbox import get_accounts
from deploy import create_app, delete_app

import enforcer.contract as enforcer
import marketplace.contract as marketplace


client = AlgodClient("a" * 64, "http://localhost:4001")

# Read in ABI description from enforcer
with open("enforcer/abi.json") as f:
    enforcer_iface = Interface.from_json(f.read())

# Read in ABI description from market
with open("marketplace/abi.json") as f:
    marketplace_iface = Interface.from_json(f.read())


# Utility method til one is provided
def get_method(i: Interface, name: str) -> Method:
    for m in i.methods:
        if m.name == name:
            return m
    raise Exception("No method with the name {}".format(name))


def main():
    # Get accounts
    accts = get_accounts()
    addr, pk = accts[0]

    royalty_addr, _ = accts[1]
    buyer_addr, buyer_pk = accts[2]

    addr_signer = AccountTransactionSigner(pk)
    buyer_signer = AccountTransactionSigner(buyer_pk)

    # Create Royalty Enforcer application
    app_id = create_app(
        client,
        addr,
        pk,
        enforcer.get_approval,
        enforcer.get_clear,
        global_schema=StateSchema(0, 64),
        local_schema=StateSchema(0, 16),
    )
    app_addr = logic.get_application_address(app_id)
    print("Created app: {} ({})".format(app_id, app_addr))

    # Create NFT
    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    atc.add_transaction(
        TransactionWithSigner(
            txn=PaymentTxn(addr, sp, app_addr, int(1e8)), signer=addr_signer
        )
    )
    atc.add_transaction(
        TransactionWithSigner(
            txn=ApplicationCallTxn(addr, sp, app_id, OnComplete.OptInOC),
            signer=addr_signer,
        )
    )
    atc.add_method_call(
        app_id, get_method(enforcer_iface, "create_nft"), addr, sp, addr_signer
    )
    results = atc.execute(client, 2)

    created_nft_id = results.abi_results[0].return_value
    print("Created nft {}".format(created_nft_id))

    print("Calling set_policy method")
    # Set the royalty policy
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(enforcer_iface, "set_policy"),
        addr,
        sp,
        addr_signer,
        method_args=[created_nft_id, royalty_addr, 1000, 0, 0, 0, 0],
    )
    atc.execute(client, 2)

    print("Calling move method to give asa to app creator")
    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    atc.add_transaction(
        TransactionWithSigner(
            txn=AssetTransferTxn(addr, sp, addr, 0, created_nft_id), signer=addr_signer
        )
    )
    atc.add_method_call(
        app_id,
        get_method(enforcer_iface, "move"),
        addr,
        sp,
        addr_signer,
        [created_nft_id, app_addr, addr],
    )
    atc.execute(client, 2)

    print("Creating marketplace app")
    market_app_id = create_app(
        client,
        addr,
        pk,
        marketplace.get_approval,
        marketplace.get_clear,
        global_schema=StateSchema(3, 1),
        local_schema=StateSchema(0, 16),
    )
    market_app_addr = logic.get_application_address(market_app_id)

    print("Created marketplace app: {} ({})".format(market_app_id, market_app_addr))

    selling_amount = int(1e5)

    print("Calling list method on marketplace")
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(enforcer_iface, "offer"),
        addr,
        sp,
        addr_signer,
        [created_nft_id, market_app_addr],
    )
    grp = atc.build_group()

    atc = AtomicTransactionComposer()
    atc.add_method_call(
        market_app_id,
        get_method(marketplace_iface, "list"),
        addr,
        sp,
        addr_signer,
        [created_nft_id, app_id, selling_amount, grp[0]],
    )
    atc.execute(client, 2)

    print("Listed asset for sale")

    print("Calling buy method on marketplace")
    atc = AtomicTransactionComposer()
    txn = TransactionWithSigner(
        txn=PaymentTxn(buyer_addr, sp, market_app_addr, selling_amount),
        signer=buyer_signer,
    )
    atc.add_transaction(
        TransactionWithSigner(
            txn=AssetOptInTxn(buyer_addr, sp, created_nft_id), signer=buyer_signer
        )
    )
    atc.add_method_call(
        market_app_id,
        get_method(marketplace_iface, "buy"),
        buyer_addr,
        sp,
        buyer_signer,
        [created_nft_id, app_id, app_addr, addr, royalty_addr, txn],
    )

    atc.execute(client, 2)
    print("Bought ASA")


if __name__ == "__main__":
    main()
