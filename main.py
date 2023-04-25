from algosdk import dryrun_results
from algosdk.v2client.algod import *
from algosdk.transaction import *
from algosdk.atomic_transaction_composer import *
from algosdk.abi import *
from sandbox import get_accounts
from deploy import create_app

import enforcer.contract as enforcer
import marketplace.contract as marketplace


client = AlgodClient("a" * 64, "http://localhost:4001")

ZERO_ADDR = encoding.encode_address(bytes(32))

# Get the contracts from the routers we've constructed
enforcer_contract = enforcer.get_contract()
marketplace_contract = marketplace.get_contract()


# Utility method til one is provided
def get_method(c: Contract, name: str) -> Method:
    for m in c.methods:
        if m.name == name:
            return m
    raise Exception("No method with the name {}".format(name))


def dryrun(atc: AtomicTransactionComposer, client: AlgodClient):
    dr_req = create_dryrun(client, atc.gather_signatures())
    dr_resp = client.dryrun(dr_req)
    dr_result = dryrun_results.DryrunResponse(dr_resp)
    for txn in dr_result.txns:
        if txn.app_call_rejected():
            print(txn.app_trace(dryrun_results.StackPrinterConfig(max_value_width=0)))


def get_algo_balance(addr: str):
    ai = client.account_info(addr)
    return ai["amount"]


