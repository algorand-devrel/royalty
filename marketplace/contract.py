from pyteal import *


app_key = Bytes("app")
asset_key = Bytes("asset")
amount_key = Bytes("amount")

list_selector = MethodSignature("list(asset,application,uint64,appl)void")


@Subroutine(TealType.uint64)
def list_nft():
    asset_id = Txn.assets[Btoi(Txn.application_args[1])]
    app_id = Txn.assets[Btoi(Txn.application_args[2])]
    amount = Btoi(Txn.application_args[3])
    offer_txn = Gtxn[Txn.group_index() - Int(1)]
    asset_balance = AssetHolding.balance(Txn.sender(), asset_id)
    asset_freeze = AssetParam.freeze(asset_id)
    asset_clawback = AssetParam.clawback(asset_id)
    app_addr = AppParam.address(app_id)
    auth_addr = App.localGetEx(Txn.sender(), app_id, Itob(app_id))

    return Seq(
        asset_balance,
        asset_freeze,
        asset_clawback,
        app_addr,
        auth_addr,
        # Check stuff
        Assert(
            And(
                App.globalGet(app_key) == Int(0),  # We don't have anything there yet
                offer_txn.application_id()
                == app_id,  # The app call to trigger offered is present and same as app id
                asset_balance.value() > Int(0),  # The caller has the asset
                asset_freeze == app_addr,
                asset_clawback == app_addr,  # The freeze/clawback are set to app addr
                auth_addr.value()
                == Global.current_application_address(),  # The authorized addr for this asset is this apps account
            )
        ),
        # Set appropriate parameters
        App.globalPut(app_key, app_id),
        App.globalPut(asset_key, asset_id),
        App.globalPut(amount_key, amount),
        Int(1),
    )


buy_selector = MethodSignature("buy(asset,application,account,account,pay)void")


@Subroutine(TealType.uint64)
def buy_nft():
    asset_id = Txn.assets[Btoi(Txn.application_args[1])]
    app_id = Txn.applications[Btoi(Txn.application_args[2])]
    owner_acct = Txn.accounts[Btoi(Txn.application_args[3])]
    royalty_acct = Txn.accounts[Btoi(Txn.application_args[4])]
    pay_txn = Gtxn[Txn.group_index() - Int(1)]

    app_addr = AppParam.address(app_id)

    # Make sure its the asset for sale
    # Make sure the payment is for the right amount
    # Issue inner app call to royalty to move asset
    return Seq(
        app_addr,
        Assert(app_addr.hasValue()),
        Assert(
            And(
                app_id == App.globalGet(app_key),
                asset_id == App.globalGet(asset_key),
                pay_txn.amount() >= App.globalGet(amount_key),
                pay_txn.receiver() == Global.current_application_address(),
            )
        ),
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.Payment,
                TxnField.amount: pay_txn.amount(),
                TxnField.receiver: app_addr.value(),
            }
        ),
        InnerTxnBuilder.Next(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.ApplicationCall,
                TxnField.application_id: app_id,
                TxnField.application_args: [
                    MethodSignature("transfer(asset,account,account,account,txn)void"),
                    Itob(Int(0)),  # Asset in 0th position of asset array
                    Itob(Int(1)),  # Current owner first element here but offset by 1
                    Itob(Int(2)),  # Buyer
                    Itob(Int(3)),  # Who we need to pay for royalties
                ],
                TxnField.assets: [asset_id],
                TxnField.accounts: [owner_acct, Txn.sender(), royalty_acct],
            }
        ),
        InnerTxnBuilder.Submit(),
        # Wipe listing
        App.globalDel(asset_key),
        App.globalDel(amount_key),
        App.globalDel(app_key),
        Int(1),
    )


def approval():
    from_creator = Txn.sender() == Global.creator_address()

    action_router = Cond(
        [Txn.application_args[0] == list_selector, list_nft()],
        # [Txn.application_args[0] == delist_selector, delist_nft()],
        [Txn.application_args[0] == buy_selector, buy_nft()],
    )

    return Cond(
        [Txn.application_id() == Int(0), Approve()],
        [Txn.on_completion() == OnComplete.DeleteApplication, Return(from_creator)],
        [Txn.on_completion() == OnComplete.UpdateApplication, Return(from_creator)],
        [Txn.on_completion() == OnComplete.OptIn, Approve()],
        [Txn.on_completion() == OnComplete.CloseOut, Approve()],
        [Txn.on_completion() == OnComplete.NoOp, Return(action_router)],
    )


def clear():
    return Approve()


def get_approval():
    return compileTeal(approval(), mode=Mode.Application, version=6)


def get_clear():
    return compileTeal(clear(), mode=Mode.Application, version=6)


if __name__ == "__main__":
    print(get_approval())
