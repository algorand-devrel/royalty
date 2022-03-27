import base64
from algosdk import algod
from algosdk.future.transaction import *


def create_app(
    client: algod.AlgodClient,
    addr: str,
    pk: str,
    get_approval,
    get_clear,
    global_schema,
    local_schema,
) -> int:
    # Get suggested params from network
    sp = client.suggested_params()

    # Read in approval teal source && compile
    app_result = client.compile(get_approval())
    app_bytes = base64.b64decode(app_result["result"])

    # Read in clear teal source && compile
    clear_result = client.compile(get_clear())
    clear_bytes = base64.b64decode(clear_result["result"])

    # Create the transaction
    create_txn = ApplicationCreateTxn(
        addr,
        sp,
        0,
        app_bytes,
        clear_bytes,
        global_schema,
        local_schema,
    )

    # Sign it
    signed_txn = create_txn.sign(pk)

    # Ship it
    txid = client.send_transaction(signed_txn)

    # Wait for the result so we can return the app id
    result = wait_for_confirmation(client, txid, 4)
    app_id = result["application-index"]
    app_addr = logic.get_application_address(app_id)

    # Make sure the app address is funded with at least min balance
    ptxn = PaymentTxn(addr, sp, app_addr, int(1e6))
    txid = client.send_transaction(ptxn.sign(pk))
    wait_for_confirmation(client, txid, 4)

    return app_id, app_addr


def update_app(
    client: algod.AlgodClient, app_id: int, addr: str, pk: str, get_approval, get_clear
) -> int:
    # Get suggested params from network
    sp = client.suggested_params()

    # Read in approval teal source && compile
    app_result = client.compile(get_approval())
    app_bytes = base64.b64decode(app_result["result"])

    # Read in clear teal source && compile
    clear_result = client.compile(get_clear())
    clear_bytes = base64.b64decode(clear_result["result"])

    # Create the transaction
    create_txn = ApplicationUpdateTxn(
        addr,
        sp,
        app_id,
        app_bytes,
        clear_bytes,
    )

    # Sign it
    signed_txn = create_txn.sign(pk)

    # Ship it
    txid = client.send_transaction(signed_txn)
    wait_for_confirmation(client, txid, 4)


def delete_app(client: algod.AlgodClient, app_id: int, addr: str, pk: str):
    # Get suggested params from network
    sp = client.suggested_params()

    # Create the transaction
    txn = ApplicationDeleteTxn(addr, sp, app_id)

    # sign it
    signed = txn.sign(pk)

    # Ship it
    txid = client.send_transaction(signed)

    return wait_for_confirmation(client, txid, 4)


def destroy_apps(client: algod.AlgodClient, addr: str, pk: str):
    acct = client.account_info(addr)

    # Delete all apps created by this account
    for app in acct["created-apps"]:
        delete_app(client, app["id"], addr, pk)