def main():
    # Get accounts
    accts = get_accounts()

    move_amount = 1
    offered_amount = 1
    price = int(2e6)

    addr, pk = accts[0]
    royalty_addr, _ = accts[1]
    buyer_addr, buyer_pk = accts[2]

    addr_signer = AccountTransactionSigner(pk)
    buyer_signer = AccountTransactionSigner(buyer_pk)

    #################
    # Create Royalty Enforcer application
    #################

    app_id, app_addr = create_app(
        client,
        addr,
        pk,
        enforcer.get_approval(),
        enforcer.get_clear(),
        global_schema=StateSchema(1, 2),
        local_schema=StateSchema(0, 16),
    )
    print("Created royalty-enforcment app {} ({})".format(app_id, app_addr))

    #################
    # Create NFT
    #################

    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    atc.add_transaction(
        TransactionWithSigner(
            txn=AssetCreateTxn(
                addr,
                sp,
                1,
                0,
                True,
                manager=app_addr,
                freeze=app_addr,
                clawback=app_addr,
                unit_name="ra-ex",
                asset_name="Royalty Asset",
            ),
            signer=addr_signer,
        )
    )
    result = atc.execute(client, 2)
    pti = client.pending_transaction_info(result.tx_ids[0])
    created_nft_id = pti["asset-index"]
    print("Created nft {}".format(created_nft_id))

    #################
    # App creator opt into app (since we use it later as the owner)
    #################

    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    atc.add_transaction(
        TransactionWithSigner(
            txn=ApplicationCallTxn(addr, sp, app_id, OnComplete.OptInOC),
            signer=addr_signer,
        )
    )
    atc.execute(client, 2)

    #################
    # Set the administrator (then revert)
    #################
    print("Calling get_administrator")
    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(enforcer_contract, "get_administrator"),
        addr,
        sp,
        addr_signer,
    )
    # TODO: really should just dryrun this
    results = atc.execute(client, 2)
    print("Current admin: {}".format(results.abi_results[0].return_value))

    print("Calling set_administrator method to set to a new address")
    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(enforcer_contract, "set_administrator"),
        addr,
        sp,
        addr_signer,
        method_args=[buyer_addr],
    )
    atc.execute(client, 2)

    print("Calling get_administrator")
    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(enforcer_contract, "get_administrator"),
        addr,
        sp,
        addr_signer,
    )
    # TODO: really should just dryrun this
    results = atc.execute(client, 2)
    print("Current admin: {}".format(results.abi_results[0].return_value))

    print("Calling set_administrator method to set back to original creator")
    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(enforcer_contract, "set_administrator"),
        buyer_addr,
        sp,
        buyer_signer,
        method_args=[addr],
    )
    atc.execute(client, 2)

    #################
    # Set the royalty policy
    #################

    print("Calling set_policy method")
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(enforcer_contract, "set_policy"),
        addr,
        sp,
        addr_signer,
        method_args=[1000, royalty_addr],
    )
    atc.execute(client, 2)

    #################
    # Get policy from global state of app
    #################

    print("Calling get_policy method")
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(enforcer_contract, "get_policy"),
        addr,
        sp,
        addr_signer,
    )
    results = atc.execute(client, 2)
    print("Got: {}".format(results.abi_results[0].return_value))

    print("Getting the policy from global state")
    app_info = client.application_info(app_id)
    state = app_info["params"]["global-state"]
    print("Policy for app id: {}".format(app_id))
    for sv in state:
        k = base64.b64decode(sv["key"]).decode("utf8")
        type = sv["value"]["type"]
        val = sv["value"]["uint"]
        if type == 1:
            val = encoding.encode_address(base64.b64decode(sv["value"]["bytes"]))
        print("\t{}: {}".format(k, val))

    #################
    # Create Marketplace Application
    #################

    print("Creating marketplace app")
    market_app_id, market_app_addr = create_app(
        client,
        addr,
        pk,
        marketplace.get_approval(),
        marketplace.get_clear(),
        global_schema=StateSchema(4, 1),
        local_schema=StateSchema(0, 16),
    )
    print("Created marketplace app: {} ({})".format(market_app_id, market_app_addr))

    #################
    # Get balances after contract setup and before list/sale
    #################

    owner_balance = get_algo_balance(addr)
    buyer_balance = get_algo_balance(buyer_addr)
    royalty_receiver_balance = get_algo_balance(royalty_addr)

    #################
    # List NFT for sale on marketplace
    #################

    print("Calling list method on marketplace")

    # We construct an ATC with no intention of submitting it as is
    # we're just using it to parse/marshal in the app args
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(enforcer_contract, "offer"),
        addr,
        sp,
        addr_signer,
        [created_nft_id, offered_amount, market_app_addr, 0, ZERO_ADDR],
    )
    grp = atc.build_group()

    # Construct the list app call, passing the offer app call built previously
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        market_app_id,
        get_method(marketplace_contract, "list"),
        addr,
        sp,
        addr_signer,
        [created_nft_id, app_id, offered_amount, price, grp[0]],
    )

    atc.execute(client, 2)
    print("Listed asset for sale")

    #################
    # Get offered details
    #################
    print("Calling get_offer method")
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id,
        get_method(enforcer_contract, "get_offer"),
        addr,
        sp,
        addr_signer,
        method_args=[created_nft_id, addr],
    )
    results = atc.execute(client, 2)
    print("Got: {}".format(results.abi_results[0].return_value))

    print("Getting offer directly from local state")
    aai = client.account_application_info(addr, app_id)
    local_state = aai["app-local-state"]["key-value"]
    for lsv in local_state:
        aid = int.from_bytes(base64.b64decode(lsv["key"]), "big")
        offer = base64.b64decode(lsv["value"]["bytes"])
        auth = encoding.encode_address(offer[:32])
        amt = int.from_bytes(offer[32:], "big")
        print("Offer from {}".format(addr))
        print("\tASA id: {}".format(aid))
        print("\tAuth addr: {}".format(auth))
        print("\tAmount: {}".format(amt))

    #################
    # Buyer calls buy method
    #################

    print("Calling buy method on marketplace")
    atc = AtomicTransactionComposer()
    txn = TransactionWithSigner(
        txn=PaymentTxn(buyer_addr, sp, market_app_addr, price),
        signer=buyer_signer,
    )
    atc.add_transaction(
        TransactionWithSigner(
            txn=AssetOptInTxn(buyer_addr, sp, created_nft_id), signer=buyer_signer
        )
    )
    atc.add_method_call(
        market_app_id,
        get_method(marketplace_contract, "buy"),
        buyer_addr,
        sp,
        buyer_signer,
        [created_nft_id, app_id, app_addr, addr, royalty_addr, offered_amount, txn],
    )
    atc.execute(client, 2)
    print("Bought ASA")

    new_owner_balance = get_algo_balance(addr)
    new_buyer_balance = get_algo_balance(buyer_addr)
    new_royalty_receiver_balance = get_algo_balance(royalty_addr)

    print()
    # Balances should all match up
    print("Owner Balance Delta: {}".format(new_owner_balance - owner_balance))
    print(
        "Royalty Receiver Balance Delta: {}".format(
            new_royalty_receiver_balance - royalty_receiver_balance
        )
    )
    print("Buyer Balance Delta: {}".format(new_buyer_balance - buyer_balance))

    #################
    # Move NFT to back to app creator
    #################

    print()
    print("Calling move method to give asa to app creator")
    sp = client.suggested_params()
    atc = AtomicTransactionComposer()
    # Opt in
    atc.add_transaction(
        TransactionWithSigner(
            txn=ApplicationOptInTxn(buyer_addr, sp, app_id), signer=buyer_signer
        )
    )
    # Offer
    atc.add_method_call(
        app_id,
        get_method(enforcer_contract, "offer"),
        buyer_addr,
        sp,
        buyer_signer,
        method_args=[created_nft_id, offered_amount, addr, 0, ZERO_ADDR],
    )
    # Move
    atc.add_method_call(
        app_id,
        get_method(enforcer_contract, "royalty_free_move"),
        addr,
        sp,
        addr_signer,
        [created_nft_id, move_amount, buyer_addr, addr, offered_amount],
    )
    atc.execute(client, 2)
    print("Original creator now owns the asset again")


if __name__ == "__main__":
    main()
