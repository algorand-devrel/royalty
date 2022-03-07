from webbrowser import get
from algosdk import *
from algosdk.v2client.algod import *
from algosdk.future.transaction import *
from algosdk.atomic_transaction_composer import *
#from algosdk.dryrun_results import DryrunResponse
from algosdk.abi import *
from sandbox import get_accounts
from deploy import create_asa, create_app, delete_app
from contract import get_approval, get_clear

client = AlgodClient("a" * 64, "http://localhost:4001")

# Read in ABI description
with open("royalty.json") as f:
    iface = Interface.from_json(f.read())


def get_method(i: Interface, name: str) -> Method:
    for m in i.methods:
        if m.name == name:
            return m
    raise Exception("No method with the name {}".format(name))


def main():
    # Get accounts
    accts = get_accounts()
    addr, pk = accts[0]

    buyer_addr, buyer_pk = accts[2]

    addr_signer = AccountTransactionSigner(pk)
    buyer_signer = AccountTransactionSigner(buyer_pk)

    # Create FakeUSD
    asset_id = create_asa(client, addr, pk, "FakeUSD", "FUSD", 1_000_000_000_000, 6)
    print("Created ASA: {}".format(asset_id))

    # Buyer OptIn and Receiver FUSD
    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    atc.add_transaction(
        TransactionWithSigner(
            txn=AssetOptInTxn(buyer_addr, sp, asset_id), signer=buyer_signer
        )
    )
    atc.add_transaction(
        TransactionWithSigner(
            txn=AssetTransferTxn(addr, sp, buyer_addr, 100_000_000_000, asset_id), signer=addr_signer
        )
    )
    atc.execute(client, 2)

    # Create application
    app_id = create_app(client, addr, pk, get_approval, get_clear)
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
    atc.add_method_call(app_id, get_method(iface, "create_nft"), addr, sp, addr_signer)
    results = atc.execute(client, 2)

    created_nft_id = results.abi_results[0].return_value
    print("Created nft {}".format(created_nft_id))

    print("Calling move method")
    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    atc.add_transaction(
        TransactionWithSigner(
            txn=AssetTransferTxn(addr, sp, addr, 0, created_nft_id), signer=addr_signer
        )
    )
    atc.add_method_call(
        app_id,
        get_method(iface, "move"),
        addr,
        sp,
        addr_signer,
        [created_nft_id, app_addr, addr],
    )
    atc.execute(client, 2)

    print("Calling set_policy method (Algo)")
    # Set the royalty policy
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(iface, "set_policy"),
        addr,
        sp,
        addr_signer,
        method_args=[created_nft_id, addr, 1000, 0, 0, 0, 0],
    )
    atc.execute(client, 2)

    print("Calling transfer")

    # Perform a transfer using the application
    atc = AtomicTransactionComposer()
    # First opt buyer into asset
    atc.add_transaction(
        TransactionWithSigner(
            txn=AssetTransferTxn(buyer_addr, sp, buyer_addr, 0, created_nft_id),
            signer=buyer_signer,
        )
    )
    # Payment Transaction to cover purchase of NFT
    ptxn = TransactionWithSigner(
        txn=PaymentTxn(buyer_addr, sp, app_addr, int(1e10)),
        signer=buyer_signer,
    )
    # Actual transfer method call
    atc.add_method_call(
        app_id,
        get_method(iface, "transfer"),
        addr,
        sp,
        addr_signer,
        method_args=[created_nft_id, addr, buyer_addr, addr, ptxn, 0],
    )
    atc.execute(client, 2)

    # Reset owner of NFT for ASA demo.

    print("Calling move method")
    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(iface, "move"),
        addr,
        sp,
        addr_signer,
        [created_nft_id, buyer_addr, addr],
    )
    atc.execute(client, 2)

    print("Calling set_policy method (FakeUSD)")
    # Set the royalty policy
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(iface, "set_policy"),
        addr,
        sp,
        addr_signer,
        method_args=[created_nft_id, addr, 1000, asset_id, 0, 0, 0],
    )
    atc.execute(client, 2)

    print("Calling transfer")

    # Perform a transfer using the application
    atc = AtomicTransactionComposer()
    # First opt buyer into asset
    atc.add_transaction(
        TransactionWithSigner(
            txn=AssetTransferTxn(buyer_addr, sp, buyer_addr, 0, created_nft_id),
            signer=buyer_signer,
        )
    )
    # Payment Transaction to cover purchase of NFT
    ptxn = TransactionWithSigner(
        txn=AssetTransferTxn(buyer_addr, sp, app_addr, int(1e10), asset_id),
        signer=buyer_signer,
    )
    # Actual transfer method call
    atc.add_method_call(
        app_id,
        get_method(iface, "transfer"),
        addr,
        sp,
        addr_signer,
        method_args=[created_nft_id, addr, buyer_addr, addr, ptxn, asset_id],
    )
    atc.execute(client, 2)

    # txns = atc.gather_signatures()
    # dr_req = create_dryrun(client, txns)
    # dr_resp = client.dryrun(dr_req)
    # drr = DryrunResponse(dr_resp)
    # for txn in drr.txns:
    #    print(txn.app_trace(spaces=0))

    # Destroy app, we're done with it
    # delete_app(client, app_id, addr, pk)


if __name__ == "__main__":
    main()
